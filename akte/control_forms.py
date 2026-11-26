from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from .feedback_store import FeedbackImage
from .models import Project


class VersionForm(forms.Form):
    version=forms.IntegerField(widget=forms.HiddenInput,min_value=0)


class DurationForm(VersionForm):
    session_days=forms.TypedChoiceField(label='Login-Dauer für neue Anmeldungen',coerce=int,
        choices=[(7,'7 Tage'),(30,'30 Tage'),(90,'90 Tage')])


class IntegrationForm(VersionForm):
    ai_mode=forms.ChoiceField(label='Gemini-Modell',choices=[('daily','Alltag · Gemini Flash-Lite'),('complex','Komplexe Belege · Gemini 3.8 Flash')])
    keep_audio=forms.BooleanField(label='Original-Audio nach erfolgreicher Auswertung behalten',required=False)
    paperless_tags=forms.CharField(max_length=4096,label='Paperless Import-Tags: IDs durch Komma getrennt',required=False,help_text='Leer = alle sichtbaren Belege. Mehrere Tags gelten als ODER-Filter. Tag-IDs stehen in Paperless unter Tags.')
    mail_senders=forms.CharField(max_length=16000,label='Erlaubte E-Mail-Absender (eine Adresse pro Zeile)',required=False,widget=forms.Textarea(attrs={'rows':4}),help_text='Eine leere Liste deaktiviert den Import. Zugangsdaten bleiben unverändert auf dem Server.')
    def clean_paperless_tags(self):
        try:values=list(dict.fromkeys(int(part.strip()) for part in self.cleaned_data['paperless_tags'].split(',') if part.strip()))
        except ValueError:raise ValidationError('Nur numerische Tag-IDs verwenden.') from None
        if len(values)>100 or any(value<1 for value in values):raise ValidationError('Höchstens 100 positive Tag-IDs verwenden.')
        return values
    def clean_mail_senders(self):
        values=list(dict.fromkeys(line.strip().casefold() for line in self.cleaned_data['mail_senders'].splitlines() if line.strip()))
        if len(values)>50:raise ValidationError('Höchstens 50 erlaubte Absender.')
        for value in values:
            forms.EmailField().clean(value)
            if any(c in value for c in '\r\n" <>'):raise ValidationError('Nur reine E-Mail-Adressen eintragen.')
        return values


class BudgetForm(forms.Form):
    monthly_usd = forms.DecimalField(
        label='Appinternes Monatslimit (USD)', min_value=0, max_digits=10, decimal_places=2,
        help_text='0 pausiert neue kostenpflichtige KI-Anfragen.',
    )

    def clean_monthly_usd(self):
        value = self.cleaned_data['monthly_usd']
        if value > settings.AI_MONTHLY_LIMIT:
            raise ValidationError('Das konfigurierte Serverlimit darf nicht überschritten werden.')
        return value


class MediaForm(VersionForm):
    photo_width=forms.TypedChoiceField(label='Maximale Fotobreite',coerce=int,choices=[(0,'Original'),(1600,'1600 px'),(2048,'2048 px')])
    photo_quality=forms.IntegerField(label='JPEG-Qualität (%)',min_value=40,max_value=95)
    trash_days=forms.IntegerField(label='Papierkorb-Aufbewahrung in Tagen (leer = nie automatisch löschen)',required=False,min_value=1,max_value=3650)
    trash_costs=forms.BooleanField(label='Automatische Löschung darf auch zugehörige Belegkosten endgültig entfernen',required=False,help_text='Ohne dieses Häkchen bleiben Belege mit Kostenbuchungen zur manuellen Prüfung erhalten. Paperless-Originale werden nicht gelöscht.')


class BackupOptionsForm(VersionForm):
    backup_keep=forms.IntegerField(label='Neueste Sicherungen insgesamt behalten',min_value=1,max_value=100,help_text='Gilt auch für Sicherungen vor Updates. Ältere Rückspielstände werden erst nach einer erfolgreichen neuen Sicherung entfernt.')


class FeedbackEditForm(forms.Form):
    text=forms.CharField(label='Feedback',max_length=4000,widget=forms.Textarea(attrs={'rows':3}))
    done=forms.BooleanField(label='Von dir geprüft und erledigt',required=False)
    implemented=forms.BooleanField(label='Von Codex umgesetzt',required=False)
    digest=forms.CharField(widget=forms.HiddenInput,max_length=64)
    image=FeedbackImage(label='Bild hinzufügen (auch Strg+V im Formular)',required=False)


class HandoverForm(forms.Form):
    project=forms.ModelChoiceField(label='Projekt',queryset=Project.objects.none())
    hide_costs=forms.BooleanField(label='Kosten / Preise ausblenden',required=False,initial=True)
    hide_notes=forms.BooleanField(label='Private Notizen ausblenden',required=False,initial=True)
    hide_dealers=forms.BooleanField(label='Händler ausblenden',required=False,initial=True)
    def __init__(self,*args,owner,**kwargs):
        super().__init__(*args,**kwargs);self.fields['project'].queryset=Project.objects.filter(owner=owner,is_scan_inbox=False)

class GeminiKeyForm(forms.Form):
    api_key=forms.CharField(label='Neuer Gemini-API-Schlüssel',max_length=120,
        widget=forms.PasswordInput(render_value=False,attrs={'autocomplete':'new-password','spellcheck':'false'}),
        help_text='Wird nur geschützt auf dem Server gespeichert und niemals wieder angezeigt. Gilt für Web und Worker ohne Neustart.')
