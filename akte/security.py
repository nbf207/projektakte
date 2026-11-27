"""Central pre-auth boundary; no anonymous writes, private file serving or opt-out decorators."""
import hashlib
import ipaddress
import math
import time
import threading
from types import SimpleNamespace
from pathlib import Path

from django.conf import settings
from django.contrib.auth.views import LoginView, redirect_to_login
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import caches
from django.core.exceptions import DisallowedHost
from django.http import HttpResponse, HttpResponseNotAllowed, HttpResponseRedirect
from django.urls import Resolver404, resolve
from django.utils import timezone
from django.utils.http import content_disposition_header

from .operations import operation_gate

LOGIN_PATHS = frozenset(('/login/', '/anmelden/'))
PUBLIC_VIEWS = {'login': '/login/', 'first_run_setup': '/setup/', 'set_language': '/sprache/', 'manifest': '/manifest.json', 'service_worker': '/sw.js'}
# No bearer-link feature exists. Never exempt an arbitrary /teilen/<token>/ prefix.
# Future share routes must be reviewed and registered by exact route name here,
# and must validate token expiry + explicit file membership in their own view.
PUBLIC_SHARE_VIEWS = frozenset()
_cache_lock = threading.RLock()

INLINE_TYPES = frozenset(('image/jpeg', 'image/png', 'image/webp', 'image/gif', 'application/pdf',
                         'video/mp4', 'audio/webm', 'audio/ogg', 'audio/mpeg', 'audio/mp4',
                         'audio/wav', 'audio/x-wav', 'audio/flac'))


def security_cache():
    return caches['security']


def client_ip(request):
    """Preserved peer address + WSGI-verified Cloudflare header, never raw XFF."""
    raw = request.META.get('HTTP_CF_CONNECTING_IP') if request.META.get('projektakte.trusted_proxy') else None
    raw = raw or request.META.get('REMOTE_ADDR', '')
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        try:
            return str(ipaddress.ip_address(request.META.get('REMOTE_ADDR', '')))
        except ValueError:
            return 'unknown'


def ip_key(request, purpose):
    digest = hashlib.sha256(client_ip(request).encode()).hexdigest()
    return 'preauth:' + purpose + ':' + digest


def failure_history():
    return [SimpleNamespace(**row) for row in security_cache().get('login-history', [])]


def record_failure(request):
    # No username lookup, signal-triggered SQLite writes, passwords or bodies.
    from .account_events import browser_name
    cache = security_cache()
    with _cache_lock:
        rows = cache.get('login-history', [])
        rows.insert(0, {'created': timezone.now(), 'success': False, 'browser': browser_name(request)})
        cache.set('login-history', rows[:10], timeout=86400)


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            request.get_host()  # Enforce ALLOWED_HOSTS even for static/unknown URLs.
        except DisallowedHost:
            response = HttpResponse('Ungültiger Host.', status=400)
        else:
            response = self.get_response(request)
        response['X-Robots-Tag'] = 'noindex, nofollow'
        response['X-Content-Type-Options'] = 'nosniff'
        response['Referrer-Policy'] = 'same-origin'
        if not request.path_info.startswith('/static/'):
            response['Cache-Control'] = 'no-store, private'
        if response.streaming and not request.path_info.startswith('/static/'):
            mime = response.get('Content-Type', '').split(';', 1)[0].lower()
            is_attachment = response.get('Content-Disposition', '').startswith('attachment')
            if mime == 'application/pdf' and not is_attachment:
                response['Content-Security-Policy'] = "default-src 'none'; frame-ancestors 'self'; object-src 'self'; style-src 'unsafe-inline'"
            else:
                response['Content-Security-Policy'] = "sandbox; default-src 'none'"
            filename = getattr(response, 'filename', '')
            active = Path(filename).suffix.lower() in ('.html', '.htm', '.svg', '.xml', '.xhtml', '.js', '.mht')
            if mime not in INLINE_TYPES or active:
                response['Content-Type'] = 'application/octet-stream'
                if not response.get('Content-Disposition', '').startswith('attachment'):
                    response['Content-Disposition'] = content_disposition_header(True, filename) or 'attachment'
        elif not request.path_info.startswith('/static/') and 'Content-Security-Policy' not in response:
            response['Content-Security-Policy'] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self'; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
                "form-action 'self'; object-src 'none'"
            )
        return response


class PreAuthBodyMiddleware:
    """Header-only rejection before sessions, form parsing or application locks."""
    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def reject_large_body(request):
        if request.META.get('HTTP_TRANSFER_ENCODING') or request.META.get('HTTP_X_PROJEKTAKTE_CHUNKED') == '1':
            return HttpResponse('Chunked benötigt eine Anmeldung.', status=400)
        value = request.META.get('CONTENT_LENGTH', '')
        try:
            size = int(value) if value else 0
        except ValueError:
            return HttpResponse('Ungültige Anfragegröße.', status=400)
        if size < 0:
            return HttpResponse('Ungültige Anfragegröße.', status=400)
        if size > 65536:
            return HttpResponse('Anfrage zu groß.', status=413)
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and not value:
            return HttpResponse('Content-Length erforderlich.', status=411)
        return None

    def __call__(self, request):
        if request.path_info in LOGIN_PATHS:
            response = self.reject_large_body(request)
            if response is not None:
                return response
        return self.get_response(request)


def limited_response(seconds):
    response = HttpResponse('Zu viele Versuche. Bitte später erneut versuchen.', status=429)
    response['Retry-After'] = str(max(1, math.ceil(seconds)))
    return response


class PreAuthRateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        public_share = path == '/teilen/' or path.startswith('/teilen/')
        if path.startswith('/demo/'):
            response = self.request_budget(request, 'demo-requests', time.time())
            if response is not None: return response
            if request.method == 'POST' and path != '/demo/sprache/':
                cache = security_cache()
                key = ip_key(request, 'demo-post')
                with _cache_lock:
                    if not cache.add(key, True, timeout=3): return limited_response(3)
        if not request.user.is_authenticated:
            response = PreAuthBodyMiddleware.reject_large_body(request)
            if response is not None:
                return response
        cache = security_cache()
        now = time.time()
        if public_share:
            response = self.request_budget(request, 'share', now)
            if response is not None:
                return response
        if path in ('/setup/', '/sprache/'):
            if request.method not in ('GET', 'HEAD', 'POST'):
                return HttpResponseNotAllowed(('GET', 'HEAD', 'POST'))
            response = self.request_budget(request, 'setup-requests' if path == '/setup/' else 'language-requests', now)
            if response is not None:
                return response
            return self.get_response(request)
        if path not in LOGIN_PATHS:
            return self.get_response(request)
        if request.method not in ('GET', 'HEAD', 'POST'):
            return HttpResponseNotAllowed(('GET', 'HEAD', 'POST'))
        response = self.request_budget(request, 'login-requests', now)
        if response is not None:
            return response
        if request.method != 'POST':
            return self.get_response(request)
        key = ip_key(request, 'login-failures')
        inflight = ip_key(request, 'login-inflight')
        with _cache_lock:
            start, failures, next_attempt = cache.get(key, (now, 0, 0))
            if now - start >= 900:
                start, failures, next_attempt = now, 0, 0
            if failures >= 8:
                return limited_response(900 - (now - start))
            if now < next_attempt:
                return limited_response(next_attempt - now)
            if not cache.add(inflight, True, timeout=60):
                return limited_response(1)
        try:
            response = self.get_response(request)
            if getattr(request, '_login_succeeded', False):
                cache.delete(key)
            else:
                # Short backoff rejects at the next request; never sleep on either
                # of the two Pi web threads, and never lock the account by name.
                cache.set(key, (start, failures + 1, time.time() + 0.5), timeout=900)
            return response
        finally:
            cache.delete(inflight)

    @staticmethod
    def request_budget(request, purpose, now):
        cache = security_cache()
        key = ip_key(request, purpose)
        with _cache_lock:
            start, count = cache.get(key, (now, 0))
            if now - start >= 60:
                start, count = now, 0
            if count >= 60:
                return limited_response(60 - (now - start))
            cache.set(key, (start, count + 1), timeout=60)
        return None


class AuthenticationBoundaryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        if path == '/admin' or path.startswith('/admin/'):
            if not request.user.is_authenticated or not (request.user.is_staff or request.user.is_superuser):
                return HttpResponse('Nicht gefunden.', status=404)
        try:
            match = resolve(path)
        except Resolver404:
            match = None
        if match and PUBLIC_VIEWS.get(match.url_name) == path:
            return self.get_response(request)
        if match is None and path.startswith('/teilen/'):
            if request.method not in ('GET', 'HEAD'):
                return HttpResponseNotAllowed(('GET', 'HEAD'))
            return HttpResponse('Nicht gefunden.', status=404)
        if path == '/anmelden/':
            # Compatibility URL exposes no second login form or arbitrary next URL.
            return HttpResponseRedirect(settings.LOGIN_URL)
        if match and match.url_name in PUBLIC_SHARE_VIEWS:
            if request.method not in ('GET', 'HEAD'):
                return HttpResponseNotAllowed(('GET', 'HEAD'))
            return self.get_response(request)
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)
        return self.get_response(request)


class GuardedSessionMiddleware(SessionMiddleware):
    def process_response(self, request, response):
        if getattr(getattr(request, 'session', None), 'modified', False):
            with operation_gate():
                return super().process_response(request, response)
        return super().process_response(request, response)


class SecureLoginView(LoginView):
    def dispatch(self, request, *args, **kwargs):
        if not settings.DEMO_MODE:
            from django.contrib.auth import get_user_model
            if not get_user_model().objects.exists():
                return HttpResponseRedirect('/setup/')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        with operation_gate():
            response = super().form_valid(form)
            self.request._login_succeeded = True
            return response
