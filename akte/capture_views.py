from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404,redirect,render
from django.urls import reverse
from .capture import receive_capture,finish_chronicle
from .forms import ProjectRoutingForm
from .models import Document,Project
from .operations import operation_gate
from .review_state import review_processing


@login_required
def upload(request):
    if request.method!='POST': return redirect('home')
    try:
        project=get_object_or_404(Project,pk=request.POST['project'],owner=request.user,archived=False,is_scan_inbox=False) if request.POST.get('project') else None
        doc=receive_capture(request.user,text=request.POST.get('text',''),uploads=request.FILES.getlist('files'),
            audio=request.FILES.get('audio'),project=project,token=request.POST.get('capture_token'),keep_audio=(request.POST.get('keep_audio')=='1' if request.POST.get('audio_option_present') or 'keep_audio' in request.POST else None))
        if request.headers.get('X-Requested-With')=='XMLHttpRequest': return JsonResponse({'url':reverse('entry',args=[doc.entry_id])})
        return redirect('entry',pk=doc.entry_id)
    except (ValidationError,ValueError,TypeError) as exc:
        text='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Ungültige Erfassungskennung. Startseite neu öffnen.'
        if request.headers.get('X-Requested-With')=='XMLHttpRequest': return JsonResponse({'error':text},status=400)
        messages.error(request,text);return redirect('home')


@login_required
def detail(request,pk):
    doc=get_object_or_404(Document.objects.select_related('entry__project','project'),pk=pk,owner=request.user,capture_type='snap')
    if doc.deleted_at: return render(request,'document_trashed.html',{'doc':doc,'project':doc.project})
    receipt=Document.objects.filter(pk=doc.voice_data.get('receipt_id'),owner=request.user).first() if doc.voice_data.get('receipt_id') else None
    if receipt: return redirect('review',pk=receipt.pk)
    if request.GET.get('weiter')=='1' and doc.status=='accepted' and doc.entry_id:
        return redirect('entry',pk=doc.entry_id)
    form=ProjectRoutingForm(request.POST if request.method=='POST' else None,owner=request.user,initial={'version':doc.version,'project':doc.project_id})
    if request.method=='POST':
        try:
            if request.POST.get('action')=='retry':
                from .documents import schedule_document
                schedule_document(doc,'retry')
                messages.success(request,'Auswertung erneut vorgemerkt. Vorhandene Bilder und Eingaben bleiben erhalten.')
            elif request.POST.get('action')=='project':
                if not form.is_valid():
                    return render(request,'capture_detail.html',{'doc':doc,'project':doc.project,'route_form':form,'processing':review_processing(doc),'pages':doc.pages.select_related('asset').order_by('position')})
                from .project_routing import assign_project
                with operation_gate(),transaction.atomic():
                    current=assign_project(request.user,doc,form.cleaned_data['project'],form.cleaned_data['version'])
                    if current.voice_data.get('result') and not review_processing(current): finish_chronicle(current)
                messages.success(request,'Projekt übernommen.')
            return redirect('capture_detail',pk=doc.pk)
        except ValidationError as exc: form.add_error(None,'; '.join(exc.messages))
    return render(request,'capture_detail.html',{'doc':doc,'project':doc.project,'route_form':form,'processing':review_processing(doc),
        'pages':doc.pages.select_related('asset').order_by('position')})
