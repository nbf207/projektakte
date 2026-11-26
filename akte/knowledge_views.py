"""Authenticated knowledge catalogue and review; all source links remain owner-scoped."""
import uuid
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.urls import reverse
from .repair_library import database
from django.views.decorators.http import require_http_methods
from .models import KnowledgeItem, Project, Entry, RepairLibrary
from .knowledge import KnowledgeForm
from .operations import operation_gate

def owned_project(user, identity):
    try:identity=uuid.UUID(str(identity))
    except (ValueError,TypeError,AttributeError):raise Http404() from None
    return get_object_or_404(Project,pk=identity,owner=user)

@login_required
@require_http_methods(['GET'])
def catalogue(request):
    rows=KnowledgeItem.objects.filter(owner=request.user).select_related('project','entry')
    project=None
    if request.GET.get('project'):
        project=owned_project(request.user,request.GET['project'])
        rows=rows.filter(project=project)
    query=request.GET.get('q','').strip()[:200]
    for term in query.split()[:12]:
        rows=rows.filter(Q(title__icontains=term)|Q(subject__icontains=term)|Q(content__icontains=term)|Q(value__icontains=term)|Q(unit__icontains=term)|Q(source_quote__icontains=term)|Q(project__name__icontains=term))
    state=request.GET.get('state','approved')
    if state in ('approved','review','rejected'):rows=rows.filter(state=state)
    else:state='all'
    libraries=RepairLibrary.objects.filter(owner=request.user,project__owner=request.user).select_related('project')
    if project:libraries=libraries.filter(project=project)
    return render(request,'knowledge_list.html',{'items':Paginator(rows.order_by('-updated'),30).get_page(request.GET.get('page')),
        'libraries':libraries,'projects':Project.objects.filter(owner=request.user,is_scan_inbox=False),'project':project,'q':query,'state':state})

@login_required
@require_http_methods(['GET','POST'])
def edit(request,pk=None):
    item=get_object_or_404(KnowledgeItem,pk=pk,owner=request.user) if pk else None
    project=item.project if item else owned_project(request.user,request.GET.get('project') or request.POST.get('project'))
    library = item.repair_library if item else None
    repair_id = item.repair_document_id if item else request.GET.get('repair','')
    if not item and request.GET.get('library'):
        try:
            library_id = uuid.UUID(request.GET['library'])
        except ValueError:
            raise Http404 from None
        library = get_object_or_404(RepairLibrary, pk=library_id, project=project, owner=request.user)
    repair_title = ''
    if library and repair_id:
        with database(library) as db:
            doc = db.execute('SELECT title FROM docs WHERE id=? AND has_content=1', (repair_id,)).fetchone()
        if doc is None and not item:
            raise Http404
        if doc:
            repair_title = doc['title']
    entry=item.entry if item else None
    if not item and request.GET.get('entry'):
        entry=get_object_or_404(Entry,pk=request.GET['entry'],project=project,deleted_at=None)
    initial={'version':item.version if item else 0,'youtube_url':'https://www.youtube.com/watch?v='+item.youtube_id if item and item.youtube_id else ''}
    if entry and not item:initial.update(title=entry.title,content=entry.description,kind='observation')
    form=KnowledgeForm(request.POST or None,instance=item,initial=initial,allow_link_only=bool(library))
    error=''
    if request.method=='POST' and form.is_valid():
        try:
            with operation_gate(),transaction.atomic():
                current=KnowledgeItem.objects.get(pk=item.pk,owner=request.user) if item else None
                if current and current.version!=form.cleaned_data['version']:raise ValidationError('Zwischenzeitlich geändert. Bitte neu laden.')
                obj=form.save(commit=False)
                obj.owner=request.user;obj.project=project;obj.entry=entry
                obj.repair_library=library;obj.repair_document_id=repair_id if library else ''
                obj.youtube_id=form.cleaned_data['youtube_id']
                action=request.POST.get('action','save')
                if action not in ('save','approve','reject'):raise ValidationError('Ungültige Aktion.')
                obj.state='approved' if action=='approve' or (library and action=='save') else 'rejected' if action=='reject' else 'review'
                obj.reviewed_at=timezone.now() if action=='approve' else None
                obj.version=(current.version if current else 0)+1;obj.updated=timezone.now()
                obj.save()
            return redirect('knowledge_detail',pk=obj.pk)
        except ValidationError as exc:error='; '.join(exc.messages)
    return render(request,'knowledge_edit.html',{'form':form,'item':item,'project':project,'error':error,'repair_library':library,'repair_title':repair_title})

@login_required
@require_http_methods(['GET'])
def detail(request,pk):
    item=get_object_or_404(KnowledgeItem.objects.select_related('project','entry'),pk=pk,owner=request.user)
    entry=item.entry if item.entry and item.entry.project.owner_id==request.user.pk and not item.entry.deleted_at else None
    repair_url = None
    library = item.repair_library
    if library and library.owner_id == request.user.pk and library.project_id == item.project_id:
        repair_url = reverse('repair_document', args=[library.pk, item.repair_document_id]) if item.repair_document_id else reverse('repair_library', args=[library.pk])
    response=render(request,'knowledge_detail.html',{'item':item,'project':item.project,'source_entry':entry,'repair_url':repair_url})
    # The normal middleware CSP must explicitly permit only the privacy-enhanced YouTube origin.
    return response
