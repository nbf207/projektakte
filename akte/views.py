import csv, io, json, hashlib, uuid, re
from pathlib import Path
from datetime import date
from decimal import Decimal
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction, connection
from django.db.models import Q,Sum
from django.http import FileResponse,JsonResponse,HttpResponse,HttpResponseBadRequest,Http404
from django.shortcuts import get_object_or_404,redirect,render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST, require_safe
from django.views.decorators.clickjacking import xframe_options_sameorigin
from .models import (AIUsage, Allocation, Asset, Category, CostSource, Document, DiscardedDraft, Draft, Entry, ExportRequest, ImportBatch, Job, Part, PartEvent, PhotoLink, Project, Revision, Step, Task, TitleTemplate)
from .forms import EntryForm, PartForm, ProjectForm, ProjectRoutingForm, ReviewForm, ScanForm, StepForm, TaskForm
from .services import (Conflict, accept_document, allocate, audit, change_deleted, confirm_import, decimal_input, ingest_asset, parse_date, receive_document, save_entry, validate_proposal)
from .documents import remote_signature
from .billing import budget_month_start
from .private_files import private_path
from .review_state import review_processing
from .adapters import paperless,IntegrationError
from .editors import ProjectFields,project_field_initial,project_fields_data,Positions,position_initial,position_data,raw_position_rows,raw_allocations

def owned_project(request,pk): return get_object_or_404(Project,pk=pk,owner=request.user)
def owned_entry(request,pk): return get_object_or_404(Entry.objects.select_related('project'),pk=pk,project__owner=request.user)
def err(exc): return '; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Vorgang konnte nicht abgeschlossen werden. Eingaben bitte prüfen.'
def form_page(request,title,form,**context): return render(request,'form.html',{'title':title,'form':form,**context})

@login_required
def home(request):
    from django.db.models import OuterRef, Subquery
    from .fields import ExactDecimalField
    from .i18n import effective_currency
    curr=effective_currency(request.user)
    eur_cost=Subquery(Allocation.objects.filter(project=OuterRef('pk'),source__currency=curr).values('project').annotate(s=Sum('amount')).values('s')[:1],output_field=ExactDecimalField(max_digits=14,decimal_places=2))
    projects=Project.objects.filter(owner=request.user).filter(Q(is_scan_inbox=False)|Q(entries__isnull=False,entries__deleted_at__isnull=True)).distinct().annotate(_annotated_cost=eur_cost).order_by('archived','position','-favorite','name')
    if request.GET.get('archiv')!='1':projects=projects.filter(archived=False)
    scans=Document.objects.filter(owner=request.user,deleted_at__isnull=True,entry__is_provisional=True,entry__deleted_at__isnull=True).exclude(capture_type='snap',status='accepted').select_related('entry','asset','project').order_by('-created')[:50]
    return render(request,'home.html',{'projects':projects,'scans':scans,'voice_token':uuid.uuid4(),'capture_projects':Project.objects.filter(owner=request.user,archived=False,is_scan_inbox=False).order_by('position','name'),'scan_form':ScanForm(),'pending':Document.objects.filter(owner=request.user,deleted_at__isnull=True).exclude(status='accepted').count(),'drafts':Draft.objects.filter(owner=request.user).select_related('project','entry'),'import_drafts':ImportBatch.objects.filter(owner=request.user,confirmed=False).select_related('project')})

@login_required
def project_edit(request,pk=None):
    project=owned_project(request,pk) if pk else None
    previous=project.fields.copy() if project else {}
    initial={'field_json':json.dumps(previous,ensure_ascii=False,indent=2),'version':project.version if project else 0,'field_editor':'fields'}
    form_data=request.POST.copy() if request.method=='POST' else None
    if form_data is not None and not form_data.get('field_editor'):
        form_data['field_editor']='expert' if 'fields-TOTAL_FORMS' not in form_data and form_data.get('field_json') else 'fields'
    form=ProjectForm(form_data,instance=project,initial=initial)
    fieldset=ProjectFields(request.POST if request.method=='POST' and 'fields-TOTAL_FORMS' in request.POST else None,initial=project_field_initial(previous),prefix='fields')
    expert=request.method=='POST' and (request.POST.get('field_editor')=='expert' or ('fields-TOTAL_FORMS' not in request.POST and bool(request.POST.get('field_json'))))
    valid=form.is_valid() if request.method=='POST' else False
    if valid and (expert or fieldset.is_valid()):
        try:
            with transaction.atomic():
                if project:
                    current=Project.objects.get(pk=pk)
                    if current.version!=form.cleaned_data['version']: raise Conflict('Projekt wurde zwischenzeitlich geändert. Bitte neu laden.')
                obj=form.save(commit=False);obj.owner=request.user
                if not project and form.cleaned_data.get('request_token'):
                    token=form.cleaned_data['request_token'];prior=Project.objects.filter(pk=token).first()
                    if prior:
                        if prior.owner_id!=request.user.pk:raise Http404
                        return redirect('project',pk=prior.pk)
                    obj.pk=token
                new_fields=form.cleaned_data['field_json'] if expert else project_fields_data(fieldset,previous)
                # Removing a definition retires it, preserving the stored value.
                if project:
                    for key,value in previous.items():
                        if key not in new_fields: new_fields[key]={**value,'active':False}
                if not project:
                    from .project_templates import initial_fields
                    new_fields={**initial_fields(obj.kind),**new_fields}
                obj.fields=new_fields;obj.version+=1;obj.save()
                if not project:
                    from .definitions import ensure_project_definitions
                    ensure_project_definitions(obj)
                audit(request.user,obj,'Stammdaten gespeichert',{'name':obj.name,'fields':obj.fields})
            return redirect('project',pk=obj.pk)
        except ValidationError as e: form.add_error(None,err(e))
    return render(request,'project_form.html',{'title':'Projekt bearbeiten' if project else 'Neues Projekt','form':form,'fieldset':fieldset,'project':project})

@login_required
def project_view(request,pk):
    project=owned_project(request,pk);tab=request.GET.get('tab','chronik')
    from .project_config import visible_tabs
    tabs=visible_tabs(project)
    if tab not in dict(tabs):tab=tabs[0][0] if tabs else 'chronik'
    if tab=='wartung':return redirect('project_maintenance',project_id=project.pk)
    entries=project.entries.filter(deleted_at__isnull=True).select_related('category').order_by('category__name','-date','-created','id')
    category=request.GET.get('category','')
    if category=='none': entries=entries.filter(category__isnull=True)
    elif category:
        try: entries=entries.filter(category_id=uuid.UUID(category))
        except ValueError: messages.error(request,'Ungültiger Kategorienfilter.')
    if request.GET.get('kind'): entries=entries.filter(kind=request.GET['kind'])
    if request.GET.get('status'): entries=entries.filter(status=request.GET['status'])
    if request.GET.get('tag'): entries=entries.filter(tags__icontains=request.GET['tag'])
    for arg,lookup in [('from','date__gte'),('to','date__lte')]:
        if request.GET.get(arg):
            try: entries=entries.filter(**{lookup:date.fromisoformat(request.GET[arg])})
            except ValueError: messages.error(request,'Ungültiger Datumsfilter.')
    q=request.GET.get('q','').strip()
    if q: entries=search_entries(entries,q)
    if tab=='umbauten': entries=entries.filter(kind='umbau')
    from .invoice_chronicle import pending_chronicles
    page_entries=Paginator(entries,30).get_page(request.GET.get('page'))
    chronicle_proposals=pending_chronicles(request.user,project) if tab=='chronik' else []
    if request.headers.get('HX-Request'):
        return render(request,'rows.html',{'project':project,'tab':tab,'entries':page_entries,'chronicle_proposals':chronicle_proposals,'return_path':request.get_full_path()})
    open_tasks=project.tasks.filter(done=False).count()
    overdue_tasks=project.tasks.filter(done=False,due__lt=timezone.localdate()).count()
    context={'project':project,'tab':tab,'entries':page_entries,'kinds':Entry.KINDS,'states':Entry.STATES,'pending':Document.objects.filter(owner=request.user,deleted_at__isnull=True).filter(Q(project=project)|Q(links__project=project)|Q(entry__project=project)).exclude(status='accepted').distinct().count(),'open_tasks':open_tasks,'overdue_tasks':overdue_tasks,'document_list':Document.objects.filter(owner=request.user,deleted_at__isnull=True).filter(Q(project=project)|Q(links__project=project)|Q(source__allocations__project=project)).distinct().order_by('-created')[:100],'costs':CostSource.objects.filter(owner=request.user,allocations__project=project).distinct().prefetch_related('allocations'),'tasks':project.tasks.select_related('entry','category').order_by('done','due'),'parts':project.parts.prefetch_related('events__entry'),'drafts':Draft.objects.filter(owner=request.user,project=project),'return_path':request.get_full_path(),'categories':Category.objects.filter(project=project,owner=request.user),'project_tabs':tabs,'chronicle_proposals':chronicle_proposals}
    return render(request,'project.html',context)

@login_required
def entry_edit(request,project_id=None,pk=None):
    entry=owned_entry(request,pk) if pk else None
    project=entry.project if entry else owned_project(request,project_id)
    token=request.POST.get('token') or request.GET.get('draft')
    draft=None
    if token:
        try: draft=Draft.objects.filter(owner=request.user,project=project,token=token).first()
        except (ValueError,ValidationError): raise Http404
        if draft and draft.entry_id!=(entry.pk if entry else None): raise Http404
    initial={'title':'\u00d6lwechsel' if project.kind=='auto' and request.GET.get('kind','wartung')=='wartung' else '', 'kind':request.GET.get('kind','wartung'),'date_text':timezone.localdate().isoformat(),'status':'geplant' if request.GET.get('kind')=='umbau' else 'erledigt','token':uuid.uuid4(),'version':0,'oil':''}
    if entry:
        date_text=entry.display_date if entry.precision in ('year','month') else (entry.date.isoformat() if entry.date else '')
        initial.update(title=entry.title,kind=entry.kind,date_text=date_text,end_text=entry.end_date.isoformat() if entry.end_date else '',status=entry.status,meter_text=str(entry.meter if entry.meter is not None else '').replace('.',','),meter_name=entry.meter_name,meter_unit=entry.meter_unit,meter_replaced=entry.meter_replaced,description=entry.description,cost_state=entry.cost_state,amount=str(entry.cost).replace('.',',') if entry.cost_state=='known' else '',budget_text=str(entry.budget).replace('.',',') if entry.budget is not None else '',oil=entry.snapshot.get('oil',''),quantity=entry.snapshot.get('quantity_l','').replace('.',','),filter=entry.snapshot.get('filter',''),tags=entry.tags,version=entry.version)
    if entry:
        from .i18n import effective_currency
        manual=entry.allocations.filter(source__document__isnull=True,source__currency=effective_currency(request.user)).select_related('source').first()
        if manual:initial['amount']=str(manual.source.amount).replace('.',',')
        initial.update(title_template=entry.title_template_id,title_suffix=entry.title_suffix)
    if draft: initial.update(draft.payload);initial['token']=draft.token
    from .categories import default_category
    initial['category']=entry.category_id if entry else (default_category(project,initial.get('kind')) or None)
    if draft and 'category' in draft.payload: initial['category']=draft.payload['category']
    if not entry and not draft and initial.get('category'):
        template=TitleTemplate.objects.filter(category=initial['category'],name=initial.get('title','')).first()
        initial['title_template']=template.pk if template else None
    simple=request.GET.get('simple')=='1' or request.POST.get('simple')=='1'
    if simple and not entry and not draft:
        initial.update(title='',kind='notiz',status='',category=default_category(project,'notiz'),title_template=None)
    form=EntryForm(request.POST or None,initial=initial,owner=request.user,entry=entry,project=project)
    if simple:form.fields['title'].label='Titel'
    if request.method=='POST' and form.is_valid():
        try:
            data=form.domain
            data['snapshot']={**(entry.snapshot if entry else {}),**data['snapshot'],'project_fields':(entry.snapshot.get('project_fields',project.fields) if entry else project.fields)}
            obj=save_entry(request.user,project,data,form.cleaned_data['token'],entry,form.cleaned_data['version'])
            messages.success(request,'Vorgang gespeichert. Historische Angaben bleiben diesem Eintrag zugeordnet.')
            from .project_config import meter_warning
            warning=meter_warning(obj)
            if warning:messages.warning(request,warning)
            return redirect('entry',pk=obj.pk)
        except ValidationError as e: form.add_error(None,err(e))
    form.primary_fields={'title','kind','date_text','status','meter_text','oil','quantity','filter','cost_state','amount'}
    selected_kind=form.cleaned_data.get('kind') if form.is_bound and not form.errors else initial.get('kind')
    if selected_kind=='umbau': form.primary_fields={'title','kind','date_text','end_text','status','budget_text','description','cost_state','amount'}
    if project.kind!='auto': form.primary_fields-={'oil','quantity','filter'}
    form.primary_fields.update({'category','title_template','title_suffix'})
    if entry:
        names={'date':'date_text','end_date':'end_text','meter':'meter_text','quantity_l':'quantity'}
        form.inferred_fields={names.get(key,key) for key in entry.inferred_fields}
        form.inferred_fields.update('extra_'+row['id'].replace('-','') for row in entry.snapshot.get('extra_fields',[]) if row.get('inferred'))
        if 'kind' in entry.inferred_fields:form.inferred_fields.add('category')
        if entry.title_template_id and 'title' in entry.inferred_fields:form.inferred_fields.add('title_template')
        if 'definition.suffix' in entry.snapshot.get('_definition_inferred',[]):form.inferred_fields.add('title_suffix')
    return form_page(request,'Vorgang bearbeiten' if entry else 'Neuer Vorgang',form,project=project,entry=entry,simple_entry=simple,autosave=True,draft_version=draft.version if draft else 0)

@login_required
@require_POST
def draft_save(request,project_id):
    project=owned_project(request,project_id)
    try:
        token=uuid.UUID(request.POST['token']);version=int(request.POST.get('draft_version',0))
        if DiscardedDraft.objects.filter(owner=request.user,token=token).exists():return JsonResponse({'error':'Entwurf wurde gelöscht.'},status=409)
        entry=owned_entry(request,request.POST['entry_id']) if request.POST.get('entry_id') else None
        if entry and entry.project_id!=project.pk: raise Http404
        payload={k:v for k,v in request.POST.items() if k not in ('csrfmiddlewaretoken','draft_version','entry_id')}
        with transaction.atomic():
            if Entry.objects.filter(project__owner=request.user,request_key=token).exists():
                return JsonResponse({'error':'Vorgang bereits bestätigt. Kein neuer Entwurf angelegt.'},status=409)
            if entry and Entry.objects.get(pk=entry.pk).version!=int(payload.get('version',0)):
                return JsonResponse({'error':'Vorgang inzwischen bestätigt oder verändert. Entwurf nicht überschrieben.'},status=409)
            draft=Draft.objects.filter(owner=request.user,token=token).first()
            if draft and (draft.project_id!=project.pk or draft.entry_id!=(entry.pk if entry else None)): raise Http404
            if draft and draft.version!=version: return JsonResponse({'error':'Entwurf wurde in einem anderen Fenster verändert. Nicht überschrieben.'},status=409)
            if not draft: draft=Draft(owner=request.user,token=token,project=project,entry=entry)
            else: draft.version+=1
            draft.payload=payload;draft.save()
        return JsonResponse({'version':draft.version,'message':'Entwurf gespeichert · noch nicht bestätigt'})
    except (ValueError,KeyError): return JsonResponse({'error':'Ungültiger Entwurf'},status=400)

@login_required
def entry_detail(request,pk):
    entry=owned_entry(request,pk)
    ret=request.GET.get('back','')
    if not url_has_allowed_host_and_scheme(ret,allowed_hosts={request.get_host()},require_https=request.is_secure()): ret=reverse('project',args=[entry.project_id])
    scan_doc=Document.objects.filter(owner=request.user,pk=entry.snapshot.get('_scan_document'),entry=entry).first() if entry.is_provisional else None
    merge_candidates=Entry.objects.filter(project=entry.project,deleted_at__isnull=True).exclude(pk=entry.pk).order_by('-date','-created')[:50] if not entry.deleted_at and not entry.project.is_scan_inbox else []
    return render(request,'entry.html',{'scan_doc':scan_doc,'review_processing':review_processing(scan_doc) if scan_doc else False,'route_form':ProjectRoutingForm(owner=request.user,initial={'version':scan_doc.version,'project':scan_doc.project_id}) if scan_doc else None,'entry':entry,'project':entry.project,'back':ret,'photo_token':uuid.uuid4(),'stepform':StepForm(),'partlist':entry.project.parts.all(),'assets':Asset.objects.filter(owner=request.user,mime__startswith='image/')[:100],'revisions':Revision.objects.filter(owner=request.user,entity=entry.pk).order_by('-created')[:20],'merge_candidates':merge_candidates})

@login_required
@require_POST
def entry_merge(request,pk):
    source=owned_entry(request,pk)
    target_id=request.POST.get('target','')
    try:
        target=owned_entry(request,uuid.UUID(target_id))
        source_version=int(request.POST['version']) if request.POST.get('version') else None
        from .entry_merge import merge_entries
        merged=merge_entries(request.user,source,target,source_version=source_version,reason='Manuell verbunden')
        messages.success(request,'Vorgang verbunden. Belege, Kosten und Medien liegen jetzt gemeinsam am Zielvorgang; der bisherige Eintrag liegt reversibel im Papierkorb.')
        return redirect('entry',pk=merged.pk)
    except (ValidationError,ValueError,TypeError) as e:
        messages.error(request,err(e))
        return redirect('entry',pk=source.pk)

@login_required
def entry_delete(request,pk):
    entry=owned_entry(request,pk)
    if request.method=='GET': return render(request,'delete_confirm.html',{'entry':entry,'project':entry.project})
    if request.method!='POST': return HttpResponseBadRequest('Ungültige Methode.')
    try: change_deleted(request.user,entry,int(request.POST.get('version',0)),restore=request.POST.get('restore')=='1')
    except ValidationError as e: messages.error(request,err(e))
    return redirect('trash' if request.POST.get('restore') else 'project',**({} if request.POST.get('restore') else {'pk':entry.project_id}))

@login_required
def trash(request):
    return render(request,'trash.html',{'documents':Document.objects.filter(owner=request.user,deleted_at__isnull=False).select_related('asset','project').order_by('-deleted_at'),'entries':Entry.objects.filter(project__owner=request.user,deleted_at__isnull=False).select_related('project'),'photos':PhotoLink.objects.filter(entry__project__owner=request.user,deleted_at__isnull=False).select_related('entry','asset')})

@login_required
def step_edit(request,entry_id,pk=None):
    entry=owned_entry(request,entry_id)
    step=get_object_or_404(Step,pk=pk,entry=entry) if pk else None
    form=StepForm(request.POST or None,instance=step,owner=request.user,entry=entry)
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            current=Entry.objects.get(pk=entry.pk)
            if step and current.version!=int(request.POST.get('version',0)): form.add_error(None,'Vorgang wurde zwischenzeitlich geändert. Bitte neu laden.')
            else:
                target=form.cleaned_data.get('target') or entry
                if step and target.pk!=entry.pk and Allocation.objects.filter(step=step).exists():
                    form.add_error('target','Schritt besitzt Kostenzuordnungen. Diese zuerst ausdrücklich umordnen.')
                    return form_page(request,'Arbeitsschritt',form,project=entry.project,entry=entry,version=entry.version)
                obj=form.save(commit=False);obj.entry=target;obj.save();current.version+=1;current.save(update_fields=['version'])
                if target.pk!=entry.pk:
                    PhotoLink.objects.filter(step=obj).update(entry=target)
                    target.version+=1;target.save(update_fields=['version'])
                audit(request.user,current,'Arbeitsschritt gespeichert',{'step':str(obj.pk),'title':obj.title,'text':obj.text,'position':obj.position})
                return redirect(reverse('entry',args=[target.pk])+f'#step-{obj.pk}')
    return form_page(request,'Arbeitsschritt',form,project=entry.project,entry=entry,version=entry.version)

@login_required
@require_POST
def photo_action(request,entry_id):
    entry=owned_entry(request,entry_id)
    try:
        if request.POST.get('link_id'):
            link=get_object_or_404(PhotoLink,pk=request.POST['link_id'],entry=entry)
            if request.POST.get('action')=='trash': link.deleted_at=timezone.now()
            elif request.POST.get('action')=='restore': link.deleted_at=None
            else:
                link.caption=request.POST.get('caption','')[:300];link.position=int(request.POST.get('position',0));link.role=request.POST.get('role','')[:10]
                link.rotation=(link.rotation+90)%360 if request.POST.get('action')=='rotate' else link.rotation
                link.taken_on=parse_date(request.POST.get('taken_on'))[0]
            if 'version' in request.POST and link.version!=int(request.POST['version']): raise Conflict('Bildzuordnung zwischenzeitlich geändert. Bitte neu laden.')
            link.version+=1;link.save()
            audit(request.user,link,'Bildzuordnung geändert',{'caption':link.caption,'rotation':link.rotation,'deleted':bool(link.deleted_at)})
        else:
            step=get_object_or_404(Step,pk=request.POST['step'],entry=entry) if request.POST.get('step') else None
            assets=[get_object_or_404(Asset,pk=request.POST['asset'],owner=request.user,mime__startswith='image/')] if request.POST.get('asset') else (ingest_asset(request.user,f) for f in request.FILES.getlist('photos'))
            for i,asset in enumerate(assets): PhotoLink.objects.get_or_create(request_key=uuid.uuid5(uuid.UUID(request.POST['token']),str(i)) if request.POST.get('token') else uuid.uuid4(),defaults=dict(asset=asset,entry=entry,step=step,caption=request.POST.get('caption','')[:300],position=int(request.POST.get('position',0))+i,role=request.POST.get('role','')[:10]))
        messages.success(request,'Bildzuordnung gespeichert. Originaldatei bleibt erhalten.')
    except (ValidationError,ValueError) as e: messages.error(request,err(e))
    return redirect(reverse('entry',args=[entry.pk])+'#bilder')

@login_required
@xframe_options_sameorigin
@require_safe
def media_file(request,pk):
    asset=get_object_or_404(Asset,pk=pk,owner=request.user)
    thumb=request.GET.get('thumb') and asset.thumbnail
    f=asset.thumbnail if thumb else asset.file
    path=private_path('media',f.name)
    response=FileResponse(path.open('rb'),content_type='image/jpeg' if thumb else asset.mime,as_attachment=request.GET.get('download')=='1',filename=asset.name)
    return response

@login_required
def part_edit(request,project_id,pk=None):
    project=owned_project(request,project_id);part=get_object_or_404(Part,pk=pk,project=project) if pk else None
    form=PartForm(request.POST or None,instance=part,owner=request.user)
    if request.method=='POST' and form.is_valid():
        if part and Part.objects.get(pk=part.pk).version!=form.cleaned_data['version']:
            form.add_error(None,'Teil zwischenzeitlich geändert. Bitte neu laden.')
            return form_page(request,'Teil bearbeiten',form,project=project)
        obj=form.save(commit=False);obj.project=project
        if not part and form.cleaned_data.get('request_token'):
            token=form.cleaned_data['request_token'];prior=Part.objects.filter(pk=token).first()
            if prior:
                if prior.project.owner_id!=request.user.pk:raise Http404
                return redirect(reverse('project',args=[prior.project_id])+'?tab=teile')
            obj.pk=token
        obj.version+=1;obj.save();form.save_m2m();audit(request.user,obj,'Teil gespeichert',{'name':obj.name,'number':obj.number,'state':obj.state});return redirect(reverse('project',args=[project.pk])+'?tab=teile')
    return form_page(request,'Teil bearbeiten' if part else 'Neues Teil',form,project=project)

@login_required
@require_POST
def part_event(request,entry_id):
    entry=owned_entry(request,entry_id);part=get_object_or_404(Part,pk=request.POST.get('part'),project=entry.project)
    try:
        dt,precision=parse_date(request.POST.get('date'))
        action=request.POST.get('action')
        if not dt or precision!='day' or action not in ('eingebaut','ausgebaut'): raise ValidationError('Einbau/Ausbau mit genauem Datum angeben.')
        with transaction.atomic():
            PartEvent.objects.create(part=part,entry=entry,action=action,date=dt,snapshot={'name':part.name,'number':part.number,'manufacturer':part.manufacturer,'meter':str(entry.meter) if entry.meter is not None else None,'meter_unit':entry.meter_unit,'meter_name':entry.meter_name,'documents':[str(pk) for pk in part.documents.values_list('pk',flat=True)]})
            part.state=action;part.save(update_fields=['state'])
        messages.success(request,'Einbauhistorie mit damaligen Teilewerten gespeichert.')
    except ValidationError as e: messages.error(request,err(e))
    return redirect('entry',pk=entry.pk)

@login_required
def tasks(request,project_id=None,pk=None):
    project=owned_project(request,project_id) if project_id else None
    task=get_object_or_404(Task,pk=pk,project__owner=request.user) if pk else None
    projects=Project.objects.filter(owner=request.user)
    target_project=project or (task.project if task else None)
    if target_project is None and request.method=='GET':
        target_project=get_object_or_404(projects,pk=request.GET['project']) if request.GET.get('project') else projects.first()
    if request.method=='POST' and target_project is None:
        target_project=get_object_or_404(projects,pk=request.POST.get('project'))
    form=TaskForm(request.POST or None,instance=task,owner=request.user,project=target_project)
    if request.method=='POST' and form.is_valid():
        obj=form.save(commit=False);obj.project=project or (task.project if task else get_object_or_404(projects,pk=request.POST.get('project')))
        if not task and form.cleaned_data.get('request_token'):
            token=form.cleaned_data['request_token'];prior=Task.objects.filter(pk=token).first()
            if prior:
                if prior.project.owner_id!=request.user.pk:raise Http404
                return redirect('tasks')
            obj.pk=token
        if obj.entry and obj.entry.project_id!=obj.project_id: form.add_error('entry','Vorgang gehört zu einem anderen Projekt.')
        elif task and Task.objects.get(pk=task.pk).version!=form.cleaned_data['version']: form.add_error(None,'Aufgabe zwischenzeitlich geändert. Bitte neu laden.')
        else: obj.version+=1;obj.save();audit(request.user,obj,'Aufgabe gespeichert',{'title':obj.title,'done':obj.done});return redirect('tasks')
    return render(request,'tasks.html',{'title':'Aufgaben','form':form,'projects':projects,'project':project,'selected_project':target_project,'choose_project':not project_id and not pk,'tasks':Task.objects.filter(project__owner=request.user).select_related('project','entry','category').order_by('done','due')})

@login_required
def inbox(request):
    return render(request,'inbox.html',{'documents':Document.objects.filter(owner=request.user,deleted_at__isnull=True).select_related('asset','project','entry').order_by('-created')[:100]})

@login_required
def document_upload(request,project_id=None,entry_id=None):
    entry=owned_entry(request,entry_id) if entry_id else None
    project=entry.project if entry else (owned_project(request,project_id) if project_id else None)
    records=[]
    scan_form=ScanForm(request.POST if request.method=='POST' else None)
    if request.method=='POST':
        try:
            if not scan_form.is_valid(): raise ValidationError('Zusatztext bitte prüfen (höchstens 1.000 Zeichen).')
            note=scan_form.cleaned_data['scan_note']
            auto_route=not project and not entry and scan_form.cleaned_data['auto_route']
            if request.POST.get('existing_id'):
                doc_id=int(request.POST['existing_id']);data=paperless().read(doc_id)
                if int(data.get('id',0))!=doc_id: raise ValidationError('Dokumentidentität stimmt nicht überein.')
                with transaction.atomic():
                    doc,new=Document.objects.get_or_create(owner=request.user,instance='demo' if settings.DEMO_MODE else settings.PAPERLESS_URL,paperless_id=doc_id,defaults={'project':project,'entry':entry,'scan_note':note,'auto_route':bool(auto_route),'ocr':data.get('content') or '','verified_at':timezone.now(),'status':'ai','remote_signature':remote_signature(data),'remote_state':'current'})
                    if doc.deleted_at: raise ValidationError('Beleg liegt im Papierkorb. Dort wiederherstellen.')
                    if new: Job.objects.create(document=doc,stage='ai')
                if settings.DEMO_MODE and new:
                    from .demo import synthetic_pdf
                    from django.core.files.uploadedfile import SimpleUploadedFile
                    doc.asset=ingest_asset(request.user,SimpleUploadedFile(f'bestand-{doc_id}.pdf',synthetic_pdf(data.get('content') or 'Synthetisches Bestandsdokument')),document=True);doc.save(update_fields=['asset'])
            elif request.FILES.getlist('document') or request.FILES.getlist('camera'):
                from .files import receive_bundle
                doc,new=receive_bundle(request.user,request.FILES.getlist('camera')+request.FILES.getlist('document'),project,entry,note=note,auto_route=auto_route,create_entry=True)
            else: raise ValidationError('Datei oder vorhandenes Paperless-Dokument auswählen.')
            from .files import link_document
            from .scan_entries import ensure_scan_entry
            doc=ensure_scan_entry(doc)
            link_document(doc,project,entry)
            messages.success(request,'Beleg und Eintrag dauerhaft gespeichert. Verarbeitung läuft im Hintergrund; du kannst das Handy jetzt weglegen.' if new else 'Bereits vorhandener Beleg gefunden. Es wurde keine zweite Kostenquelle angelegt.')
            return redirect('entry',pk=doc.entry_id) if doc.entry_id else redirect('review',pk=doc.pk)
        except (ValidationError,IntegrationError,ValueError) as e:
            if request.headers.get('X-Requested-With')=='XMLHttpRequest': return JsonResponse({'error':err(e)},status=400)
            messages.error(request,err(e))
    if request.GET.get('q'):
        try: records=paperless().search(request.GET['q'])
        except IntegrationError as e: messages.error(request,str(e))
    return render(request,'upload.html',{'project':project,'entry':entry,'records':records,'scan_form':scan_form},status=400 if request.method=='POST' else 200)

@login_required
def document_delete(request,pk):
    doc=get_object_or_404(Document,pk=pk,owner=request.user)
    if request.method=='GET': return render(request,'delete_confirm.html',{'doc':doc,'project':doc.project})
    if request.method!='POST': return HttpResponseBadRequest('Ungültige Methode.')
    try:
        from .document_trash import change_document_deleted
        restore=request.POST.get('restore')=='1'
        change_document_deleted(request.user,doc,int(request.POST.get('version',0)),restore=restore)
        messages.success(request,'Beleg wiederhergestellt.' if restore else 'Beleg im Papierkorb. Bereits gebuchte Kosten und Paperless-Original bleiben erhalten.')
        return redirect('review',pk=doc.pk) if restore else redirect('inbox')
    except (ValidationError,ValueError) as exc:
        messages.error(request,err(exc));return redirect('review',pk=doc.pk)

@login_required
@require_POST
def document_route(request,pk):
    doc=get_object_or_404(Document,pk=pk,owner=request.user)
    form=ProjectRoutingForm(request.POST,owner=request.user)
    if form.is_valid():
        try:
            from .project_routing import assign_project
            assign_project(request.user,doc,form.cleaned_data['project'],form.cleaned_data['version'])
            messages.success(request,'Projekt zugeordnet. Chronikvorschlag und Kostenziele bitte prüfen; keine Kosten übernommen.')
        except ValidationError as exc: messages.error(request,err(exc))
    else: messages.error(request,'Aktives eigenes Projekt auswählen; bei Änderung Seite neu laden.')
    return redirect('review',pk=doc.pk)

def allocation_rows(request):
    rows=[]
    steps=request.POST.getlist('alloc_step')
    projects=request.POST.getlist('alloc_project');entries=request.POST.getlist('alloc_entry');amounts=request.POST.getlist('alloc_amount');refs=request.POST.getlist('alloc_ref')
    if (steps and len(steps)!=len(amounts)) or len(projects)!=len(amounts) or (entries and len(entries)!=len(amounts)) or (refs and len(refs)!=len(amounts)):
        raise ValidationError('Kostenzuordnungen sind unvollständig. Alle Zeilen erneut prüfen.')
    for i,(project_id,amount) in enumerate(zip(projects,amounts)):
        if not amount.strip(): continue
        project=owned_project(request,project_id)
        entry=owned_entry(request,entries[i]) if i<len(entries) and entries[i] else None
        step=get_object_or_404(Step,pk=steps[i],entry__project__owner=request.user) if steps and steps[i] else None
        rows.append({'project':project,'entry':entry,'step':step,'amount':decimal_input(amount,money=True),'position_ref':refs[i][:50] if i<len(refs) else ''})
    return rows

@login_required
def review(request,pk):
    doc=get_object_or_404(Document.objects.select_related('asset','entry','project'),pk=pk,owner=request.user)
    if doc.deleted_at: return render(request,'document_trashed.html',{'doc':doc,'project':doc.project})
    if doc.capture_type=='snap': return redirect('capture_detail',pk=doc.pk)
    if request.method=='POST' and request.POST.get('action')=='parts':
        try:
            from .documents import create_parts
            create_parts(doc,owned_project(request,request.POST.get('parts_project')),request.POST.getlist('parts_position'))
            messages.success(request,'Ausgewählte Teile vorbereitet. Kein Einbau bestätigt.')
        except (ValidationError,ValueError) as exc: messages.error(request,err(exc))
        return redirect('review',pk=doc.pk)
    if request.method=='POST' and doc.status=='accepted' and request.POST.get('action')!='chronicle':
        messages.success(request,'Dieser Beleg wurde bereits einmal übernommen. Keine weitere Buchung erzeugt.')
        return redirect('review',pk=doc.pk)
    values={**doc.proposal,**doc.edits}
    from .scan_entries import own_scan_entry, entry_values
    provisional=own_scan_entry(doc)
    if provisional and 'entry' not in doc.edits:
        values['entry']=entry_values(provisional)
        values['inferred_fields']=[field for field in values.get('inferred_fields',[]) if not field.startswith('entry.')] + ['entry.'+field for field in provisional.inferred_fields]
    if not isinstance(values.get('positions',[]),list) or any(not isinstance(item,dict) for item in values.get('positions',[])):
        messages.warning(request,'Gespeicherte Positionsdaten sind ungültig. Bitte mit Original und Expertenansicht vergleichen und korrigieren.')
    initial={**values,'version':doc.version,'draft_version':doc.review_version}
    from .invoice_chronicle import entry_initial, attach_accepted
    from .proposal_schema import retained_inferences
    initial.update(entry_initial(values),chronicle_mode='existing' if doc.entry_id else ('create' if doc.project_id else 'document'),chronicle_project=doc.project_id,chronicle_entry=doc.entry_id)
    for field in ('gross_total','net_total','tax_total','odometer_km'):
        if initial.get(field) is not None: initial[field]=str(initial[field]).replace('.',',')
    initial['positions']=json.dumps(values.get('positions',[]),ensure_ascii=False,indent=2) if not isinstance(values.get('positions'),str) else values['positions']
    ai_initial=initial.copy()
    if doc.review_draft:
        initial.update(doc.review_draft.get('fields',{}));initial['version']=doc.version;initial['draft_version']=doc.review_version
    form_data=request.POST.copy() if request.method=='POST' else None
    if form_data is not None and not form_data.get('positions_editor'):
        form_data['positions_editor']='expert' if 'positions-TOTAL_FORMS' not in form_data and 'positions' in form_data else 'rows'
    form=ReviewForm(form_data,initial=initial,owner=request.user)
    from .review_entry_form import make_review_entry_form, merge_review_entry
    entry_project=doc.entry.project if doc.entry_id and not doc.entry.project.is_scan_inbox else doc.project
    if entry_project and entry_project.is_scan_inbox: entry_project=None
    selected_project=request.POST.get('chronicle_project') if request.method=='POST' else request.GET.get('entry_project')
    if not entry_project and not selected_project:
        from .project_routing import suggested_project
        entry_project=suggested_project(doc)
    if request.method == 'POST' and request.POST.get('simple_review') == '1':
        form_data['chronicle_mode']='existing' if doc.entry_id else 'create'
        form_data['chronicle_entry']=str(doc.entry_id or '')
        form=ReviewForm(form_data,initial=initial,owner=request.user)
    if selected_project:entry_project=get_object_or_404(Project,pk=selected_project,owner=request.user,is_scan_inbox=False)
    entry_form=make_review_entry_form(request,doc,values,provisional,entry_project)
    if request.method == 'GET' and request.GET.get('entry_project'):
        from django.template.loader import render_to_string
        return JsonResponse({'html':render_to_string('entry_fields.html',{'form':entry_form,'compact_review':True},request=request)})
    form.initial['chronicle_project']=entry_project.pk if entry_project else None
    if provisional:
        form.fields['chronicle_mode'].choices=[('existing','Diesen Eintrag prüfen und behalten')]
        form.fields['chronicle_entry'].queryset=Entry.objects.filter(pk=provisional.pk)
    form.inferred_fields=set()
    for path in values.get('inferred_fields',[]):
        if not path.startswith('positions.'):
            name=path.replace('entry.','entry_')
            if name in form.fields and str(form[name].value() or '')==str(ai_initial.get(name) or ''):
                form.inferred_fields.add(name)
    pos_initial=position_initial(values.get('positions',[])) if isinstance(values.get('positions',[]),list) else []
    if 'position_rows' in doc.review_draft: pos_initial=doc.review_draft['position_rows']
    elif doc.review_draft.get('fields',{}).get('positions'):
        try: pos_initial=position_initial(json.loads(doc.review_draft['fields']['positions']))
        except (ValueError,TypeError,AttributeError): pass
    position_set=Positions(request.POST if request.method=='POST' and 'positions-TOTAL_FORMS' in request.POST else None,initial=pos_initial,prefix='positions')
    for index,row in enumerate(position_set):
        row.inferred_fields={path.split('.')[-1] for path in values.get('inferred_fields',[]) if path.startswith(f'positions.{index}.')}
    expert=request.method=='POST' and (request.POST.get('positions_editor')=='expert' or ('positions-TOTAL_FORMS' not in request.POST and 'positions' in request.POST))
    valid=form.is_valid() if request.method=='POST' else False
    if valid and request.POST.get('action')!='entry_project' and (request.POST.get('action')=='chronicle' or expert or position_set.is_valid()):
        try:
            p=form.proposal([] if request.POST.get('action')=='chronicle' else (None if expert else position_data(position_set)))
            from .definitions import retain_review_definition
            retain_review_definition(p,values)
            if 'chronicle-title_template' in request.POST and (provisional or not doc.entry_id) and form.chronicle_choice(doc)['mode']!='document':
                merge_review_entry(entry_form,p)
            p['inferred_fields']=retained_inferences(values,p)
            chronicle=form.chronicle_choice(doc)
            if request.POST.get('simple_review') == '1' and entry_project and doc.project_id != entry_project.pk:
                from .project_routing import assign_project
                doc=assign_project(request.user,doc,entry_project,form.cleaned_data['version'])
                form.cleaned_data['version']=doc.version
            if request.POST.get('action')=='save':
                validate_proposal(p)
                with transaction.atomic():
                    current=Document.objects.get(pk=doc.pk)
                    if current.version!=form.cleaned_data['version']: raise Conflict('Zwischenzeitliche Änderung erkannt. Neu laden.')
                    if current.status=='accepted': raise ValidationError('Beleg bereits übernommen.')
                    current.edits=p
                    current.review_draft={'allocations':raw_allocations(request.POST),'connect':request.POST.get('connect',''),'fields':{'keep_unallocated':form.cleaned_data['keep_unallocated'],'positions_editor':form.cleaned_data.get('positions_editor') or 'rows',**{key:request.POST.get(key,'') for key in ('chronicle_mode','chronicle_project','chronicle_entry')}}}
                    if not expert: current.review_draft['position_rows']=raw_position_rows(request.POST)
                    current.version+=1;current.save(update_fields=['edits','review_draft','version']);audit(request.user,current,'Prüfkorrektur',p)
                messages.success(request,'Korrekturen gespeichert. Noch keine Kosten gebucht.')
            elif request.POST.get('action')=='chronicle' and doc.status=='accepted':
                attach_accepted(request.user,doc,p,chronicle,form.cleaned_data['version'])
                messages.success(request,'Chronik ergänzt. Bestehende Kosten unverändert; keine neue Buchung.')
            else:
                connect=get_object_or_404(CostSource,pk=request.POST['connect'],owner=request.user,document__isnull=True) if request.POST.get('connect') else None
                rows=allocation_rows(request)
                unallocated=form.cleaned_data['keep_unallocated']
                if unallocated and connect: raise ValidationError('Eine vorhandene Ausgabe verbinden und vollständig unzugeordnet lassen schließen sich aus.')
                if unallocated: rows=[]
                if not connect and not rows and p.get('gross_total') is not None and Decimal(p['gross_total'])!=0 and not unallocated:
                    raise ValidationError('Bitte Kostenziel und Betrag auswählen oder „Betrag zunächst vollständig unzugeordnet lassen“ ausdrücklich bestätigen.')
                if not settings.DEMO_MODE:
                    from .documents import sync_document
                    doc=sync_document(doc)
                source=accept_document(request.user,doc,p,rows,expected_version=form.cleaned_data['version'],connect=connect,allow_similar=form.cleaned_data['separate'],chronicle=chronicle)
                messages.success(request,f'Einmal übernommen. Offener Rest: {source.remainder:.2f} {source.currency}.')
            if doc.entry_id:
                from .project_config import meter_warning
                warning=meter_warning(Entry.objects.get(pk=doc.entry_id))
                if warning:messages.warning(request,warning)
            return redirect('review',pk=doc.pk)
        except ValidationError as e: form.add_error(None,err(e))
    manual=CostSource.objects.filter(owner=request.user,document__isnull=True)
    if doc.entry_id: manual=manual.filter(allocations__entry=doc.entry).distinct()
    manual=list(manual)
    target=entry_project
    saved=doc.review_draft.get('allocations',[])
    selected_connect=doc.review_draft.get('connect','')
    suggestion=''
    if request.method=='POST':
        saved=raw_allocations(request.POST);selected_connect=request.POST.get('connect','')
    elif not doc.review_draft and doc.status!='accepted':
        from .i18n import effective_currency
        amount=values.get('gross_total');currency=values.get('currency') or effective_currency(request.user)
        candidates=[source for source in manual if source.currency==currency and amount is not None and source.amount==Decimal(amount)]
        if len(candidates)==1 and doc.entry_id:
            selected_connect=str(candidates[0].pk)
            suggestion='Die passende vorhandene Ausgabe ist vorausgewählt. Beim Übernehmen bleibt ihre Zuordnung erhalten; der Beleg ergänzt diese Ausgabe.'
        elif target:
            saved=[{'project_id':str(target.pk),'entry_id':str(doc.entry_id) if doc.entry_id else '', 'amount':str(amount).replace('.',',') if amount is not None else '', 'position_ref':''}]
            suggestion='Das bekannte Ziel und der erkannte Betrag sind vorausgewählt. Bitte prüfen und bei Bedarf aufteilen.'
    for source in manual: source.allocated_amount=source.amount-source.remainder
    processing=review_processing(doc)
    if doc.status=='review' and doc.proposal and 'entry' not in values and not processing:
        messages.info(request,'Dieser ältere Vorschlag enthält noch keine Chronikfelder. „Neu auswerten“ ergänzt sie mit dem neuen KI-Schema; vorhandene Korrekturen bleiben erhalten.')
    return render(request,'review.html',{'entry_form':entry_form,'doc':doc,'provisional_entry':provisional,'review_processing':processing,'route_form':ProjectRoutingForm(owner=request.user,initial={'version':doc.version,'project':doc.project_id}),'project':target,'form':form,'position_set':position_set,'manual':manual,'projects':Project.objects.filter(owner=request.user,is_scan_inbox=False),'all_steps':Step.objects.filter(entry__project__owner=request.user,entry__deleted_at__isnull=True).select_related('entry'),'all_entries':Entry.objects.filter(project__owner=request.user,deleted_at__isnull=True).select_related('project'),'allocations':saved,'saved_connect':selected_connect,'suggestion':suggestion,'alloc_rows':range(0 if saved else 1)})

@login_required
@require_POST
def document_action(request,pk):
    doc=get_object_or_404(Document,pk=pk,owner=request.user)
    try:
        from .documents import schedule_document
        schedule_document(doc,request.POST.get('action'),with_original=request.POST.get('ai_with_original')=='1',existing_id=request.POST.get('existing_id'),identity_checked=request.POST.get('identity_checked')=='1')
        messages.success(request,'Abgleich bzw. Auftrag gespeichert. Bestätigte Kosten und Korrekturen bleiben erhalten.')
    except (ValidationError,IntegrationError) as e: messages.error(request,err(e))
    return redirect('review',pk=doc.pk)

@login_required
def document_status(request,pk):
    doc=get_object_or_404(Document,pk=pk,owner=request.user)
    if doc.deleted_at: return JsonResponse({'status':'trash','label':'Im Papierkorb','error':'','processing':False})
    processing=review_processing(doc)
    return JsonResponse({'status':doc.status,'label':'Wird verarbeitet' if processing else doc.get_status_display(),'error':doc.error,'processing':processing})

@login_required
@require_POST
def review_draft_save(request,pk):
    doc=get_object_or_404(Document,pk=pk,owner=request.user)
    try:
        version=int(request.POST.get('version',0))
        fields={k:v for k,v in request.POST.items() if (k in ReviewForm.base_fields or k.startswith('chronicle-')) and k not in ('version','draft_version')}
        allocations=raw_allocations(request.POST)
        with transaction.atomic():
            current=Document.objects.get(pk=doc.pk)
            if current.deleted_at or current.status=='accepted' or current.version!=version or current.review_version!=int(request.POST.get('draft_version',0)): return JsonResponse({'error':'Zwischenzeitliche Änderung erkannt. Eingabe nicht überschrieben.'},status=409)
            current.review_draft={'fields':fields,'allocations':allocations,'connect':request.POST.get('connect','')}
            if 'positions-TOTAL_FORMS' in request.POST: current.review_draft['position_rows']=raw_position_rows(request.POST)
            current.review_version+=1;current.save(update_fields=['review_draft','review_version'])
        return JsonResponse({'version':current.version,'draft_version':current.review_version,'message':'Prüfentwurf gespeichert · noch nicht übernommen'})
    except ValueError: return JsonResponse({'error':'Ungültige Version'},status=400)

@login_required
def cost_edit(request,pk=None,project_id=None):
    source=get_object_or_404(CostSource,pk=pk,owner=request.user) if pk else None
    project=owned_project(request,project_id) if project_id else None
    if request.method=='POST':
        try:
            amount=decimal_input(request.POST.get('amount'),money=True)
            if amount is None: raise ValidationError('Betrag fehlt.')
            from .i18n import effective_currency
            currency=request.POST.get('currency',effective_currency(request.user)).upper()
            if not re.fullmatch('[A-Z]{3}',currency): raise ValidationError('Ungültige Währung.')
            with transaction.atomic():
                if source:
                    source=CostSource.objects.get(pk=source.pk)
                    if source.version!=int(request.POST.get('version',0)): raise Conflict('Kostenquelle zwischenzeitlich geändert. Neu laden und vergleichen.')
                    if source.document_id and (source.amount!=amount or source.currency!=currency): raise ValidationError('Bestätigte Rechnung unverändert lassen. Korrektur als eigene Gutschrift erfassen.')
                else:
                    token=uuid.UUID(request.POST['token'])
                    source=CostSource.objects.filter(owner=request.user,request_key=token).first()
                    if source: return redirect('cost',pk=source.pk)
                    source=CostSource(owner=request.user,request_key=token)
                source.label=request.POST.get('label','Ausgabe')[:200];source.amount=amount;source.currency=currency;source.version+=1;source.save()
                allocate(request.user,source,allocation_rows(request))
            messages.success(request,'Kostenquelle und Zuordnungen gespeichert.')
            return redirect('cost',pk=source.pk)
        except (ValidationError,ValueError,KeyError) as e: messages.error(request,err(e))
    return render(request,'cost.html',{'source':source,'project':project,'projects':Project.objects.filter(owner=request.user),'all_entries':Entry.objects.filter(project__owner=request.user,deleted_at__isnull=True),'allocations':source.allocations.all() if source else [],'token':uuid.uuid4(),'alloc_rows':range(4)})

def search_entries(entries,q):
    from .search import indexed_ids
    matches=indexed_ids(entries,'entry',q)
    if matches is not None: return entries.filter(pk__in=matches)
    return entries.filter(Q(title__icontains=q)|Q(description__icontains=q)|Q(tags__icontains=q)|Q(snapshot__icontains=q)|Q(steps__text__icontains=q)|Q(steps__title__icontains=q)|Q(photos__caption__icontains=q,photos__deleted_at__isnull=True)|Q(part_events__snapshot__icontains=q)|Q(documents__ocr__icontains=q,documents__deleted_at__isnull=True)|Q(documents__scan_note__icontains=q,documents__deleted_at__isnull=True)|Q(documentlink__document__ocr__icontains=q,documentlink__document__deleted_at__isnull=True)|Q(allocations__source__document__ocr__icontains=q,allocations__source__document__deleted_at__isnull=True)).distinct()

@login_required
def search(request):
    q=request.GET.get('q','').strip()[:200]
    entries=search_entries(Entry.objects.filter(project__owner=request.user,deleted_at__isnull=True),q) if q else Entry.objects.none()
    docs=Document.objects.filter(owner=request.user,deleted_at__isnull=True).filter(Q(ocr__icontains=q)|Q(scan_note__icontains=q)|Q(proposal__icontains=q)|Q(edits__icontains=q)) if q else Document.objects.none()
    from .search import indexed_ids
    if q:
        document_matches=indexed_ids(docs,'document',q)
        if document_matches is not None: docs=Document.objects.filter(owner=request.user,deleted_at__isnull=True,pk__in=document_matches)
    page=Paginator(entries.select_related('project'),30).get_page(request.GET.get('page'))
    for entry in page:
        step=entry.steps.filter(Q(title__icontains=q)|Q(text__icontains=q)).first()
        entry.match_anchor='#step-'+str(step.pk) if step else ('#bilder' if entry.photo_links.filter(caption__icontains=q).exists() else '')
    parts=Part.objects.filter(project__owner=request.user).filter(Q(name__icontains=q)|Q(number__icontains=q)|Q(notes__icontains=q))[:30] if q else Part.objects.none()
    if q:
        part_matches=indexed_ids(Part.objects.all(),'part',q)
        if part_matches is not None: parts=Part.objects.filter(project__owner=request.user,pk__in=part_matches)[:30]
    return render(request,'search.html',{'query':q,'entries':page,'documents':docs[:30],'parts':parts})

@login_required
def import_view(request,project_id,pk=None):
    project=owned_project(request,project_id);batch=get_object_or_404(ImportBatch,pk=pk,owner=request.user,project=project) if pk else None
    combined={**project.fields,**(batch.proposed_fields if batch else {})}
    field_initial=project_field_initial(combined)
    for row in field_initial:
        if row['original_name'] not in project.fields: row['original_name']=''
    fields=ProjectFields(request.POST if request.method=='POST' and 'importfields-TOTAL_FORMS' in request.POST else None,initial=field_initial,prefix='importfields')
    if request.method=='POST':
        try:
            if batch:
                rows=json.loads(request.POST.get('rows','[]'))
                if not isinstance(rows,list) or not 0<len(rows)<=1000: raise ValidationError('Import braucht 1–1000 Zeilen.')
                if request.POST.get('apply_fields') and not fields.is_valid(): raise ValidationError('Stammdatenfelder prüfen.')
                with transaction.atomic():
                    if not batch.confirmed:
                        if request.POST.get('apply_fields'):
                            current=Project.objects.get(pk=project.pk)
                            if current.version!=int(request.POST.get('project_version',0)): raise Conflict('Stammdaten inzwischen geändert. Neu laden und vergleichen.')
                            current.fields=project_fields_data(fields,project.fields);current.version+=1;current.save(update_fields=['fields','version'])
                            audit(request.user,current,'Import-Stammdaten bestätigt',{'source':batch.source_name,'sha256':batch.sha256})
                        batch.rows=rows;batch.save()
                    confirm_import(request.user,batch)
                messages.success(request,'Import einmal bestätigt. Quelldatei unverändert.')
                return redirect('project',pk=project.pk)
            f=request.FILES.get('source')
            if not f or f.size>5*1024*1024: raise ValidationError('CSV/JSON/PDF-Importdatei bis 5 MB wählen.')
            content=f.read();sha=hashlib.sha256(content).hexdigest()
            from .imports import parse_source_details
            from django.core.files.uploadedfile import SimpleUploadedFile
            parsed=parse_source_details(f.name,content)
            rows=parsed['rows']
            source=ingest_asset(request.user,SimpleUploadedFile(f.name,content),document=True,import_source=True)
            batch,new=ImportBatch.objects.get_or_create(owner=request.user,sha256=sha,defaults={'project':project,'source_name':f.name[:250],'rows':rows,'source_asset':source,'proposed_fields':parsed['project_fields'],'warnings':parsed['warnings']})
            if batch.project_id!=project.pk: raise ValidationError('Diese Quelle ist bereits einem anderen Projekt zugeordnet.')
            return redirect('import_batch',project_id=project.pk,pk=batch.pk)
        except (ValidationError,ValueError,UnicodeError,KeyError) as e: messages.error(request,err(e))
    return render(request,'import.html',{'project':project,'batch':batch,'fieldset':fields,'rows_json':json.dumps(batch.rows,ensure_ascii=False,indent=2) if batch else ''})

@login_required
def system_view(request):
    usage=AIUsage.objects.filter(owner=request.user,created__gte=budget_month_start())
    spent=usage.aggregate(s=Sum('estimated_usd'))['s'] or Decimal(0)
    from .preferences import monthly_budget
    budget = monthly_budget()
    return render(request,'system.html',{'jobs':Job.objects.filter(Q(document__owner=request.user)|Q(asset__owner=request.user)).order_by('-updated')[:30],'spent':spent,'budget':budget,'warning':spent>=min(settings.AI_MONTHLY_WARN,budget),'model':settings.GEMINI_MODEL,'paperless_url_set':bool(settings.PAPERLESS_URL),'exports':ExportRequest.objects.filter(owner=request.user).order_by('-created')[:20]})

@require_safe
def health(request):
    # No private counts, paths, provider names or credentials in public health check.
    try:
        with connection.cursor() as c: c.execute('SELECT 1')
        return JsonResponse({'status':'ok'})
    except Exception: return JsonResponse({'status':'unavailable'},status=503)

@login_required
@xframe_options_sameorigin
@require_safe
def document_original(request,pk):
    doc=get_object_or_404(Document,pk=pk,owner=request.user)
    if doc.asset_id and request.GET.get('remote')!='1':
        if doc.asset.owner_id!=request.user.pk:raise Http404
        return redirect('media',pk=doc.asset_id)
    try:
        raw=paperless(doc).original(doc.paperless_id)
        return FileResponse(io.BytesIO(raw),content_type='application/pdf' if raw.startswith(b'%PDF-') else 'application/octet-stream',filename='beleg.pdf' if raw.startswith(b'%PDF-') else 'beleg.bin')
    except (IntegrationError,AttributeError): return HttpResponse('Belegoriginal derzeit nicht erreichbar.',status=503)

@login_required
def export_request(request,project_id=None,entry_id=None):
    entry=owned_entry(request,entry_id) if entry_id else None
    project=entry.project if entry else (owned_project(request,project_id) if project_id else None)
    if request.method=='POST':
        kind=request.POST.get('kind','zip')
        if kind not in ('zip','pdf','wartung') or (kind in ('pdf','wartung') and not project): return HttpResponseBadRequest('Projekt für PDF wählen.')
        with transaction.atomic():
            try:token=uuid.UUID(request.POST['request_token']) if request.POST.get('request_token') else uuid.uuid4()
            except ValueError:return HttpResponseBadRequest('Ungültige Exportkennung.')
            export,created=ExportRequest.objects.get_or_create(pk=token,defaults={'owner':request.user,'kind':kind,'project':project,'entry':entry})
            if export.owner_id!=request.user.pk:raise Http404
            if created:Job.objects.create(kind='export',export=export,stage='export')
        messages.success(request,'Export zur seriellen Verarbeitung vorgemerkt. Download unter Betrieb & Export.')
        return redirect('system')
    return render(request,'export.html',{'project':project,'entry':entry,'request_token':uuid.uuid4(),'pdf_entry_limit':settings.PDF_MAX_ENTRIES,'pdf_photo_limit':settings.PDF_MAX_PHOTOS})

@login_required
@require_safe
def export_download(request,pk):
    export=get_object_or_404(ExportRequest,pk=pk,owner=request.user,status='done')
    relative=Path(export.path)
    if not relative.parts or relative.parts[0]!='exports':raise Http404
    path=private_path('exports',str(Path(*relative.parts[1:])))
    return FileResponse(path.open('rb'),as_attachment=True,filename='projektakte-'+export.kind+('.pdf' if export.kind in ('pdf','wartung') else '.zip'),content_type='application/pdf' if export.kind in ('pdf','wartung') else 'application/zip')
