"""Project-scoped knowledge. AI observations never become approved specifications."""
import hashlib
import json
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import KnowledgeItem

class KnowledgeForm(forms.ModelForm):
    class Meta:
        model = KnowledgeItem
        fields = ['title', 'subject', 'kind', 'content', 'value', 'unit', 'source_reference', 'source_url']
        labels = {'title':'Titel','subject':'Bauteil / Thema','kind':'Art der Angabe',
                  'content':'Beschreibung / Geltungsbereich','value':'Wert','unit':'Einheit',
                  'source_reference':'Quelle (Dokument, Seite, Hersteller)',
                  'source_url':'Quell-URL (optional)'}
        widgets = {'content': forms.Textarea(attrs={'rows':6})}
    youtube_url = forms.URLField(label='YouTube-Link',required=False,max_length=2000)
    version = forms.IntegerField(widget=forms.HiddenInput, min_value=0)
    def __init__(self, *args, allow_link_only=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.allow_link_only = allow_link_only
        if allow_link_only:
            self.fields['content'].required = False

    def clean(self):
        data = super().clean()
        if data.get('kind') == 'reference' and not data.get('source_reference'):
            self.add_error('source_reference', 'Für einen Sollwert bitte eine überprüfbare Quelle angeben.')
        from urllib.parse import urlsplit,parse_qs
        import re
        if data.get('source_url') and urlsplit(data['source_url']).scheme not in ('http','https'):
            self.add_error('source_url','Nur HTTP- oder HTTPS-Links sind erlaubt.')
        data['youtube_id']=''
        if data.get('youtube_url'):
            url=urlsplit(data['youtube_url'])
            host=(url.hostname or '').lower()
            if host=='youtu.be':identity=url.path.strip('/')
            elif host in ('youtube.com','www.youtube.com','m.youtube.com'):
                identity=parse_qs(url.query).get('v',[''])[0] if url.path=='/watch' else url.path.split('/')[-1] if url.path.startswith(('/shorts/','/embed/')) else ''
            else:identity=''
            if url.scheme not in ('http','https') or not re.fullmatch(r'[A-Za-z0-9_-]{11}',identity):
                self.add_error('youtube_url','Bitte einen gültigen YouTube-Video-Link verwenden.')
            else:data['youtube_id']=identity
        if self.allow_link_only and not self.errors and not any(data.get(key) for key in ('content','source_url','youtube_id','value')):
            self.add_error('content','Bitte eine Notiz, einen Wert oder einen Link eintragen.')
        return data

def knowledge_schema():
    properties = {key:{'type':'string','maxLength':limit} for key,limit in {
        'title':180,'subject':200,'content':4000,'value':100,'unit':30,'source_quote':4000,'source_reference':1000}.items()}
    properties['kind'] = {'type':'string','enum':['observation','reference','note']}
    return {'type':'array','maxItems':12,'items':{'type':'object','properties':properties,
        'required':list(properties),'additionalProperties':False}}

INSTRUCTION = (
    'knowledge enthält höchstens zwölf wiederverwendbare Wissensvorschläge aus der Quelle, sonst []. '
    'Erfasse z.B. Drehmomente, Bauteilbezeichnungen, Material, Werkzeuge und Arbeitshinweise. '
    'Jede Angabe braucht source_quote als wörtlichen Beleg aus Text/Transkript oder lesbarem Dokument. '
    'Wenn kein belegbarer Text vorliegt, keinen Wissenseintrag erfinden. '
    '„Ich habe mit 120 Nm angezogen“ ist kind=observation (Durchführung), niemals reference (Hersteller-Sollwert). '
    'reference nur bei ausdrücklich genannter Hersteller-/Handbuchquelle mit source_reference. '
    'Geltungsbereich (Bauteil, Schraube, Fahrzeug, Bedingungen) nur soweit belegt; Unklarheiten in content benennen. '
    'Wert und Einheit getrennt als Text; unbekannt leer. Die spätere Nutzerprüfung ist immer erforderlich. '
)

def validate_knowledge(rows):
    from .proposal_schema import validate_ai_content
    if not isinstance(rows,list) or len(rows)>12:
        raise ValidationError('Ungültige Wissensvorschläge.')
    schema=knowledge_schema()['items']['properties']
    for row in rows:
        if not isinstance(row,dict) or set(row)!=set(schema):
            raise ValidationError('Unvollständiger Wissensvorschlag.')
        for key,rule in schema.items():
            if not isinstance(row[key],str) or len(row[key])>rule.get('maxLength',20):
                raise ValidationError('Ungültiges Wissensfeld.')
        if row['kind'] not in ('observation','reference','note') or not row['title'].strip() or not row['source_quote'].strip():
            raise ValidationError('Wissensvorschlag ohne Quellenbeleg.')
        if row['kind']=='reference' and not row['source_reference'].strip():
            raise ValidationError('Sollwert ohne Quellenangabe.')
        validate_ai_content(row)
    return rows

def record_suggestions(document, rows):
    if not document.entry_id or document.entry.project.is_scan_inbox:
        return
    for row in validate_knowledge(rows):
        digest=hashlib.sha256((str(document.pk)+json.dumps(row,sort_keys=True,ensure_ascii=False)).encode()).hexdigest()
        KnowledgeItem.objects.get_or_create(owner=document.owner,source_fingerprint=digest,
            defaults={**row,'project':document.entry.project,'entry':document.entry,
                      'source_document':document,'state':'review'})
