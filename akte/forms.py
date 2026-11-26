import json, uuid
from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from .models import Category,Project,Entry,Step,Part,Task,TitleTemplate
from .services import decimal_input,parse_date
from .project_config import TABS

class ScanForm(forms.Form):
    scan_note=forms.CharField(label='Kurzer Zusatztext (optional)',max_length=1000,required=False,
        widget=forms.Textarea(attrs={'rows':2,'placeholder':'Zum Beispiel: Öl für den BMW oder Pflanzen fürs Aquarium'}))
    auto_route=forms.BooleanField(label='KI soll das passende Projekt auswählen',initial=True,required=False)

class ProjectRoutingForm(forms.Form):
    version=forms.IntegerField(widget=forms.HiddenInput)
    project=forms.ModelChoiceField(label='Richtiges Projekt',queryset=Project.objects.none())
    def __init__(self,*args,owner=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['project'].queryset=Project.objects.filter(owner=owner,archived=False,is_scan_inbox=False) if owner else Project.objects.none()

class ProjectForm(forms.ModelForm):
    request_token=forms.UUIDField(widget=forms.HiddenInput,required=False,initial=uuid.uuid4)
    field_editor=forms.ChoiceField(label='Feldbearbeitung',choices=[('fields','Beschriftete Felder'),('expert','JSON-Expertenansicht')],required=False,initial='fields')
    field_json=forms.CharField(label='Felddefinitionen als JSON (optional)',widget=forms.Textarea,required=False)
    version=forms.IntegerField(widget=forms.HiddenInput,required=False)
    hidden_tabs=forms.MultipleChoiceField(label='Ausgeblendete Reiter',required=False,choices=TABS,widget=forms.CheckboxSelectMultiple)
    class Meta:
        model=Project;fields=['name','kind','description','tags','favorite','archived','position','color','icon','ai_profile','hidden_tabs']
        labels={'name':'Projektname','kind':'Vorlage','description':'Beschreibung','tags':'Tags (durch Komma getrennt)','favorite':'Favorit','archived':'Archiviert'}
        widgets={'color':forms.TextInput(attrs={'type':'color'}),'ai_profile':forms.Textarea(attrs={'rows':4})}
    def clean_color(self):
        import re
        value=self.cleaned_data['color']
        if not re.fullmatch(r'#[0-9a-fA-F]{6}',value):raise ValidationError('Farbe im Format #087f80 eingeben.')
        return value
    def clean_hidden_tabs(self):
        value=self.cleaned_data.get('hidden_tabs',[])
        if len(value)==len(TABS):raise ValidationError('Mindestens einen Reiter sichtbar lassen.')
        return value
    def clean_field_json(self):
        if self.data.get('field_editor')!='expert' and ('fields-TOTAL_FORMS' in self.data or not self.data.get('field_json')): return None
        try: data=json.loads(self.cleaned_data['field_json'] or '{}')
        except ValueError: raise ValidationError('Bitte gültiges JSON eingeben.')
        if not isinstance(data,dict) or len(data)>100: raise ValidationError('Höchstens 100 benannte Felder.')
        for key,item in data.items():
            if not isinstance(item,dict) or item.get('type') not in ('text','multiline','number','date','choice','boolean','link'): raise ValidationError(f'Feld {key}: gültigen Typ angeben.')
            if item.get('type')=='choice' and (not isinstance(item.get('options',[]),list) or any(not isinstance(option,str) for option in item.get('options',[]))): raise ValidationError(f'{key}: options muss eine Liste von Textwerten sein.')
            value=item.get('value')
            if value in (None,''): continue
            typ=item['type']
            if typ=='number': decimal_input(value)
            if typ=='date': parse_date(str(value))
            if typ=='boolean' and not isinstance(value,bool): raise ValidationError(f'{key}: true oder false erforderlich.')
            if typ=='choice' and value not in item.get('options',[]): raise ValidationError(f'{key}: Wert fehlt in options.')
            if typ=='link': forms.URLField().clean(value)
        return data

class EntryForm(forms.Form):
    title_names={'category','title_template','title_suffix','title'}
    title=forms.CharField(label='Eigener Titel',max_length=180,required=False)
    category=forms.ModelChoiceField(label='Kategorie',queryset=Category.objects.none(),required=False,empty_label='Ohne Kategorie')
    title_template=forms.ModelChoiceField(label='Titel-Vorlage',queryset=TitleTemplate.objects.none(),required=False,empty_label='Eigenen Titel eingeben')
    title_suffix=forms.CharField(label='Titel-Zusatz (optional)',max_length=100,required=False)
    category_version=forms.IntegerField(widget=forms.HiddenInput,required=False)
    template_version=forms.IntegerField(widget=forms.HiddenInput,required=False)
    kind=forms.ChoiceField(choices=[('','Unbekannt'),*Entry.KINDS],required=False,widget=forms.HiddenInput)
    date_text=forms.CharField(label='Arbeitsdatum / Beginn',required=False,help_text='TT.MM.JJJJ, JJJJ-MM-TT, MM/JJJJ, JJJJ oder leer für unbekannt.')
    end_text=forms.CharField(label='Ende (bei Umbau)',required=False)
    status=forms.ChoiceField(label='Status',choices=[('','Unbekannt'),*Entry.STATES],initial='erledigt',required=False)
    meter_text=forms.CharField(label='Zählerstand (optional)',required=False)
    meter_name=forms.CharField(label='Zählerbezeichnung',initial='Kilometerzähler',required=False,max_length=100)
    meter_unit=forms.CharField(label='Einheit',initial='km',required=False,max_length=20)
    meter_replaced=forms.BooleanField(label='Zähler ausdrücklich getauscht',required=False)
    oil=forms.CharField(label='Motoröl (historischer Wert)',required=False,max_length=200,widget=forms.HiddenInput)
    quantity=forms.CharField(label='Ölmenge in Litern',required=False,widget=forms.HiddenInput)
    filter=forms.CharField(label='Filter / Teilenummer',required=False,max_length=150,widget=forms.HiddenInput)
    description=forms.CharField(label='Beschreibung / Ergebnis (Markdown)',required=False,widget=forms.Textarea,help_text='Absätze, **fett**, Listen, Tabellen und Links sind möglich.')
    cost_state=forms.ChoiceField(label='Kostenstatus',choices=[('unknown','Unbekannt'),('free','Kostenlos'),('known','Betrag bekannt')])
    amount=forms.CharField(label='Manuelle Gesamtausgabe in EUR',required=False,help_text='Bei vorhandenem Beleg die Kostenquelle bearbeiten. Keine zweite Ausgabe anlegen.')
    budget_text=forms.CharField(label='Budget in EUR (Umbau)',required=False)
    tags=forms.CharField(label='Tags',max_length=250,required=False)
    token=forms.UUIDField(widget=forms.HiddenInput)
    version=forms.IntegerField(widget=forms.HiddenInput,initial=0)
    def __init__(self,*args,owner=None,entry=None,project=None,**kwargs):
        super().__init__(*args,**kwargs)
        from .definitions import catalog,field_name
        self.entry=entry;self.project=project or (entry.project if entry else None)
        self.prefix_extra_version=(self.prefix+'-' if self.prefix else '')+'extra_version_'
        self.entry_title_editor=True;self.dynamic_names=[];self.dynamic_fields=[]
        self.definition_marks=entry.snapshot.get('extra_fields',[]) if entry else []
        self.fields['category'].queryset=Category.objects.filter(project=self.project,owner=owner) if self.project and owner else Category.objects.none()
        self.fields['title_template'].queryset=TitleTemplate.objects.filter(category__project=self.project,category__owner=owner) if self.project and owner else TitleTemplate.objects.none()
        self.definition_catalog=catalog([self.project]) if self.project else []
        identity=self.data.get(self.add_prefix('title_template')) if self.is_bound else self.initial.get('title_template')
        self.selected_template=None
        if identity:
            try:self.selected_template=self.fields['title_template'].queryset.filter(pk=identity).first()
            except (ValueError,ValidationError):pass
        category_id=self.data.get(self.add_prefix('category')) if self.is_bound else self.initial.get('category')
        if isinstance(category_id,Category):category_id=category_id.pk
        category=None
        if category_id:
            try:category=self.fields['category'].queryset.filter(pk=category_id).first()
            except (ValueError,ValidationError):pass
        self.initial['category_version']=category.version if category else None
        self.initial['template_version']=self.selected_template.version if self.selected_template else None
        from .project_config import available_fields
        if self.project:
            stored={row['id']:row.get('value','') for row in (entry.snapshot.get('extra_fields',[]) if entry else [])}
            for definition in available_fields(self.project,self.selected_template):
                name=field_name(definition);self.dynamic_names.append(name)
                value=stored.get(str(definition.pk),entry.snapshot.get(definition.legacy_key,'') if entry and definition.legacy_key else '')
                attrs={'placeholder':definition.placeholder}
                if definition.field_type=='number':attrs['inputmode']='decimal'
                self.fields[name]=forms.CharField(label=definition.label+((' ('+definition.unit+')') if definition.unit else ''),required=False,max_length=2000,initial=value,widget=forms.TextInput(attrs=attrs))
                self.dynamic_fields.append((self[name],definition))

    def clean(self):
        c=super().clean()
        if self.errors: return c
        from .definitions import category_kind,clean_field_value,field_name
        category=c.get('category')
        if self.add_prefix('category') in self.data:c['kind']=category_kind(category,self.entry.kind if self.entry else '')
        selection=None
        if self.add_prefix('title_template') in self.data:
            template=c.get('title_template')
            if category and c.get('category_version')!=category.version:raise ValidationError('Kategorie wurde geändert. Bitte neu laden.')
            if template and (not category or template.category_id!=category.pk):raise ValidationError('Titel-Vorlage gehört zu einer anderen Kategorie.')
            if template and c.get('template_version')!=template.version:raise ValidationError('Titel-Vorlage wurde geändert. Bitte neu laden.')
            values=[];allowed=set()
            from .project_config import available_fields
            if self.project:
                for definition in available_fields(self.project,template):
                    name=field_name(definition);allowed.add(name)
                    try:version=int(self.data.get(self.prefix_extra_version+definition.pk.hex,'0'))
                    except (ValueError,TypeError):version=0
                    if version!=definition.version:raise ValidationError('Zusatzfelder wurden geändert. Bitte neu laden.')
                    value=clean_field_value(definition,c.get(name))
                    values.append({'field_id':str(definition.pk),'field_version':version,'value':value or None,'inferred':False})
            if template:
                c['title']=(template.name+' '+c.get('title_suffix','')).strip()
                if len(c['title'])>180:raise ValidationError('Titel mit Zusatz ist zu lang.')
            for raw_key in self.data:
                if self.prefix and not raw_key.startswith(self.prefix+'-'):continue
                key=raw_key[len(self.prefix)+1:] if self.prefix else raw_key
                if key.startswith('extra_') and not key.startswith('extra_version_') and key not in allowed and self.data.get(raw_key):
                    raise ValidationError('Zusatzfelder passen nicht zur gewählten Titel-Vorlage.')
            selection={'category_id':str(category.pk) if category else None,'category_version':category.version if category else None,
                       'template_id':str(template.pk) if template else None,'template_version':template.version if template else None,
                       'suffix':c.get('title_suffix') or None if template else None,'values':values}
        dt,precision=parse_date(c.get('date_text'))
        end,ep=parse_date(c.get('end_text'))
        if end and ep!='day': raise ValidationError('Das Ende braucht ein genaues Datum.')
        if dt and end and end<dt: raise ValidationError('Ende liegt vor Beginn.')
        meter=decimal_input(c.get('meter_text'))
        if meter is not None and meter<0: raise ValidationError('Zählerstand darf nicht negativ sein. Für Zählertausch das Kennzeichen setzen.')
        quantity=decimal_input(c.get('quantity'))
        if quantity is not None and quantity<=0: raise ValidationError('Ölmenge muss positiv sein.')
        amount=decimal_input(c.get('amount'),money=True)
        if c['cost_state']=='known' and amount is None: raise ValidationError('Bekannten Betrag eingeben.')
        self.domain={'title':c['title'],'kind':c['kind'],'status':c['status'],'date':dt,'precision':precision,'end_date':end,'meter':meter,'meter_name':c.get('meter_name') or '', 'meter_unit':c.get('meter_unit') or '', 'meter_replaced':c['meter_replaced'],'description':c['description'],'snapshot':{'oil':c.get('oil',''),'quantity_l':str(quantity) if quantity is not None else '', 'filter':c.get('filter','')},'cost_state':c['cost_state'],'manual_amount':amount if c['cost_state']=='known' else (0 if c['cost_state']=='free' else None),'budget':decimal_input(c.get('budget_text'),money=True),'tags':c['tags']}
        if self.add_prefix('category') in self.data:self.domain['category']=c.get('category')
        for key,form_key in [('oil','oil'),('quantity_l','quantity'),('filter','filter')]:
            if self.entry and self.add_prefix(form_key) not in self.data:
                self.domain['snapshot'][key]=self.entry.snapshot.get(key,'')
        if selection is not None:self.domain['_definition']=selection
        return c

class StepForm(forms.ModelForm):
    target=forms.ModelChoiceField(label='Ziel-Vorgang',queryset=Entry.objects.none(),required=False,help_text='Beim Verschieben ziehen die zugehörigen Bilder mit um. Bereits zugeordnete Schrittkosten zuerst ausdrücklich umordnen.')
    class Meta:
        model=Step;fields=['title','date','position','text']
        labels={'title':'Arbeitsschritt','date':'Datum','position':'Reihenfolge','text':'Beschreibung (Markdown)'}
        widgets={'date':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d')}
    def __init__(self,*args,owner=None,entry=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['target'].queryset=Entry.objects.filter(project__owner=owner,deleted_at__isnull=True) if owner else Entry.objects.none()
        if entry: self.fields['target'].initial=entry.pk

class PartForm(forms.ModelForm):
    request_token=forms.UUIDField(widget=forms.HiddenInput,required=False,initial=uuid.uuid4)
    version=forms.IntegerField(widget=forms.HiddenInput,required=False)
    class Meta:
        model=Part;fields=['name','manufacturer','number','state','notes','documents']
        labels={'name':'Teil','manufacturer':'Hersteller','number':'Teilenummer','state':'Beschaffungsstatus','notes':'Notizen','documents':'Verknüpfte Belege'}
        widgets={'state':forms.Select(choices=[('benötigt','Benötigt'),('bestellt','Bestellt'),('vorhanden','Vorhanden'),('eingebaut','Eingebaut'),('ausgebaut','Ausgebaut')])}
    def __init__(self,*args,owner=None,**kwargs):
        super().__init__(*args,**kwargs)
        from .models import Document
        if self.instance.pk: self.initial['version']=self.instance.version
        self.fields['documents'].queryset=Document.objects.filter(owner=owner) if owner else Document.objects.none()

class TaskForm(forms.ModelForm):
    request_token=forms.UUIDField(widget=forms.HiddenInput,required=False,initial=uuid.uuid4)
    version=forms.IntegerField(widget=forms.HiddenInput,required=False)
    class Meta:
        model=Task;fields=['title','category','due','done','entry']
        labels={'title':'Aufgabe','category':'Kategorie','due':'Termin','done':'Erledigt','entry':'Konkreter Chronikeintrag (optional)'}
        widgets={'due':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d')}
    def __init__(self,*args,owner,project=None,**kwargs):
        super().__init__(*args,**kwargs)
        if self.is_bound and self.instance.pk and 'category' not in self.data:
            self.data=self.data.copy();self.data['category']=str(self.instance.category_id or '')
        if self.instance.pk: self.initial['version']=self.instance.version
        self.fields['entry'].queryset=Entry.objects.filter(project__owner=owner,deleted_at__isnull=True)
        if project: self.fields['entry'].queryset=self.fields['entry'].queryset.filter(project=project)
        self.fields['entry'].empty_label='Kein konkreter Chronikeintrag'
        self.fields['category'].queryset=Category.objects.filter(owner=owner,project=project) if project else Category.objects.none()
        self.fields['category'].empty_label='Ohne Kategorie'

class ReviewForm(forms.Form):
    draft_version=forms.IntegerField(widget=forms.HiddenInput,required=False,initial=0)
    invoice_number=forms.CharField(label='Rechnungsnummer',required=False,max_length=150)
    vendor=forms.CharField(label='Lieferant',required=False,max_length=200)
    invoice_date=forms.DateField(label='Rechnungsdatum',required=False,widget=forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'))
    service_date=forms.DateField(label='Leistungsdatum (leer, wenn nicht belegt)',required=False,widget=forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'))
    odometer_km=forms.CharField(label='Belegter Kilometerstand (optional)',required=False)
    gross_total=forms.CharField(label='Brutto',required=False)
    net_total=forms.CharField(label='Netto',required=False)
    tax_total=forms.CharField(label='Steuer',required=False)
    currency=forms.CharField(label='Währung (ISO)',max_length=3,required=False)
    positions_editor=forms.ChoiceField(label='Positionsbearbeitung',choices=[('rows','Editierbare Positionen'),('expert','JSON-Expertenansicht')],required=False,initial='rows')
    positions=forms.CharField(label='Positionen als JSON (optional)',required=False,widget=forms.Textarea)
    keep_unallocated=forms.BooleanField(label='Betrag zunächst vollständig unzugeordnet lassen',required=False)
    version=forms.IntegerField(widget=forms.HiddenInput)
    separate=forms.BooleanField(label='Geprüft: eigenständige Rechnung trotz Ähnlichkeit',required=False)
    chronicle_mode=forms.ChoiceField(label='Chronik',required=False,choices=[('create','Neuen Chronikeintrag anlegen'),('existing','Vorhandenen Vorgang verknüpfen'),('document','Ausdrücklich nur als Dokument ablegen')])
    chronicle_project=forms.ModelChoiceField(label='Projekt des Chronikeintrags',queryset=Project.objects.none(),required=False)
    chronicle_entry=forms.ModelChoiceField(label='Vorhandener Vorgang',queryset=Entry.objects.none(),required=False)
    entry_title=forms.CharField(label='Titel',max_length=180,required=False)
    entry_kind=forms.ChoiceField(label='Vorgangsart',choices=[('','Unbekannt'),*Entry.KINDS],required=False)
    entry_status=forms.ChoiceField(label='Status',choices=[('','Unbekannt'),*Entry.STATES],required=False)
    entry_date=forms.CharField(label='Arbeitsdatum / Beginn',required=False,help_text='Tag, Monat, Jahr oder leer. Kein automatisches Rechnungsdatum.')
    entry_end_date=forms.DateField(label='Ende',required=False,widget=forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'))
    entry_meter=forms.CharField(label='Zählerstand',required=False)
    entry_meter_unit=forms.CharField(label='Zählereinheit',max_length=20,required=False)
    entry_meter_name=forms.CharField(label='Zählerbezeichnung',max_length=100,required=False)
    entry_description=forms.CharField(label='Beschreibung / Ergebnis',max_length=8000,required=False,widget=forms.Textarea(attrs={'rows':4}))
    entry_oil=forms.CharField(label='Motoröl',max_length=200,required=False,widget=forms.HiddenInput)
    entry_quantity_l=forms.CharField(label='Ölmenge in Litern',required=False,widget=forms.HiddenInput)
    entry_filter=forms.CharField(label='Filter / Teilenummer',max_length=150,required=False,widget=forms.HiddenInput)
    entry_tags=forms.CharField(label='Tags',max_length=250,required=False)
    def __init__(self,*args,owner=None,**kwargs):
        super().__init__(*args,**kwargs)
        if owner:
            initial_project=self.initial.get('chronicle_project')
            project_filter=Q(archived=False)|Q(pk=initial_project) if initial_project else Q(archived=False)
            self.fields['chronicle_project'].queryset=Project.objects.filter(owner=owner,is_scan_inbox=False).filter(project_filter)
            self.fields['chronicle_entry'].queryset=Entry.objects.filter(project__owner=owner,deleted_at__isnull=True).select_related('project')
        self.chronicle_names=[key for key in self.fields if key.startswith(('entry_','chronicle_'))]
        self.chronicle_fields=[self[key] for key in self.chronicle_names if not self.fields[key].widget.is_hidden]
    def chronicle_choice(self,doc):
        c=self.cleaned_data
        return {'mode':c.get('chronicle_mode') or ('existing' if doc.entry_id else ('create' if doc.project_id else 'document')),
                'project':c.get('chronicle_project') or doc.project,
                'entry':c.get('chronicle_entry') or doc.entry,
                'manual_definition':'chronicle-title_template' in self.data}
    def proposal(self,positions=None):
        c=self.cleaned_data
        if positions is None:
            try: positions=json.loads(c.get('positions') or '[]')
            except ValueError: raise ValidationError('Positionsliste ist kein gültiges JSON.')
        p={k:c.get(k) or None for k in ('invoice_number','vendor','currency')}
        for key in ('gross_total','net_total','tax_total','odometer_km'):
            value=decimal_input(c.get(key),money=key!='odometer_km');p[key]=str(value) if value is not None else None
        for key in ('invoice_date','service_date'): p[key]=c[key].isoformat() if c.get(key) else None
        p['positions']=positions
        from .proposal_schema import ENTRY_FIELDS
        p['entry']={key:c.get('entry_'+key) or None for key in ENTRY_FIELDS}
        for key in ('meter','quantity_l'):
            value=decimal_input(c.get('entry_'+key))
            p['entry'][key]=str(value) if value is not None else None
        start,precision=parse_date(c.get('entry_date'))
        p['entry']['date']=(start.strftime('%Y' if precision=='year' else '%Y-%m') if precision in ('year','month') else start.isoformat()) if start else None
        end=c.get('entry_end_date')
        p['entry']['end_date']=end.isoformat() if end else None
        return p
