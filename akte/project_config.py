"""Small shared project controls and read-only form suggestions."""
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import gettext as _, gettext_lazy

from .models import Entry, TitleField, Revision

TABS = [('chronik', gettext_lazy('Chronik')), ('umbauten', gettext_lazy('Umbauten')), ('teile', gettext_lazy('Teile')),
        ('dokumente', gettext_lazy('Dokumente')), ('kosten', gettext_lazy('Kosten')), ('aufgaben', gettext_lazy('Aufgaben')),
        ('stammdaten', gettext_lazy('Stammdaten')), ('wartung', gettext_lazy('Wartungsplan'))]


def visible_tabs(project):
    return [(key, label) for key, label in TABS if key not in project.hidden_tabs]


def available_fields(project, template=None):
    query = Q(project=project)
    if template:
        query |= Q(template=template)
    return TitleField.objects.filter(query).order_by('created', 'id')


def recent_values(project):
    """Newest persisted values first; retain pre-builder oil fields as suggestions."""
    from .models import TitleField
    from django.db.models import Q
    result={}
    legacy={str(field.pk):field.legacy_key for field in TitleField.objects.filter(Q(project=project)|Q(template__category__project=project)) if field.legacy_key}
    entries=Entry.objects.filter(project=project,deleted_at__isnull=True)
    snapshots=list(entries.order_by('-created','-id').values_list('snapshot',flat=True)[:500])
    revisions=Revision.objects.filter(owner=project.owner,entity__in=entries.values('pk')).order_by('-created','-id').values_list('data',flat=True)[:500]
    snapshots.extend(data['snapshot'] for data in revisions if isinstance(data.get('snapshot'),dict))
    for snapshot in snapshots:
        rows=list(snapshot.get('extra_fields',[]))+[{'id':identity,'value':snapshot.get(key)} for identity,key in legacy.items()]
        for row in rows:
            if not isinstance(row,dict) or not row.get('id'): continue
            value=row.get('value')
            if value in (None,''): continue
            value=str(value)
            values=result.setdefault(row['id'],[])
            if value not in values and len(values)<12:values.append(value)
    return result


def meter_warning(entry):
    if entry.date is None or entry.meter is None or entry.meter_replaced:
        return ''
    earlier = Entry.objects.filter(project=entry.project, deleted_at__isnull=True,
        date__isnull=False, meter__isnull=False, meter_unit=entry.meter_unit).exclude(pk=entry.pk).filter(
        Q(date__lt=entry.date) | Q(date=entry.date, created__lt=entry.created)).order_by('-date', '-created').first()
    if earlier and entry.meter < earlier.meter:
        return f'Zählerstand prüfen: {entry.meter} {entry.meter_unit} liegt unter dem vorherigen Eintrag „{earlier.title}“ ({earlier.meter} {earlier.meter_unit}, {earlier.display_date}). Der Eintrag wurde gespeichert.'
    return ''


def validate_intervals(data):
    for name, maximum in [('interval_days', 36500), ('interval_months', 600)]:
        value = data.get(name)
        if value is not None and not 1 <= value <= maximum:
            raise ValidationError('Wartungsintervall muss positiv sein (höchstens 36500 Tage / 600 Monate).')
    meter = data.get('interval_meter')
    if meter is not None and (meter <= 0 or not data.get('meter_unit', '').strip()):
        raise ValidationError('Für ein Zählerintervall einen positiven Wert und eine Einheit angeben.')
