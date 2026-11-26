"""PWA metadata and the authenticated, CSRF-protected share confirmation."""
import uuid
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST, require_safe
from .capture import receive_capture
from .models import Project


@require_safe
def manifest(request):
    return JsonResponse({'id':'/','name':'Projektakte','short_name':'Projektakte','lang':'de',
        'start_url':'/','scope':'/','display':'standalone','background_color':'#f4f7f6','theme_color':'#0b625e',
        'description':'Deine Projekte, Arbeitsschritte und Belege.',
        'icons':[{'src':'/static/icon-192.png','sizes':'192x192','type':'image/png','purpose':'any'},
                 {'src':'/static/icon-512.png','sizes':'512x512','type':'image/png','purpose':'any maskable'}],
        'share_target':{'action':'/teilen/','method':'POST','enctype':'multipart/form-data',
            'params':{'title':'title','text':'text','url':'url',
                'files':[{'name':'files','accept':['image/*','application/pdf','.jpg','.jpeg','.png','.webp','.pdf']}]}}},
        content_type='application/manifest+json')


@require_safe
def service_worker(request):
    source=(settings.BASE_DIR/'static'/'auth.js').read_bytes()+b'\n'+(settings.BASE_DIR/'static'/'sw.js').read_bytes()
    response=HttpResponse(source,content_type='application/javascript')
    response['Service-Worker-Allowed']='/'
    response['Cache-Control']='no-cache'
    return response


@login_required
@require_GET
@ensure_csrf_cookie
def inbox(request):
    return render(request,'share.html',{'projects':Project.objects.filter(owner=request.user,archived=False,is_scan_inbox=False).order_by('position','name'),
        'capture_token':uuid.uuid4()})


@login_required
@require_POST
def save(request):
    # No csrf_exempt endpoint: only the service worker receives the OS share POST.
    # Files reach Django after this normal form has a fresh CSRF token/session.
    try:
        if not request.POST.get('project'): raise ValidationError('Bitte das Zielprojekt auswählen.')
        project=get_object_or_404(Project,pk=request.POST['project'],owner=request.user,archived=False,is_scan_inbox=False)
        if not request.FILES.getlist('files'): raise ValidationError('Mindestens ein geteiltes Bild oder PDF hinzufügen.')
        doc=receive_capture(request.user,text=request.POST.get('text',''),uploads=request.FILES.getlist('files'),
            audio=request.FILES.get('audio'),project=project,token=request.POST.get('capture_token'),
            keep_audio=(request.POST.get('keep_audio')=='1' if request.POST.get('audio_option_present') or 'keep_audio' in request.POST else None))
        url=reverse('capture_detail',args=[doc.pk])+'?weiter=1'
        if request.headers.get('X-Requested-With')=='XMLHttpRequest': return JsonResponse({'url':url})
        return redirect(url)
    except (ValidationError,ValueError,TypeError) as exc:
        text='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Ungültige Erfassungskennung. Teilen-Seite neu öffnen.'
        return JsonResponse({'error':text},status=400)
