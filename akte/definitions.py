"""Project-specific entry definitions, shared by forms, imports and Gemini."""
import json
import re
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError

from .models import Category, TitleTemplate, TitleField


def category_hint(category):
    if not category:return ''
    names={'wartung':'wartung','wartungen':'wartung','pflege':'wartung',
           'maintenance':'wartung','care':'wartung','care & notes':'notiz',
           'umbau':'umbau','umbauten':'umbau','modification':'umbau','modifications':'umbau',
           'reparatur':'reparatur','reparaturen':'reparatur','repair':'reparatur','repairs':'reparatur',
           'notiz':'notiz','notizen':'notiz','teile & zubehör':'notiz',
           'note':'notiz','notes':'notiz','parts & accessories':'notiz'}
    return names.get(category.name.casefold(),category.default_kind or '')


def category_kind(category, previous=''):
    if not category:return previous
    return category_hint(category) or previous or 'notiz'


def sync_classification(entry):
    if entry.category_id:
        if entry.category.project_id!=entry.project_id:
            raise ValidationError('Kategorie gehört zu einem anderen Projekt.')
        entry.kind=category_kind(entry.category,entry.kind)
    if entry.title_template_id and (not entry.category_id or entry.title_template.category_id!=entry.category_id):
        raise ValidationError('Titel-Vorlage gehört nicht zur gewählten Kategorie.')


def field_name(field):
    return 'extra_'+field.pk.hex


def number_value(value):
    value=str(value).strip()
    if ',' in value:
        if '.' in value or value.count(',')!=1:
            raise ValidationError('Zahl bitte ohne Tausendertrennzeichen eingeben.')
        value=value.replace(',','.')
    if not re.fullmatch(r'-?\d+(?:\.\d{1,6})?',value):
        raise ValidationError('Eine Zahl mit höchstens sechs Nachkommastellen eingeben.')
    try:
        number=Decimal(value)
    except InvalidOperation:
        raise ValidationError('Ungültige Zahl.') from None
    if not number.is_finite() or abs(number)>Decimal('999999999999'):
        raise ValidationError('Zahl außerhalb des zulässigen Bereichs.')
    return format(number,'f')


def clean_field_value(field, value):
    if value in (None,''):
        return ''
    if not isinstance(value,str) or len(value)>2000:
        raise ValidationError('Zusatzfeld enthält keinen gültigen Text.')
    value=value.strip()
    if not value:return ''
    if field.field_type=='number':
        value=number_value(value)
    if field.legacy_key=='quantity_l' and (Decimal(value)<=0 or Decimal(value)!=Decimal(value).quantize(Decimal('.001'))):
        raise ValidationError('Ölmenge muss positiv sein und darf höchstens drei Nachkommastellen haben.')
    return value


def catalog(projects):
    projects=list(projects)
    categories=Category.objects.filter(project__in=projects).select_related('project').prefetch_related('title_templates__extra_fields')
    from .project_config import recent_values
    result=[]
    for project in projects:
        suggestions=recent_values(project)
        def describe(f):
            return {'id':str(f.pk),'label':f.label,'type':f.field_type,'unit':f.unit,
                    'placeholder':f.placeholder,'version':f.version,'legacy_key':f.legacy_key,
                    'suggestions':suggestions.get(str(f.pk),[])}
        groups=[]
        for category in categories:
            if category.project_id!=project.pk:continue
            titles=[]
            for template in category.title_templates.all():
                titles.append({'id':str(template.pk),'name':template.name,'version':template.version,
                    'fields':[describe(f) for f in template.extra_fields.all()]})
            groups.append({'id':str(category.pk),'name':category.name,'version':category.version,
                           'kind':category_hint(category) or None,'titles':titles})
        result.append({'project_id':str(project.pk),'categories':groups,
                       'fields':[describe(f) for f in project.entry_fields.all()]})
    if len(json.dumps(result,ensure_ascii=False).encode())>250_000:
        raise ValidationError('Titel-/Feldkatalog zu groß für eine gemeinsame KI-Anfrage. Bitte ein einzelnes Projekt wählen oder Vorlagen reduzieren.')
    return result


def selection_schema(context):
    groups=[g for p in (context or {}).get('entry_definitions',[]) for g in p['categories']]
    titles=[t for g in groups for t in g['titles']]
    fields=[f for t in titles for f in t['fields']]+[f for p in (context or {}).get('entry_definitions',[]) for f in p.get('fields',[])]
    nullable=lambda values:{'type':['string','null'],'enum':[None,*values]}
    props={'category_id':nullable([g['id'] for g in groups]),'category_version':{'type':['integer','null']},
           'template_id':nullable([t['id'] for t in titles]),'template_version':{'type':['integer','null']},
           'suffix':{'type':['string','null'],'maxLength':100},
           'values':{'type':'array','maxItems':min(100,len(fields)),'items':{'type':'object','properties':{
               'field_id':{'type':'string',**({'enum':[f['id'] for f in fields]} if fields else {})},
               'field_version':{'type':'integer'},'value':{'type':['string','null'],'maxLength':2000},
               'inferred':{'type':'boolean'}},'required':['field_id','field_version','value','inferred'],'additionalProperties':False}}}
    return {'type':'object','properties':props,'required':list(props),'additionalProperties':False}


SELECTION_INSTRUCTION=(
    ' entry_definitions enthält ausschließlich freigegebene Projekt-Kategorien, Titel-Vorlagen und deren Zusatzfelder. '
    'definition wählt category_id und template_id nur aus dem tatsächlich zugeordneten Projekt samt den gelieferten Versionsnummern. '
    'Passt eine Titel-Vorlage inhaltlich, wähle sie und ergänze optional einen kurzen suffix; entry.title ist Vorlagenname plus Zusatz. '
    'values enthält ausschließlich projektweite fields und Felder der gewählten Vorlage mit field_id, field_version, value als Dezimalstring/Text oder null und inferred. Vorschlagswerte sind kein Nachweis. '
    'Befülle nur tatsächlich genannte/erkannte Fakten. Projekt-Stammdaten sind kein Nachweis für historische Ölmenge oder andere Werte. '
    'Interpretationspfade für die Auswahl heißen definition.category_id, definition.template_id oder definition.suffix. '
    'Bei Zusatzwerten nutze vorzugsweise deren inferred-Flag; alternativ definition.values.<Index>.value. Versionen sind keine Interpretationen. '
    'Ohne passende Vorlage: template_id/template_version/suffix=null, entry.title als eigener freier Titel; values darf nur projektweite fields enthalten. '
    'Wähle die Kategorie nach der tatsächlich beschriebenen Arbeit, niemals die erste Kategorie oder bloß eine mit gleicher kind. '
    'Pflege bedeutet Reinigung/Konservierung, nicht Bauteiltausch. Airbag-Tausch ist Reparatur (bei Nachrüstung Umbau), niemals Pflege. '
    'Fehlt eine fachlich passende Kategorie, setze category_id/category_version=null. KI-Projekt-Steckbrief hilft bei Abgrenzungen, ersetzt aber keinen Quellennachweis. '
    'Bei unklarem Projekt bleiben auch category_id/category_version=null. Keine Felder erfinden. '
    'Öl/Menge/Filter gehören nur zur passenden Ölwechsel-Vorlage; ohne Vorlage auch entry.oil/quantity_l/filter=null. '
    'Belege keinen Ölwechsel durch bloßen Ölkauf. '
)


def validate_selection_shape(selection):
    expected={'category_id','category_version','template_id','template_version','suffix','values'}
    if not isinstance(selection,dict) or set(selection)!=expected:
        raise ValidationError('Unvollständige Titel-/Feldauswahl.')
    if selection['suffix'] is not None and (not isinstance(selection['suffix'],str) or len(selection['suffix'])>100):
        raise ValidationError('Titel-Zusatz ist zu lang oder ungültig.')
    if not isinstance(selection['values'],list) or len(selection['values'])>100:
        raise ValidationError('Ungültige Zusatzfeldliste.')
    seen=set()
    for row in selection['values']:
        if not isinstance(row,dict) or set(row)!={'field_id','field_version','value','inferred'}:
            raise ValidationError('Ungültiges Zusatzfeld.')
        if not isinstance(row['field_id'],str) or row['field_id'] in seen or type(row['field_version']) is not int or type(row['inferred']) is not bool:
            raise ValidationError('Doppeltes oder ungültiges Zusatzfeld.')
        if row['value'] is not None and (not isinstance(row['value'],str) or len(row['value'])>2000):
            raise ValidationError('Ungültiger Zusatzfeldwert.')
        if row['value'] in (None,'') and row['inferred']:
            raise ValidationError('Fehlende Zusatzwerte dürfen nicht als Interpretation markiert werden.')
        seen.add(row['field_id'])
    for prefix in ('category','template'):
        value=selection[prefix+'_id'];version=selection[prefix+'_version']
        if value is None:
            if version is not None:raise ValidationError('Version ohne zugehörige Auswahl.')
        elif not isinstance(value,str) or type(version) is not int or version<1:
            raise ValidationError('Ungültige Auswahlkennung oder Version.')
    if selection['template_id'] is None and selection['suffix']:
        raise ValidationError('Ein freier Titel darf keinen Vorlagen-Zusatz enthalten.')
    if selection['template_id'] and not selection['category_id']:
        raise ValidationError('Eine Titel-Vorlage benötigt ihre Kategorie.')


def validate_ai_definition(proposal, context):
    selection=proposal.get('definition')
    if selection is None:return
    validate_selection_shape(selection)
    project_id=(context or {}).get('selected_project') or (proposal.get('project_assignment') or {}).get('project_id')
    groups={g['id']:g for p in (context or {}).get('entry_definitions',[]) if p['project_id']==project_id for g in p['categories']}
    group=groups.get(selection['category_id'])
    if selection['category_id'] is not None and (not group or selection['category_version']!=group['version']):
        raise ValidationError('Kategorie gehört nicht zum erkannten Projekt oder wurde verändert.')
    project_definition=next((p for p in (context or {}).get('entry_definitions',[]) if p['project_id']==project_id),{})
    template=None
    if selection['template_id']:
        template=next((t for t in (group or {}).get('titles',[]) if t['id']==selection['template_id']),None)
        if not template or template['version']!=selection['template_version']:
            raise ValidationError('Titel-Vorlage gehört nicht zur Kategorie oder wurde verändert.')
    else:
        if any(proposal['entry'].get(key) not in (None,'') for key in ('oil','quantity_l','filter')):
            raise ValidationError('Freie Titel dürfen keine titelabhängigen Öl-Zusatzfelder enthalten.')
    fields={f['id']:f for f in project_definition.get('fields',[])+((template or {}).get('fields',[]))}
    for row in selection['values']:
        field=fields.get(row['field_id'])
        if not field or row['field_version']!=field['version']:
            raise ValidationError('Zusatzfeld gehört nicht zur Titel-Vorlage oder wurde verändert.')
        if field['type']=='number' and row['value'] not in (None,''):row['value']=number_value(row['value'])
        if row['value'] not in (None,'') and field.get('legacy_key') and 'entry.'+field['legacy_key'] in proposal.get('inferred_fields',[]):
            row['inferred']=True
    # A category may only refine the kind when consistent with the source type.
    if group and group.get('kind') and proposal['entry'].get('kind') not in (None,'',group['kind']):
        raise ValidationError('KI-Kategorie widerspricht der erkannten Arbeit. Bitte Zuordnung prüfen.')
    if template:
        title=(template['name']+' '+(selection['suffix'] or '')).strip()
        if len(title)>180:raise ValidationError('Titel mit Zusatz ist zu lang.')
        proposal['entry']['title']=title
    if group and group['kind']:proposal['entry']['kind']=group['kind']


def apply_definition(entry, selection, *, manual=False, inferred_paths=()):
    validate_selection_shape(selection)
    previous_values={row['id']:row for row in entry.snapshot.get('extra_fields',[])}
    previous_selection={'category_id':str(entry.category_id) if entry.category_id else None,
                        'template_id':str(entry.title_template_id) if entry.title_template_id else None,
                        'suffix':entry.title_suffix or None}
    category=None;template=None
    if selection['category_id']:
        category=Category.objects.filter(pk=selection['category_id'],project=entry.project).first()
        if not category or category.version!=selection['category_version']:
            raise ValidationError('Kategorie wurde geändert oder gehört zu einem anderen Projekt. Bitte neu laden.')
    if selection['template_id']:
        template=TitleTemplate.objects.filter(pk=selection['template_id'],category=category).first()
        if not template or template.version!=selection['template_version']:
            raise ValidationError('Titel-Vorlage wurde geändert. Bitte neu laden.')
    rows=[]
    from .project_config import available_fields
    fields={str(f.pk):f for f in available_fields(entry.project,template)}
    for value in selection['values']:
        field=fields.get(value['field_id'])
        if not field or field.version!=value['field_version']:
            raise ValidationError('Zusatzfeld wurde geändert oder gehört zu einer anderen Vorlage.')
        cleaned=clean_field_value(field,value['value'])
        if cleaned:
            previous=previous_values.get(str(field.pk),{})
            inferred=bool(previous.get('inferred') and previous.get('value')==cleaned) if manual else value['inferred']
            rows.append({'id':str(field.pk),'label':field.label,'type':field.field_type,'unit':field.unit,
                         'value':cleaned,'legacy_key':field.legacy_key,'inferred':bool(inferred),
                         'scope':'project' if field.project_id else 'title'})
        if field.legacy_key:entry.snapshot[field.legacy_key]=cleaned
    previous=entry.snapshot.get('extra_fields',[])
    if str(entry.title_template_id or '')==str(template.pk if template else ''):
        rows.extend(row for row in previous if row['id'] not in fields)
    elif previous:
        history=entry.snapshot.get('_previous_extra_fields',[])
        history.append({'title':entry.snapshot.get('title_template_name',''),'fields':previous})
        entry.snapshot['_previous_extra_fields']=history
    entry.category=category;entry.title_template=template;entry.title_suffix=(selection['suffix'] or '').strip()
    entry.snapshot['extra_fields']=rows
    entry.snapshot['title_template_name']=template.name if template else ''
    scalar_paths=['definition.category_id','definition.template_id','definition.suffix']
    if manual:
        entry.snapshot['_definition_selected']=True
        entry.snapshot['_definition_inferred']=[path for path in entry.snapshot.get('_definition_inferred',[])
            if path in scalar_paths and previous_selection[path.split('.')[1]]==selection[path.split('.')[1]]]
    else:entry.snapshot['_definition_inferred']=[path for path in inferred_paths if path in scalar_paths]
    if template:
        entry.title=(template.name+' '+entry.title_suffix).strip()
        if len(entry.title)>180:raise ValidationError('Titel mit Zusatz ist zu lang.')
    sync_classification(entry)


def ensure_project_definitions(project):
    if project.categories.exists():return
    from .i18n import effective_language
    en = effective_language(project.owner if project.owner_id else None) == 'en'
    if en:
        defaults = [('Maintenance','wartung'),('Modification','umbau'),('Repair','reparatur'),('Notes','notiz'),('Care',''),('Parts & Accessories','')]
        maint_key, oil_name = 'maintenance', 'Oil Change'
        oil_fields = [('Engine oil','text','','oil'),('Oil quantity','number','l','quantity_l'),('Filter / Part number','text','','filter')]
    else:
        defaults = [('Wartung','wartung'),('Umbau','umbau'),('Reparatur','reparatur'),('Notizen','notiz'),('Pflege',''),('Teile & Zubehör','')]
        maint_key, oil_name = 'wartung', 'Ölwechsel'
        oil_fields = [('Motoröl','text','','oil'),('Ölmenge','number','l','quantity_l'),('Filter / Teilenummer','text','','filter')]
    for name,kind in defaults:
        Category.objects.get_or_create(project=project,name_key=name.casefold(),defaults={'owner':project.owner,'name':name,'default_kind':kind})
    if project.kind=='auto':
        template,_=TitleTemplate.objects.get_or_create(category=project.categories.get(name_key=maint_key),name=oil_name)
        for label,typ,unit,key in oil_fields:
            TitleField.objects.get_or_create(template=template,label=label,defaults={'field_type':typ,'unit':unit,'legacy_key':key})


def retain_review_definition(proposal, original):
    """A manual title suffix keeps its template; a genuinely free title drops it."""
    if not original.get('definition'):return
    import copy
    selection=copy.deepcopy(original['definition'])
    template=TitleTemplate.objects.filter(pk=selection.get('template_id')).first() if selection.get('template_id') else None
    title=proposal['entry'].get('title') or ''
    if template:
        prefix=template.name
        if title.casefold()==prefix.casefold() or title.casefold().startswith(prefix.casefold()+' '):
            suffix=title[len(prefix):].strip()
            if len(suffix)>100:raise ValidationError('Titel-Zusatz ist zu lang.')
            selection['suffix']=suffix or None
        else:
            selection.update(template_id=None,template_version=None,suffix=None,values=[])
    proposal['definition']=selection


def reconcile_legacy_title(entry, title):
    template=entry.title_template
    if template:
        if title.casefold()==template.name.casefold() or title.casefold().startswith(template.name.casefold()+' '):
            entry.title_suffix=title[len(template.name):].strip()
            if len(entry.title_suffix)>100:raise ValidationError('Titel-Zusatz ist zu lang.')
        else:
            if entry.snapshot.get('extra_fields'):
                entry.snapshot.setdefault('_previous_extra_fields',[]).append({'title':entry.snapshot.get('title_template_name',''),'fields':entry.snapshot['extra_fields']})
            entry.title_template=None;entry.title_suffix='';entry.snapshot['extra_fields']=[r for r in entry.snapshot.get('extra_fields',[]) if r.get('scope')=='project']
    entry.title=title
