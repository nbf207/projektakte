"""Isolated, allowlisted product demo. No dispatch into live application views."""
import hmac
import re
import secrets
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import PermissionDenied
from django.core.files.uploadhandler import MemoryFileUploadHandler
from django.db import transaction, DatabaseError
from django.http import HttpResponse, HttpResponseRedirect, HttpResponseNotAllowed
from django.middleware.csrf import CsrfViewMiddleware
from django.shortcuts import render
from django.utils import timezone, translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _l
from .demo_boundary import demo_scope
from .demo_seed import seed_story, clear_story
from .i18n import normalize_language
from .models import Allocation, Category, CostSource, Document, Entry, MaintenanceRule, Project, Task
from .security import PreAuthRateMiddleware, limited_response, security_cache, _cache_lock

COOKIE = 'projektakte_demo'
LIMIT = 2 * 1024 * 1024
PAGES = {'start', 'home', 'chronik', 'wartung', 'aufgaben', 'belege', 'suche', 'export', 'darstellung', 'erfassen', 'reset', 'ende', 'beleg', 'sprache'}
NAV = [('home',_l('Schnellerfassung'),'folder'),('chronik',_l('Chronik & Teile'),'folder'),('wartung',_l('Wartungsplan'),'calendar'),
       ('aufgaben',_l('Aufgaben'),'check'),('belege',_l('Belegeingang'),'inbox'),('suche',_l('Suche'),'search'),
       ('export',_l('Export'),'system'),('darstellung',_l('Darstellung'),'settings')]


def cookie_response(response, session):
    response.set_cookie(COOKIE, session.session_key, max_age=7200, path='/demo/',
                        secure=settings.SESSION_COOKIE_SECURE, httponly=True, samesite='Lax')
    return response


def demo_user(session):
    key = session.get('_auth_user_id')
    if not key:
        return AnonymousUser()
    user = get_user_model().objects.filter(pk=key, is_active=True, is_staff=False, is_superuser=False,
        username__startswith='demo_', date_joined__gt=timezone.now()-timedelta(hours=2)).first()
    if user and hmac.compare_digest(user.get_session_auth_hash(), str(session.get('_auth_user_hash', ''))):
        return user
    return AnonymousUser()


class DemoMemoryUploadHandler(MemoryFileUploadHandler):
    def handle_raw_input(self, input_data, META, content_length, boundary, encoding=None):
        self.activated = True  # Header boundary already enforces total request <= 2 MB.


class DemoBoundaryMiddleware:
    """Run before live sessions. Separate cookie, CSRF enforcement, router and SQL guard.

    A demo credential grants access only to the exact /demo/ allowlist. Live
    authentication and all existing private URL rules stay untouched.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path_info.startswith('/demo/'):
            return self.get_response(request)
        page = request.path_info.removeprefix('/demo/').strip('/') or 'home'
        if page not in PAGES or request.path_info not in ('/demo/', '/demo/'+page+'/'):
            return HttpResponse('Nicht gefunden.', status=404)
        if request.method not in ('GET','HEAD','POST'):
            return HttpResponseNotAllowed(('GET','HEAD','POST'))
        try:
            size = int(request.META.get('CONTENT_LENGTH') or 0)
        except ValueError:
            return HttpResponse('Ungültige Anfragegröße.', status=400)
        if size < 0 or size > LIMIT or request.META.get('HTTP_TRANSFER_ENCODING') or request.META.get('HTTP_X_PROJEKTAKTE_CHUNKED') == '1':
            return HttpResponse('Demo: maximal 2 MB pro Anfrage, kein Chunked-Upload.', status=413)
        with demo_scope():
            try:
                request.public_demo = True
                request.session = SessionStore(request.COOKIES.get(COOKIE))
                request.user = demo_user(request.session)
                request.resolver_match = SimpleNamespace(url_name='public_demo')
                if not request.user.is_authenticated and page != 'start':
                    return HttpResponseRedirect('/login/')
                # Memory-only upload handling; no TemporaryFileUploadHandler fallback.
                request.upload_handlers = [DemoMemoryUploadHandler(request)]
                request.demo_page = page
                demo_lang = (
                    normalize_language(request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME))
                    or normalize_language(request.session.get('demo_lang'))
                    or normalize_language(translation.get_language_from_request(request))
                    or normalize_language(settings.LANGUAGE_CODE)
                    or 'de'
                )
                request.demo_lang = demo_lang
                request.LANGUAGE_CODE = demo_lang
                csrf = CsrfViewMiddleware(lambda req: dispatch(req))
                def protected(req):
                    csrf.process_request(req)
                    rejected = csrf.process_view(req, dispatch, (), {})
                    response = rejected if rejected is not None else dispatch(req)
                    return csrf.process_response(req, response)
                with translation.override(demo_lang):
                    response = PreAuthRateMiddleware(protected)(request)
                response['X-Frame-Options'] = 'DENY'
                response['Content-Language'] = demo_lang
                return response
            except (DatabaseError, PermissionDenied):
                return HttpResponse('Die Demo ist gerade nicht verfügbar. Bitte später erneut starten.', status=503)


def dispatch(request):
    page = request.demo_page
    if page in ('start','reset','ende','erfassen','sprache') and request.method != 'POST':
        return HttpResponseNotAllowed(('POST',))
    if page not in ('start','reset','ende','erfassen','sprache') and request.method not in ('GET','HEAD'):
        return HttpResponseNotAllowed(('GET','HEAD'))
    if page == 'start':
        if request.user.is_authenticated:
            return HttpResponseRedirect('/demo/?tour=1')
        with _cache_lock:
            cache = security_cache()
            count = cache.get('demo-start-hour', 0)
            if count >= 40:
                return limited_response(3600)
            cache.set('demo-start-hour', count + 1, 3600)
        with transaction.atomic(using='demo'):
            if get_user_model().objects.filter(username__startswith='demo_').count() >= 40:
                return limited_response(300)
            user = get_user_model().objects.create(username='demo_'+secrets.token_hex(16),
                password='!', is_staff=False, is_superuser=False)
            seed_story(user, lang=request.demo_lang)
            session = request.session
            session['_auth_user_id'] = str(user.pk)
            session['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
            session['_auth_user_hash'] = user.get_session_auth_hash()
            session['_account_epoch'] = 0
            session['demo_lang'] = request.demo_lang
            session.set_expiry(7200)
            session.create()
        return cookie_response(HttpResponseRedirect('/demo/?tour=1'), session)
    user = request.user
    if page == 'sprache':
        lang = normalize_language(request.POST.get('language')) or 'de'
        request.session['demo_lang'] = lang
        request.session.save()
        seed_story(user, lang=lang)
        next_url = request.POST.get('next') or '/demo/'
        if not next_url.startswith('/demo/') or not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            next_url = '/demo/'
        response = HttpResponseRedirect(next_url)
        response.set_cookie(settings.LANGUAGE_COOKIE_NAME, lang, max_age=365*86400, path='/', secure=settings.SESSION_COOKIE_SECURE, samesite='Lax')
        return response
    if page == 'ende':
        with transaction.atomic(using='demo'):
            request.session.flush()
            clear_story(user)
            user.delete(using='demo')
        response = HttpResponseRedirect('/login/')
        response.delete_cookie(COOKIE, path='/demo/', samesite='Lax')
        return response
    if page == 'reset':
        seed_story(user, lang=request.demo_lang)
        return HttpResponseRedirect('/demo/?tour=1')
    project = Project.objects.get(owner=user)
    if page == 'erfassen':
        return capture(request, project)
    if page == 'beleg':
        try: document_id = uuid.UUID(request.GET.get('id', ''))
        except (ValueError, AttributeError): return HttpResponse('Nicht gefunden.', status=404)
        document = Document.objects.filter(owner=user, pk=document_id).first()
        if document is None: return HttpResponse('Nicht gefunden.', status=404)
        # Generated synthetic PDF only. Never reads a file or calls Paperless.
        from .demo import synthetic_pdf
        lines = ['PROJEKTAKTE - MUSTERBELEG', 'Alle Daten sind frei erfunden.',
                 str(document.proposal.get('invoice_number', 'MUSTER')), 'Beispielzubehoer',
                 'Betrag: '+str(document.proposal.get('gross_total', '49.90'))+' EUR', 'Kein echter Beleg.']
        response = HttpResponse(synthetic_pdf(chr(10).join(lines)), content_type='application/pdf')
        response['Content-Disposition'] = 'inline; filename="musterbeleg.pdf"'
        response['Content-Security-Policy'] = "sandbox; default-src 'none'"
        return response
    entries = project.entries.select_related('category').order_by('-created')
    query = request.GET.get('q','')[:120]
    if page == 'suche' and query: entries = entries.filter(title__icontains=query) | entries.filter(description__icontains=query)
    return render(request, 'public_demo.html', {'page':page, 'demo_nav':NAV, 'project':project,
        'entries':entries, 'documents':Document.objects.filter(owner=user).order_by('-created'),
        'query':query, 'demo_result':request.GET.get('saved')=='1', 'demo_user_key':str(user.pk), 'demo_generation':str(project.pk), 'demo_lang':request.demo_lang})


def capture(request, project):
    """Transparent deterministic simulation: no model, audio decoder or file storage."""
    action = request.POST.get('action', '')
    if action == 'review':
        try:
            document_id = uuid.UUID(request.POST.get('document', ''))
        except (ValueError, AttributeError):
            return HttpResponse('Ungültiger Beleg.', status=400)
        with transaction.atomic(using='demo'):
            document = Document.objects.filter(pk=document_id, owner=request.user, project=project,
                instance='public-demo', deleted_at=None).first()
            if document is None:
                return HttpResponse('Nicht gefunden.', status=404)
            if document.status == 'accepted':
                return HttpResponseRedirect('/demo/belege/?confirmed=1')
            if not document.voice_data.get('demo_assets') or document.entry_id is None or document.entry.project_id != project.pk:
                return HttpResponse('Bitte die Beispielrechnung aus der Tour verwenden.', status=400)
            try:
                amount = Decimal(str(document.proposal.get('gross_total', '')))
                if not amount.is_finite() or amount <= 0 or amount > 10000 or amount != amount.quantize(Decimal('.01')):
                    raise ValueError
            except (InvalidOperation, ValueError):
                return HttpResponse('Ungültiger Betrag.', status=400)
            source, created = CostSource.objects.get_or_create(document=document, defaults={
                'owner': request.user, 'label': document.entry.title, 'amount': amount})
            if created:
                Allocation.objects.create(source=source, project=project, entry=document.entry, amount=amount)
            document.status = 'accepted'
            document.save(update_fields=['status'])
            if document.entry.kind == 'wartung' and document.entry.date:
                MaintenanceRule.objects.filter(project=project, title='Ölwechsel').update(
                    base_date=document.entry.date, base_meter=document.entry.meter)
        return HttpResponseRedirect('/demo/belege/?confirmed=1')
    if action == 'work-photo':
        with transaction.atomic(using='demo'):
            entry = project.entries.filter(kind='umbau').order_by('-created').first()
            if entry is None:
                return HttpResponse('Kein Beispiel-Umbau vorhanden.', status=404)
            entry.snapshot = {**entry.snapshot, 'demo_work_photo': True}
            entry.save(update_fields=['snapshot'])
        return HttpResponseRedirect('/demo/chronik/?photo=1')
    text = request.POST.get('note','').strip()[:2000]
    files = request.FILES.getlist('files')
    if len(files)>4 or sum(f.size for f in files)>LIMIT:
        return HttpResponse('Maximal vier Dateien und insgesamt 2 MB.', status=413)
    for file in files:
        header = file.read(16)
        file.seek(0)
        allowed = header.startswith((b'%PDF-', b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n', b'RIFF', b'OggS', b'ID3', b'\x1aE\xdf\xa3')) or header[4:8] == b'ftyp'
        if not allowed: return HttpResponse('Bitte Foto, PDF oder Audio verwenden.', status=400)
    if not text and not files:
        return HttpResponseRedirect('/demo/')
    with transaction.atomic(using='demo'):
        if project.entries.count() >= 24:
            return HttpResponse('Demo-Limit erreicht. Bitte die Demo zurücksetzen.', status=429)
        key = request.POST.get('request_key','')
        if not re.fullmatch(r'[0-9a-f]{32}',key): return HttpResponse('Ungültige Erfassung.',status=400)
        request_key = uuid.UUID(key)
        if Entry.objects.filter(project=project,request_key=request_key).exists():
            return HttpResponseRedirect('/demo/chronik/?saved=1')
        lower = text.casefold()
        kind, title = ('wartung','Ölwechsel') if 'öl' in lower or 'filter' in lower else ('reparatur','Bremsen kontrolliert') if 'brems' in lower else ('umbau','Dachträger montiert') if 'dach' in lower or 'träger' in lower else ('notiz','Unterwegs festgehalten')
        is_task = 'kontrollieren' in lower and ('urlaub' in lower or 'aufgabe' in lower)
        if is_task:
            kind, title = 'notiz', 'Dachträger vor dem Urlaub kontrollieren'
        category = Category.objects.get(project=project,default_kind=kind)
        meter = re.search(r'\b(\d{2,7})\s*km\b',lower)
        amount = re.search(r'\b(\d{1,5}(?:[.,]\d{1,2})?)\s*(?:€|eur|\$|usd|£|gbp|chf)',lower)
        template = category.title_templates.first() if kind=='wartung' else None
        oil_grade = re.search(r'\b\d{1,2}w-\d{2}\b', lower)
        oil_quantity = re.search(r'\b(\d{1,2}(?:[.,]\d{1,2})?)\s*liter\b', lower)
        fields = {'Erkennung': 'Simulierter Vorschlag · bitte prüfen', 'Dateien': str(len(files))+' · nicht gespeichert'}
        if template and oil_grade: fields['Motoröl'] = oil_grade[0].upper()
        if template and oil_quantity: fields['Ölmenge'] = oil_quantity[1]+' Liter'
        entry = Entry.objects.create(project=project,category=category,kind=kind,title=title,title_template=template,
            request_key=request_key, date=timezone.localdate() if 'heute' in lower else None,
            meter=int(meter[1]) if meter else None, status='', description=text or 'Demo-Datei erfasst. In der echten App übernimmt Gemini die Inhaltsauswertung.',
            snapshot={'demo_fields': fields,'demo_assets': request.POST.get('example') == '1'})
        if is_task:
            Task.objects.create(project=project, entry=entry, title=title)
        if amount:
            Document.objects.create(owner=request.user,project=project,entry=entry,status='review',instance='public-demo',ai_enabled=False,
                scan_note='Simulierter Belegentwurf · noch keine Kosten gebucht',proposal={'gross_total':amount[1].replace(',','.'),'currency':'EUR','invoice_number':'DEMO-2026-042', 'vendor':'Nordlicht Werkstatt' if request.POST.get('example') == '1' else ''}, voice_data={'demo_assets': request.POST.get('example') == '1'})
    return HttpResponseRedirect('/demo/chronik/?saved=1')
