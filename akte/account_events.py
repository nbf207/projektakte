"""Bounded login/device records; never retain credentials or request bodies."""
import logging
from types import SimpleNamespace
from datetime import timedelta
from django.contrib.auth import get_user_model, logout
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.contrib.sessions.models import Session
from django.dispatch import receiver
from django.shortcuts import redirect
from django.utils import timezone
from .models import LoginAttempt, SessionDevice, ErrorEvent
from .operations import OperationBusy, operation_gate
from .preferences import preferences


def browser_name(request):
    agent=request.META.get('HTTP_USER_AGENT','')[:500]
    platform=next((label for key,label in [('Android','Android'),('iPhone','iPhone'),('iPad','iPad'),('Windows','Windows'),('Macintosh','Mac'),('Linux','Linux')] if key in agent),'Unbekanntes Gerät')
    browser=next((label for key,label in [('Edg/','Edge'),('Firefox/','Firefox'),('Chrome/','Chrome'),('Safari/','Safari')] if key in agent),'Browser')
    return platform+' · '+browser


def attempt(owner, success, request):
    item=LoginAttempt.objects.create(owner=owner,success=success,browser=browser_name(request))
    keep=LoginAttempt.objects.filter(owner=owner).order_by('-created','-id').values('id')[:10]
    LoginAttempt.objects.filter(owner=owner).exclude(pk__in=keep).delete()
    return item


@receiver(user_logged_in)
def logged_in(sender,request,user,**kwargs):
    with operation_gate():
        pref=preferences(user)
        request.session.set_expiry(pref.session_days*86400)
        request.session['_account_epoch']=pref.auth_epoch
        attempt(user,True,request)
        if request.session.session_key:
            SessionDevice.objects.update_or_create(session_key=request.session.session_key,
                defaults={'owner':user,'browser':browser_name(request),'last_seen':timezone.now()})


@receiver(user_login_failed)
def failed_login(sender,credentials,request,**kwargs):
    if request is None:return
    from .security import record_failure
    record_failure(request)


@receiver(user_logged_out)
def logged_out(sender,request,user,**kwargs):
    if request and request.session.session_key:
        SessionDevice.objects.filter(session_key=request.session.session_key).delete()


class DeviceMiddleware:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        if request.user.is_authenticated:
            pref=preferences(request.user)
            if request.session.get('_account_epoch',0)!=pref.auth_epoch:
                logout(request)
                return redirect('login')
            key=request.session.session_key
            if key:
                now=timezone.now()
                device=SessionDevice.objects.filter(session_key=key).first()
                if device is None or device.last_seen<now-timedelta(minutes=1):
                    try:
                        with operation_gate(timeout=0):
                            SessionDevice.objects.update_or_create(session_key=key,
                                defaults={'owner':request.user,'browser':browser_name(request),'last_seen':now})
                    except OperationBusy:
                        pass
        return self.get_response(request)


def active_devices(user, current_key):
    result=[];known={item.session_key:item for item in SessionDevice.objects.filter(owner=user)}
    for session in Session.objects.filter(expire_date__gt=timezone.now()):
        decoded=session.get_decoded() if session else {}
        if str(decoded.get('_auth_user_id'))!=str(user.pk) or decoded.get('_auth_user_hash')!=user.get_session_auth_hash():
            continue
        item=known.get(session.session_key) or SimpleNamespace(browser='Ältere Sitzung · Gerät beim nächsten Zugriff erkennbar',last_seen=None)
        result.append({'device':item,'current':session.session_key==current_key})
    return sorted(result,key=lambda row:(not row['current'],str(row['device'].last_seen or '')))


def error_event(component,message,owner=None):
    try:
        item=ErrorEvent.objects.create(owner=owner,component=component,message=message[:800])
        keep=ErrorEvent.objects.filter(owner=owner).order_by('-created','-id').values('pk')[:30]
        ErrorEvent.objects.filter(owner=owner).exclude(pk__in=keep).delete()
        return item
    except Exception:
        return None


class SafeErrorHandler(logging.Handler):
    def emit(self,record):
        request=getattr(record,'request',None)
        if getattr(request, 'public_demo', False): return
        user=getattr(request,'user',None)
        owner=user if user and user.is_authenticated else None
        route=getattr(getattr(request,'resolver_match',None),'url_name',None) or 'Anfrage'
        if record.name.startswith('django') and owner is None:
            return  # Bot errors must never write to SQLite.
        if record.name.startswith('django'):
            error_event('Server',f'HTTP {getattr(record,"status_code",500)} · {route}',owner)
        else:
            error_event('IMAP','Postfachabruf fehlgeschlagen; Verbindung, Anmeldung oder Dateiformat prüfen.')
