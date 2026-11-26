"""Invoice chronology uses the same EntryForm as the normal entry editor."""
from django.core.exceptions import ValidationError
from django.forms import HiddenInput

from .forms import EntryForm
from .models import Category, TitleTemplate
from .rules import parse_date


def make_review_entry_form(request, doc, values, entry, project):
    data = values.get('entry') or {}
    selection = values.get('definition') or {}
    if entry and not doc.edits.get('definition'):
        category, template, suffix = entry.category_id, entry.title_template_id, entry.title_suffix
        marks = entry.snapshot.get('extra_fields', [])
    else:
        category, template, suffix = selection.get('category_id'), selection.get('template_id'), selection.get('suffix')
        marks = [{'id':r['field_id'], 'value':r.get('value') or '', 'inferred':r.get('inferred',False)} for r in selection.get('values',[])]
    if project and category and not Category.objects.filter(pk=category,project=project).exists():
        category=template=None; suffix=''; marks=[]
    if project and not category:
        from .project_routing import suggested_kind
        from .categories import default_category
        guessed=default_category(project, data.get('kind') or suggested_kind(doc))
        category=guessed.pk if guessed else None
    initial = {'title':data.get('title') or '', 'category':category, 'title_template':template,
        'title_suffix':suffix or '', 'kind':data.get('kind') or '', 'status':data.get('status') or '',
        'date_text':data.get('date') or '', 'end_text':data.get('end_date') or '',
        'meter_text':data.get('meter') or '', 'meter_unit':data.get('meter_unit') or '',
        'meter_name':data.get('meter_name') or '', 'description':data.get('description') or '',
        'tags':data.get('tags') or '', 'cost_state':'unknown', 'token':doc.pk, 'version':entry.version if entry else 0}
    initial.update(oil=data.get('oil') or '',quantity=data.get('quantity_l') or '',filter=data.get('filter') or '')
    initial.update({key.removeprefix('chronicle-'):value for key,value in doc.review_draft.get('fields',{}).items() if key.startswith('chronicle-')})
    posted = request.POST if request.method=='POST' and 'chronicle-title_template' in request.POST and request.POST.get('action')!='entry_project' else None
    form=EntryForm(posted, prefix='chronicle', owner=request.user, entry=entry, project=project, initial=initial)
    # Invoice costs are reviewed once in the enclosing invoice form.
    for name in ('cost_state','amount','budget_text'):
        form.fields[name].widget=HiddenInput()
    form.definition_marks=marks
    for row in marks:
        name='extra_'+row['id'].replace('-','')
        if name in form.fields and 'chronicle-'+name not in doc.review_draft.get('fields',{}):form.initial[name]=row['value']
    return form


def merge_review_entry(form, proposal):
    if not form.is_valid():
        raise ValidationError('Chronikeintrag bitte prüfen: '+str(form.errors.as_text()))
    data=form.domain
    start=data['date'];precision=data['precision']
    proposal['entry']={key:data.get(key) or None for key in
        ('title','kind','status','description','meter_unit','meter_name','tags')}
    proposal['entry'].update(date=(start.strftime('%Y' if precision=='year' else '%Y-%m') if precision!='day' else start.isoformat()) if start else None,
        end_date=data['end_date'].isoformat() if data['end_date'] else None,
        meter=str(data['meter']) if data['meter'] is not None else None,
        **{key:data['snapshot'].get(key) or None for key in ('oil','quantity_l','filter')})
    proposal['definition']=data['_definition']
    return proposal
