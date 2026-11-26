"""Bounded formsets for mobile-friendly fields and invoice positions."""
from django import forms
from django.forms import formset_factory,BaseFormSet
from django.core.exceptions import ValidationError
from .services import decimal_input,parse_date

FIELD_TYPES=[('text','Text'),('multiline','Mehrzeiliger Text'),('number','Zahl'),('date','Datum'),('choice','Auswahl'),('boolean','Ja / Nein'),('link','Link')]

class ProjectFieldForm(forms.Form):
    position=forms.IntegerField(label='Reihenfolge',required=False,initial=0)
    original_name=forms.CharField(required=False,widget=forms.HiddenInput)
    name=forms.CharField(label='Feldname',max_length=100)
    kind=forms.ChoiceField(label='Typ',choices=FIELD_TYPES,initial='text')
    value=forms.CharField(label='Wert',required=False)
    unit=forms.CharField(label='Einheit (optional)',max_length=30,required=False)
    options=forms.CharField(label='Auswahlmöglichkeiten (eine pro Zeile)',required=False,widget=forms.Textarea(attrs={'rows':3}))
    active=forms.BooleanField(label='Aktiv',required=False,initial=True)

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        kind=self.data.get(self.add_prefix('kind')) if self.is_bound else self.initial.get('kind','text')
        value=self.initial.get('value')
        if kind=='boolean': self.fields['value']=forms.BooleanField(label='Wert: Ja',required=False)
        elif kind=='multiline': self.fields['value'].widget=forms.Textarea(attrs={'rows':3})
        elif kind=='choice':
            raw=self.data.get(self.add_prefix('options'),'') if self.is_bound else self.initial.get('options','')
            choices=[line.strip() for line in raw.splitlines() if line.strip()]
            self.fields['value']=forms.ChoiceField(label='Wert',required=False,choices=[('','—')]+[(v,v) for v in choices])
        elif kind=='number':
            self.fields['value'].widget=forms.TextInput(attrs={'inputmode':'decimal'})
            if value is not None: self.initial['value']=str(value).replace('.',',')
        elif kind=='date': self.fields['value'].widget=forms.TextInput(attrs={'placeholder':'TT.MM.JJJJ oder MM/JJJJ'})
        elif kind=='link': self.fields['value']=forms.URLField(label='Link',required=False)

    def clean(self):
        c=super().clean()
        if self.errors or c.get('DELETE'): return c
        kind=c.get('kind');value=c.get('value')
        if kind=='number' and value not in ('',None):
            n=decimal_input(value);c['value']=str(n) if n is not None else ''
        elif kind=='date' and value:
            dt,precision=parse_date(value)
            c['value']=dt.isoformat() if precision=='day' else dt.strftime('%Y-%m' if precision=='month' else '%Y')
        return c

class ProjectFieldsBase(BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors): return
        names=[];originals=[]
        for form in self.forms:
            c=form.cleaned_data
            if not c or c.get('DELETE'): continue
            if c['name'] in names: raise ValidationError('Jeder Feldname darf nur einmal vorkommen.')
            if c.get('original_name') and c['original_name'] in originals: raise ValidationError('Ein bestehendes Feld wurde mehrfach übermittelt.')
            names.append(c['name']);originals.append(c.get('original_name'))

ProjectFields=formset_factory(ProjectFieldForm,formset=ProjectFieldsBase,extra=0,can_delete=True,max_num=100,validate_max=True,absolute_max=110)

def project_field_initial(fields):
    rows=[]
    for index,(name,item) in enumerate(fields.items()):
        value=item.get('value','')
        if item.get('type')=='number' and value not in ('',None):
            # Old JSON strings were German input; new editor values are canonical.
            if isinstance(value,str) and item.get('value_format')!='decimal': value=str(decimal_input(value))
            value=str(value).replace('.',',')
        rows.append({'position':index,'original_name':name,'name':name,'kind':item.get('type','text'),'value':value,'unit':item.get('unit',''),'options':'\n'.join(map(str,item.get('options',[]))),'active':item.get('active',True)})
    return rows

def project_fields_data(formset,previous):
    # Missing/deleted/renamed definitions remain retired with their old values.
    result={name:{**item,'active':False} for name,item in previous.items()}
    ordered=sorted(formset,key=lambda form:form.cleaned_data.get('position') or 0)
    active_names=[]
    for form in ordered:
        c=form.cleaned_data
        if not c or c.get('DELETE'): continue
        original=c.get('original_name','')
        if original and original not in previous: raise ValidationError('Unbekanntes ursprüngliches Feld. Bitte neu laden.')
        if c['name'] in previous and c['name']!=original: raise ValidationError(f'Feldname „{c["name"]}“ ist bereits vergeben. Vorhandenes Feld bearbeiten.')
        item={**previous.get(original,{}),'type':c['kind'],'value':c.get('value',''),'unit':c.get('unit',''),'active':c['active']}
        if c['kind']=='number': item['value_format']='decimal'
        else: item.pop('value_format',None)
        if c['kind']=='choice': item['options']=[line.strip() for line in c['options'].splitlines() if line.strip()]
        result[c['name']]=item
        active_names.append(c['name'])
    if len(result)>100: raise ValidationError('Höchstens 100 Felddefinitionen einschließlich stillgelegter Felder sind möglich.')
    return {name:result[name] for name in active_names+[name for name in result if name not in active_names]}

class PositionForm(forms.Form):
    description=forms.CharField(label='Beschreibung',max_length=500)
    part_number=forms.CharField(label='Teilenummer (optional)',max_length=150,required=False)
    quantity=forms.CharField(label='Menge (optional)',required=False,widget=forms.TextInput(attrs={'inputmode':'decimal'}))
    unit=forms.CharField(label='Einheit (z. B. l, Stück, h)',required=False,max_length=30)
    unit_price=forms.CharField(label='Einzelpreis laut Beleg (optional)',required=False,widget=forms.TextInput(attrs={'inputmode':'decimal'}))
    price_basis=forms.ChoiceField(label='Einzelpreis ist',required=False,choices=[('unknown','Nicht angegeben'),('gross','Brutto'),('net','Netto')])
    def clean(self):
        data=super().clean()
        for key in ('quantity','unit_price'):
            if data.get(key):
                number=decimal_input(data[key])
                data[key]=str(number)
        from .line_items import validate_line_details
        if not self.errors and not data.get('DELETE'):
            validate_line_details({**data,'price_basis':data.get('price_basis') or 'unknown'})
        return data
    amount=forms.CharField(label='Bruttobetrag',widget=forms.TextInput(attrs={'inputmode':'decimal','placeholder':'0,00'}))
    def clean_amount(self):
        n=decimal_input(self.cleaned_data['amount'],money=True)
        if n is None: raise ValidationError('Betrag fehlt.')
        return str(n)

Positions=formset_factory(PositionForm,extra=0,can_delete=True,max_num=300,validate_max=True,absolute_max=305)

def position_initial(positions):
    return [{**item,**{key:str(item.get(key) or '').replace('.',',') for key in ('amount','quantity','unit_price')}} for item in positions if isinstance(item,dict)]

def position_data(formset):
    return [{key:form.cleaned_data.get(key,'') for key in ('description','part_number','amount','quantity','unit','unit_price','price_basis')} for form in formset if form.cleaned_data and not form.cleaned_data.get('DELETE')]

def raw_position_rows(data):
    try: count=min(max(int(data.get('positions-TOTAL_FORMS',0)),0),305)
    except ValueError: count=0
    return [{key:data.get(f'positions-{i}-{key}','') for key in ('description','part_number','amount','quantity','unit','unit_price','price_basis','DELETE')} for i in range(count)]

def raw_allocations(data):
    keys=('alloc_project','alloc_entry','alloc_step','alloc_amount','alloc_ref')
    values=[data.getlist(key) for key in keys]
    count=max((len(column) for column in values),default=0)
    names=('project_id','entry_id','step_id','amount','position_ref')
    return [{name:(column[i] if i<len(column) else '') for name,column in zip(names,values)} for i in range(count)]
