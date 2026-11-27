"""Explicit recurring maintenance. Reminders never create work or cost bookings."""
import calendar
from datetime import date, timedelta
from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import Entry, MaintenanceCompletion, MaintenanceRule
from .operations import guarded
from .rules import Conflict, audit, check_owner


class MaintenanceForm(forms.ModelForm):
    version = forms.IntegerField(widget=forms.HiddenInput,required=False)
    class Meta:
        model = MaintenanceRule
        fields = ['title','interval_days','interval_months','interval_km','meter_unit','base_date','base_meter','active']
        labels = {'title':'Wartung','interval_months':'Intervall in Monaten','interval_km':'Intervall in km','base_date':'Zuletzt erledigt am / Bezugsdatum','base_meter':'Zähler bei letzter Wartung (km)','active':'Aktiv'}
        labels.update(interval_days='Intervall in Tagen',interval_km='Zählerintervall',meter_unit='Zählereinheit',base_meter='Zähler bei letzter Wartung')
        help_texts = {'base_date':'Pflicht, sobald ein Zeitintervall (Tage oder Monate) angegeben ist.','base_meter':'Pflicht, sobald ein Zählerintervall angegeben ist.'}
        widgets = {'base_date':forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'})}
        localized_fields = ['interval_km','base_meter']
    def clean(self):
        data=super().clean()
        months,km=data.get('interval_months'),data.get('interval_km')
        days=data.get('interval_days')
        if days is not None and not 1<=days<=36500:self.add_error('interval_days','Zwischen 1 und 36500 Tagen wählen.')
        if months is None and km is None and days is None:
            raise ValidationError('Ein Zeit- und/oder Kilometerintervall angeben.')
        if months is not None and not 1<=months<=600:
            self.add_error('interval_months','Zwischen 1 und 600 Monaten wählen.')
        if km is not None and km<=0:
            self.add_error('interval_km','Das Kilometerintervall muss positiv sein.')
        if (months is not None or days is not None) and not data.get('base_date'):
            self.add_error('base_date','Für das Zeitintervall fehlt das Bezugsdatum.')
        if km is not None and data.get('base_meter') is None:
            self.add_error('base_meter','Für das km-Intervall fehlt der Zählerstand.')
        if data.get('base_meter') is not None and data['base_meter']<0:
            self.add_error('base_meter','Zählerstand darf nicht negativ sein.')
        return data


def next_due(rule):
    due_date=None
    if rule.interval_months and rule.base_date:
        index=rule.base_date.year*12+rule.base_date.month-1+rule.interval_months
        year,month=divmod(index,12);month+=1
        if year>9999:
            raise ValidationError('Nächster Termin liegt außerhalb des gültigen Datumsbereichs.')
        due_date=date(year,month,min(rule.base_date.day,calendar.monthrange(year,month)[1]))
    if rule.interval_days and rule.base_date:
        try:due_date=(due_date or rule.base_date)+timedelta(days=rule.interval_days)
        except OverflowError:raise ValidationError('Wartungstermin außerhalb des gültigen Datumsbereichs.') from None
    due_meter=rule.base_meter+rule.interval_km if rule.interval_km is not None and rule.base_meter is not None else None
    return due_date,due_meter


def rule_state(rule,*,today=None):
    due_date,due_meter=next_due(rule)
    reading=Entry.objects.filter(project=rule.project,deleted_at__isnull=True,status='erledigt',meter_unit=rule.meter_unit,meter__isnull=False,date__isnull=False).order_by('-date','-created').first()
    uncertain=bool(due_meter is not None and (not reading or reading.meter<rule.base_meter))
    if due_meter is not None and rule.base_date:
        uncertain=uncertain or Entry.objects.filter(project=rule.project,deleted_at__isnull=True,meter_replaced=True,date__gte=rule.base_date).exists()
    overdue=bool(rule.active and ((due_date and due_date<=(today or timezone.localdate())) or (due_meter is not None and not uncertain and reading.meter>=due_meter)))
    return {'rule':rule,'due_date':due_date,'due_meter':due_meter,'reading':reading,'meter_uncertain':uncertain,'due':overdue,
            'missing_date':bool((rule.interval_days or rule.interval_months) and not rule.base_date),
            'missing_meter':bool(rule.interval_km is not None and rule.base_meter is None)}


@guarded
@transaction.atomic
def complete_maintenance(owner,rule,entry,expected_version):
    rule=MaintenanceRule.objects.get(pk=rule.pk);check_owner(owner,rule);check_owner(owner,entry)
    if rule.title_template_id:raise ValidationError('Diese Regel folgt automatisch den erledigten Einträgen ihrer Titel-Vorlage.')
    prior=MaintenanceCompletion.objects.filter(rule=rule,entry=entry).first()
    if prior:
        return prior
    if rule.version!=expected_version:
        raise Conflict('Wartungsplan wurde geändert. Bitte neu laden.')
    entry=Entry.objects.get(pk=entry.pk)
    if entry.project_id!=rule.project_id or entry.deleted_at or entry.status!='erledigt':
        raise ValidationError('Einen erledigten Vorgang aus demselben Projekt wählen.')
    if not rule.active:
        raise ValidationError('Diese Wartung ist deaktiviert.')
    if (rule.interval_months or rule.interval_days) and (not entry.date or entry.precision!='day'):
        raise ValidationError('Für die Fortschreibung ist ein taggenaues Arbeitsdatum erforderlich.')
    if rule.base_date and entry.date and entry.date<rule.base_date:
        raise ValidationError('Ein älterer Vorgang darf den Wartungsplan nicht zurücksetzen.')
    if rule.interval_km is not None:
        if entry.meter is None:
            raise ValidationError('Passenden km-Stand wählen; der gewählte Vorgang hat keinen Zählerstand.')
        if entry.meter_unit != rule.meter_unit:
            current_unit = entry.meter_unit or 'keine Einheit'
            raise ValidationError(f'Zählereinheit des Vorgangs ({current_unit}) passt nicht zur Regel ({rule.meter_unit}). Bitte am Vorgang die passende Einheit ergänzen.')
        if entry.meter_replaced or (rule.base_meter is not None and entry.meter < rule.base_meter):
            raise ValidationError('Passenden km-Stand wählen; bei Zählertausch die Bezugsdaten ausdrücklich bearbeiten.')
    snapshot={'title':entry.title,'date':entry.date.isoformat() if entry.date else None,'meter':str(entry.meter) if entry.meter is not None else None,'previous_date':rule.base_date.isoformat() if rule.base_date else None,'previous_meter':str(rule.base_meter) if rule.base_meter is not None else None}
    completion=MaintenanceCompletion.objects.create(rule=rule,entry=entry,snapshot=snapshot)
    if entry.date:
        rule.base_date=entry.date
    if rule.interval_km is not None:
        rule.base_meter=entry.meter
    rule.version+=1;rule.save()
    audit(owner,rule,'Wartungsplan nach bestätigter Arbeit fortgeschrieben',snapshot)
    return completion
