"""Authenticated settings center. Mutations use POST, CSRF and owner scoping."""
import hashlib
from django.db.models import Sum
from .billing import budget_month_start
import json
import shutil
import time
import uuid
from pathlib import Path
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.sessions.models import Session
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST, require_safe
from .account_events import active_devices
from .control_forms import GeminiKeyForm, BudgetForm, DurationForm, IntegrationForm, MediaForm, BackupOptionsForm, FeedbackEditForm, HandoverForm
from .control_tasks import schedule_control, backup_path, restart_stale, read_media_usage
from .feedback_store import list_feedback, edit_feedback, image_path, feedback_prompt
from .media_controls import orphan_files, remove_orphans, trash_items, empty_trash
from .models import Preferences, SessionDevice, LoginAttempt, ManagedBackup, ControlTask, Job, ErrorEvent, Document, AIUsage, SystemBudget
from .operations import OperationBusy, operation_gate
from .preferences import preferences, monthly_budget, system_access, require_system_access
from .purging import source_fingerprint

SECTIONS=[('projekte','Projekte'),('konto','Konto & Sicherheit'),('anbindungen','KI & Schnittstellen'),
          ('medien','Medien & Papierkorb'),('feedback','Feedback'),('system','System & Sicherung'),('darstellung','Darstellung')]


def center_url(section):
    return reverse('settings_center',args=[section])


def preference_initial(pref):
    return {'version':0 if pref._state.adding else pref.version,'session_days':pref.session_days,'language':pref.language,'currency':pref.currency,'ai_mode':pref.ai_mode,
        'keep_audio':pref.keep_audio,'paperless_tags':','.join(map(str,pref.paperless_tags)),
        'mail_senders':'\n'.join(pref.mail_senders),'photo_width':pref.photo_width,'photo_quality':pref.photo_quality,
        'trash_days':pref.trash_days,'trash_costs':pref.trash_costs,'backup_keep':pref.backup_keep}


def save_preferences(owner,form):
    with operation_gate(),transaction.atomic():
        current=preferences(owner)
        version=0 if current._state.adding else current.version
        if version!=form.cleaned_data['version']:raise ValidationError('Einstellungen inzwischen geändert. Bitte neu laden.')
        for field,value in form.cleaned_data.items():
            if field!='version':setattr(current,field,value)
        if isinstance(form,IntegrationForm):current.mail_override=True
        current.version=version+1;current.save()


def trash_signature(owner):
    rows=[]
    for item in trash_items(owner):
        source=getattr(item,'source',None) if isinstance(item,Document) else None
        rows.append([item._meta.model_name,str(item.pk),item.version,source_fingerprint(source) if source else ''])
    return hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()


def monitor():
    disk=shutil.disk_usage(settings.DATA_DIR)
    def size(path):
        try:
            return path.stat().st_size if path.is_file() and not path.is_symlink() else 0
        except OSError:
            # Upload cleanup can remove a file between enumeration and stat.
            return 0
    db=sum(size(settings.DATA_DIR/name) for name in ('db.sqlite3','db.sqlite3-wal','db.sqlite3-shm'))
    media,media_updated=read_media_usage()
    memory={}
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            key,value=line.split(':',1);memory[key]=int(value.strip().split()[0])*1024
    except (OSError,ValueError):pass
    heartbeat=settings.DATA_DIR/'worker.heartbeat'
    try:
        age=max(0,round(time.time()-heartbeat.stat().st_mtime))
    except OSError:
        age=None
    return {'free':disk.free,'total':disk.total,'database':db,'media':media,
            'media_updated':timezone.localtime(media_updated).strftime('%d.%m.%Y %H:%M') if media_updated else None,
            'ram_total':memory.get('MemTotal'),'ram_used':memory.get('MemTotal',0)-memory.get('MemAvailable',0),
            'worker_age':age,'worker_ok':age is not None and age<180}


@login_required
@require_http_methods(['GET','POST'])
def settings_center(request,section):
    if section not in dict(SECTIONS) or section=='projekte':raise Http404
    if section=='feedback' and not settings.FEEDBACK_WIDGET_ENABLED:raise Http404
    pref=preferences(request.user);initial=preference_initial(pref)
    if not pref.mail_override:
        from .mail_config import mail_config
        try:
            config=mail_config();initial['mail_senders']=config.sender if config else ''
        except ValueError:initial['mail_senders']=''
    form_class={'konto':DurationForm,'anbindungen':IntegrationForm,'medien':MediaForm,'system':BackupOptionsForm}.get(section)
    action=request.POST.get('action')
    form=form_class(request.POST if request.method=='POST' and action=='preferences' else None,initial=initial) if form_class else None
    password_form=PasswordChangeForm(request.user,request.POST if action=='password' else None)
    error=''
    feedback_edit=None
    feedback_key=None
    key_form=GeminiKeyForm(request.POST if action=='gemini_key' else None)
    budget_form = None
    if section == 'anbindungen' and system_access(request.user):
        budget_form = BudgetForm(request.POST if action == 'budget' else None, initial={'monthly_usd': monthly_budget()})
    if request.method=='POST':
        try:
            if action=='preferences' and form:
                if section=='anbindungen':require_system_access(request.user)
                if not form.is_valid():raise ValidationError('Bitte die markierten Einstellungen korrigieren.')
                save_preferences(request.user,form);messages.success(request,'Einstellungen gespeichert.')
            elif action == 'budget' and section == 'anbindungen':
                require_system_access(request.user)
                if not budget_form.is_valid():
                    raise ValidationError('Bitte das Monatslimit prüfen.')
                with operation_gate(), transaction.atomic():
                    SystemBudget.objects.update_or_create(pk=1, defaults={'monthly_usd': budget_form.cleaned_data['monthly_usd']})
                messages.success(request, 'KI-Monatslimit gespeichert.')
            elif action=='gemini_key' and section=='anbindungen':
                require_system_access(request.user)
                if not key_form.is_valid():raise ValidationError('Bitte den Schlüssel prüfen.')
                from .integration_secrets import save_gemini_key
                save_gemini_key(key_form.cleaned_data['api_key'])
                messages.success(request,'Gemini-Schlüssel geschützt gespeichert. Künftige Auswertungen verwenden ihn; bereits laufende Anfragen können noch den bisherigen Schlüssel nutzen.')
            elif action=='password' and section=='konto':
                if not password_form.is_valid():raise ValidationError('Passwort bitte prüfen.')
                old_key=request.session.session_key
                with operation_gate(),transaction.atomic():
                    user=password_form.save();update_session_auth_hash(request,user)
                    SessionDevice.objects.filter(owner=user,session_key=old_key).update(session_key=request.session.session_key)
                    for other in Session.objects.filter(expire_date__gt=timezone.now()).exclude(session_key=request.session.session_key):
                        if str(other.get_decoded().get('_auth_user_id'))==str(user.pk):other.delete()
                    SessionDevice.objects.filter(owner=user).exclude(session_key=request.session.session_key).delete()
                messages.success(request,'Passwort geändert. Diese Sitzung bleibt angemeldet.')
            elif action=='sessions' and section=='konto':
                with operation_gate(),transaction.atomic():
                    current,_=Preferences.objects.get_or_create(owner=request.user)
                    current.auth_epoch+=1;current.version+=1;current.save()
                    request.session['_account_epoch']=current.auth_epoch
                    for session in Session.objects.filter(expire_date__gt=timezone.now()).exclude(session_key=request.session.session_key):
                        if str(session.get_decoded().get('_auth_user_id'))==str(request.user.pk):session.delete()
                    SessionDevice.objects.filter(owner=request.user).exclude(session_key=request.session.session_key).delete()
                messages.success(request,'Alle anderen Geräte sind abgemeldet.')
            elif action=='feedback_edit' and section=='feedback':
                feedback_key=request.POST.get('key','')
                edit=FeedbackEditForm(request.POST,request.FILES,auto_id='feedback_'+feedback_key+'_%s')
                feedback_edit=edit
                if not edit.is_valid():raise ValidationError(edit.errors.as_text())
                data=edit.cleaned_data;edit_feedback(request.user,request.POST.get('key',''),data['text'],data['done'],data['digest'],data.get('image'),implemented=data['implemented'])
                messages.success(request,'Feedback gespeichert.')
            elif action=='empty' and section=='medien':
                if request.POST.get('confirmed')!='LEEREN':raise ValidationError('Zum endgültigen Löschen LEEREN eingeben.')
                try:expected=signing.loads(request.POST.get('snapshot',''),salt='trash074',max_age=600)
                except signing.BadSignature:raise ValidationError('Papierkorbvorschau abgelaufen. Neu laden.') from None
                with operation_gate():
                    if expected!={'owner':request.user.pk,'hash':trash_signature(request.user)}:raise ValidationError('Papierkorb oder Kosten geändert. Bitte Vorschau neu laden.')
                    removed,blocked=empty_trash(request.user,remove_costs=request.POST.get('remove_costs')=='1')
                messages.success(request,f'{removed} Einträge endgültig entfernt; {blocked} wegen Kosten oder Historien-Verknüpfungen aufbewahrt.')
            elif action=='cleanup' and section=='medien':
                require_system_access(request.user)
                if request.POST.get('confirmed')!='1':raise ValidationError('Dateilöschung bestätigen.')
                count=remove_orphans(request.user,request.POST.getlist('file'))
                messages.success(request,f'{count} unreferenzierte Dateien entfernt.')
            elif action=='restart' and section=='system':
                count=restart_stale(request.user);messages.success(request,f'{count} abgelaufene Aufträge freigegeben. Laufende Aufträge und übernommene Rechnungen bleiben geschützt.')
            else:raise ValidationError('Ungültige Aktion.')
            return redirect(center_url(section))
        except (ValidationError,ValueError) as exc:error='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Ungültige Eingabe.'
    context={'section':section,'settings_sections':SECTIONS,'form':form,'password_form':password_form,'error':error,
        'gemini_key_form':key_form,'system_access':system_access(request.user),'action_key':uuid.uuid4(),
        'control_tasks':ControlTask.objects.filter(owner=request.user).select_related('job').order_by('-created')[:12]}
    for kind in ('paperless_test','paperless_sync','mail','backup','backup_discover'):
        context[kind+'_key']=uuid.uuid5(context['action_key'],kind)
    if budget_form is not None:
        spent = AIUsage.objects.filter(created__gte=budget_month_start()).aggregate(total=Sum('estimated_usd'))['total'] or 0
        context.update(budget_form=budget_form, budget_spent=spent, budget_max=settings.AI_MONTHLY_LIMIT)
    if section == 'anbindungen':
        from .integration_secrets import gemini_key
        from .mail_config import mail_config
        from .preferences import ai_is_active, model_spec, paperless_is_active
        ai_key_present = bool(gemini_key())
        ai_active = ai_is_active()
        ai_model_name = model_spec(request.user)[0]
        paperless_active = paperless_is_active()
        try:
            mail_active = bool(mail_config()) or settings.DEMO_MODE
        except ValueError:
            mail_active = False
        context.update(
            ai_enabled=settings.AI_ENABLED,
            ai_key_present=ai_key_present,
            ai_active=ai_active,
            ai_model_name=ai_model_name,
            paperless_active=paperless_active,
            mail_active=mail_active,
        )
    if section=='konto':
        attempts=LoginAttempt.objects.filter(Q(owner=request.user)|Q(owner=None)) if system_access(request.user) else LoginAttempt.objects.filter(owner=request.user)
        recent=list(attempts.order_by('-created','-id')[:10])
        if system_access(request.user):
            from .security import failure_history
            recent+=failure_history()
        context.update(devices=active_devices(request.user,request.session.session_key),attempts=sorted(recent,key=lambda row:row.created,reverse=True)[:10])
    elif section=='feedback':
        rows=list_feedback(request.user)
        feedback_filter=request.GET.get('filter','open')
        if feedback_filter!='all': rows=[row for row in rows if row['record'].get('status')!='done']
        context['feedback_filter']=feedback_filter
        for row in rows:
            row['form']=FeedbackEditForm(initial={'text':row['record'].get('text',''),'done':row['record'].get('status')=='done','implemented':bool(row['record'].get('implemented')),'digest':row['digest']},auto_id='feedback_'+row['key']+'_%s')
            if feedback_edit is not None and row['key']==feedback_key:
                row['form']=feedback_edit
        context.update(feedbacks=rows,prompt=feedback_prompt(rows))
    elif section=='medien':
        items=trash_items(request.user)
        context.update(trash_count=len(items),trash_preview=signing.dumps({'owner':request.user.pk,'hash':trash_signature(request.user)},salt='trash074'),handover_form=HandoverForm(owner=request.user))
        context['trash_costs']=[item.source for item in items if isinstance(item,Document) and hasattr(item,'source')]
        if request.GET.get('scan')=='1':
            require_system_access(request.user);context['orphans']=orphan_files(request.user);context['scanned']=True
    elif section=='system':
        events=ErrorEvent.objects.filter(Q(owner=request.user)|Q(owner=None)) if system_access(request.user) else ErrorEvent.objects.filter(owner=request.user)
        context.update(monitor=monitor(),backups=ManagedBackup.objects.filter(owner=request.user).order_by('-created'),errors=events.order_by('-created','-id')[:30])
    return render(request,'settings_center.html',context)


@login_required
@require_POST
def control_action(request):
    kind=request.POST.get('kind')
    try:
        schedule_control(request.user,kind,request.POST.get('request_key'))
        messages.success(request,'Auftrag im Hintergrund vorgemerkt. Hier kannst du das Ergebnis aktualisieren.')
    except (ValidationError,ValueError,TypeError) as exc:
        messages.error(request,'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Ungültige Auftragskennung.')
    return redirect(center_url('system' if kind in ('backup','backup_discover') else 'anbindungen'))


@login_required
@require_safe
def backup_download(request,pk):
    record=get_object_or_404(ManagedBackup,pk=pk,owner=request.user)
    require_system_access(request.user)
    try:path=backup_path(record.path)
    except ValidationError:raise Http404 from None
    try:
        with operation_gate():
            if not path.is_file():raise Http404
            stream=path.open('rb')
    except OperationBusy:
        response=HttpResponse('Sicherung oder Schreibvorgang läuft. Bitte kurz warten und Download erneut starten.',status=503)
        response['Retry-After']='5'
        return response
    return FileResponse(stream,as_attachment=True,filename='projektakte-'+record.created.strftime('%Y%m%d-%H%M%S')+'.zip',content_type='application/zip')


@login_required
@require_safe
def feedback_image(request,key,identity):
    if not settings.FEEDBACK_WIDGET_ENABLED:raise Http404
    path=image_path(request.user,key,identity)
    response=FileResponse(path.open('rb'),content_type={'.jpg':'image/jpeg','.png':'image/png','.webp':'image/webp'}[path.suffix])
    response['Content-Security-Policy']="default-src 'none'"
    return response


@login_required
@require_http_methods(['GET'])
def settings_status(request):
    tasks=ControlTask.objects.filter(owner=request.user).select_related('job').order_by('-created')[:12]
    return JsonResponse({'monitor':monitor() if request.GET.get('monitor')=='1' else None,
        'tasks':[{'id':str(task.pk),'state':task.display_state,'result':task.result or (task.job.error if hasattr(task,'job') else '')} for task in tasks]})
