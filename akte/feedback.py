"""Authenticated private feedback files on the persistent SSD, never in /app."""
import json
import os
import uuid
from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .models import Entry, Project
from .operations import operation_gate
from .storage import durable_replace
from .feedback_store import FeedbackImage, attach_image, write_feedback


class FeedbackForm(forms.Form):
    image=FeedbackImage(required=False)
    text=forms.CharField(max_length=4000)
    url=forms.CharField(max_length=2000,required=False)
    title=forms.CharField(max_length=200,required=False)
    project=forms.UUIDField(required=False)
    entry=forms.UUIDField(required=False)


@login_required
@require_POST
def save_feedback(request):
    form=FeedbackForm(request.POST,request.FILES)
    if not form.is_valid():return JsonResponse({'error':form.errors.as_text()},status=400)
    data=form.cleaned_data
    project=get_object_or_404(Project,pk=data['project'],owner=request.user) if data.get('project') else None
    entry=get_object_or_404(Entry,pk=data['entry'],project__owner=request.user) if data.get('entry') else None
    if entry and project and entry.project_id!=project.pk:return JsonResponse({'error':'Projekt und Eintrag passen nicht zusammen.'},status=400)
    try:parsed=urlsplit(data['url'])
    except ValueError:return JsonResponse({'error':'Ungültige Seitenadresse.'},status=400)
    if '\\' in parsed.path:return JsonResponse({'error':'Ungültige Seitenadresse.'},status=400)
    if parsed.netloc and parsed.netloc!=request.get_host():return JsonResponse({'error':'Ungültige Seitenadresse.'},status=400)
    sensitive=('token','key','password','secret','authorization','cf_access')
    query=[(k,v) for k,v in parse_qsl(parsed.query,keep_blank_values=True) if not any(part in k.casefold() for part in sensitive)]
    current=urlunsplit(('', '',parsed.path if parsed.path.startswith('/') else '/',urlencode(query),''))
    if not url_has_allowed_host_and_scheme(current,allowed_hosts={request.get_host()},require_https=request.is_secure()):
        return JsonResponse({'error':'Ungültige Seitenadresse.'},status=400)
    record={'timestamp':timezone.now().isoformat(),'user':{'id':request.user.pk,'username':request.user.username},
            'url':current,'page_title':data['title'],'project':str((project or (entry.project if entry else None)).pk) if project or entry else None,
            'entry':str(entry.pk) if entry else None,'app_version':settings.APP_VERSION,'text':data['text']}
    with operation_gate():
        folder=settings.DATA_DIR/'feedback';folder.mkdir(mode=0o700,exist_ok=True)
        identity=uuid.uuid4().hex;target=folder/(timezone.now().strftime('%Y%m%dT%H%M%S')+'-'+identity+'.json')
        attachment=None
        try:
            attachment=attach_image(record,data.get('image'));write_feedback(target,record)
        except BaseException:
            if attachment:attachment.unlink(missing_ok=True)
            raise
    if request.headers.get('X-Requested-With')=='XMLHttpRequest':return JsonResponse({'message':'Feedback auf dem Pi gespeichert. Danke!'})
    messages.success(request,'Feedback gespeichert.');return redirect(current)
