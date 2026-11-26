"""Bilingual (DE/EN) language & currency helpers, middleware, and switcher view."""
from __future__ import annotations

import re
from contextvars import ContextVar
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.http import HttpResponseRedirect
from django.utils import translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

_active_currency: ContextVar[str] = ContextVar('projektakte_active_currency', default='')

SUPPORTED_LANGUAGES = ('de', 'en')
LANGUAGE_CHOICES = (
    ('', 'Automatisch (Browser)'),
    ('de', 'Deutsch'),
    ('en', 'English'),
)
COMMON_CURRENCIES = (
    'EUR', 'USD', 'GBP', 'CHF', 'CAD', 'AUD', 'JPY', 'PLN', 'SEK', 'NOK', 'DKK', 'CZK',
)
CURRENCY_SYMBOLS = {
    'EUR': '€',
    'USD': '$',
    'GBP': '£',
    'CHF': 'CHF',
    'CAD': 'CA$',
    'AUD': 'A$',
    'JPY': '¥',
    'PLN': 'zł',
    'SEK': 'kr',
    'NOK': 'kr',
    'DKK': 'kr',
    'CZK': 'Kč',
}
PREFIX_CURRENCIES = frozenset(('USD', 'GBP', 'CAD', 'AUD', 'JPY'))


def normalize_language(value: str | None) -> str:
    if not value:
        return ''
    short = str(value).strip().lower().split('-', 1)[0].split('_', 1)[0]
    return short if short in SUPPORTED_LANGUAGES else ''


def normalize_currency(value: str | None) -> str:
    if not value:
        return ''
    code = str(value).strip().upper()
    return code if re.fullmatch(r'[A-Z]{3}', code) else ''


def effective_language(user=None, request=None) -> str:
    if user is not None and getattr(user, 'is_authenticated', False):
        from .preferences import preferences
        saved = normalize_language(preferences(user).language)
        if saved:
            return saved
    if request is not None:
        req_lang = normalize_language(getattr(request, 'LANGUAGE_CODE', ''))
        if req_lang:
            return req_lang
    active = normalize_language(translation.get_language())
    if active:
        return active
    return normalize_language(getattr(settings, 'DEFAULT_LANGUAGE', settings.LANGUAGE_CODE)) or 'de'


def effective_currency(user=None) -> str:
    if user is not None and getattr(user, 'is_authenticated', False):
        from .preferences import preferences
        saved = normalize_currency(preferences(user).currency)
        if saved:
            return saved
    return normalize_currency(getattr(settings, 'DEFAULT_CURRENCY', 'EUR')) or 'EUR'


def currency_symbol(code: str | None = None) -> str:
    normalized = normalize_currency(code) or normalize_currency(getattr(settings, 'DEFAULT_CURRENCY', 'EUR')) or 'EUR'
    return CURRENCY_SYMBOLS.get(normalized, normalized)


def format_number(value, lang: str | None = None) -> str:
    if value is None or value == '':
        return '—'
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    text = f'{dec:,.3f}'.rstrip('0').rstrip('.')
    active = normalize_language(lang) or normalize_language(translation.get_language()) or 'de'
    if active == 'en':
        return text
    return text.replace(',', 'X').replace('.', ',').replace('X', '.')


def format_money(value, currency: str | None = None, lang: str | None = None, use_symbol: bool = True) -> str:
    if value is None or value == '':
        return str(translation.gettext('Unbekannt'))
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    code = normalize_currency(currency) or normalize_currency(_active_currency.get()) or normalize_currency(getattr(settings, 'DEFAULT_CURRENCY', 'EUR')) or 'EUR'
    symbol = currency_symbol(code) if use_symbol else code
    active = normalize_language(lang) or normalize_language(translation.get_language()) or 'de'
    if active == 'en':
        sign = '-' if dec < 0 else ''
        formatted = f'{abs(dec):,.2f}'
        if use_symbol and (code in PREFIX_CURRENCIES or code == 'EUR'):
            return f'{sign}{symbol}{formatted}'
        return f'{sign}{formatted} {symbol}'
    formatted = f'{dec:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return f'{formatted} {symbol}'


class UserLanguageMiddleware:
    """Activate authenticated user's persisted language preference if configured."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        curr_token = _active_currency.set(effective_currency(user))
        try:
            if user is not None and user.is_authenticated:
                from .preferences import preferences
                saved = normalize_language(preferences(user).language)
                if saved:
                    translation.activate(saved)
                    request.LANGUAGE_CODE = saved
            response = self.get_response(request)
            if 'Content-Language' not in response:
                response['Content-Language'] = translation.get_language() or settings.LANGUAGE_CODE
            return response
        finally:
            _active_currency.reset(curr_token)


@require_POST
def set_language(request):
    """Switch UI language via cookie (and persist to Preferences when signed in)."""
    raw = request.POST.get('language', '')
    lang = normalize_language(raw)
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or '/'
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = '/'

    if getattr(request, 'user', None) and request.user.is_authenticated:
        from .models import Preferences
        pref, _ = Preferences.objects.get_or_create(owner=request.user)
        pref.language = lang
        pref.version += 1
        pref.save(update_fields=['language', 'version'])

    response = HttpResponseRedirect(next_url)
    cookie_name = settings.LANGUAGE_COOKIE_NAME
    if lang:
        response.set_cookie(
            cookie_name,
            lang,
            max_age=365 * 86400,
            path='/',
            secure=settings.SESSION_COOKIE_SECURE,
            samesite='Lax',
        )
    else:
        response.delete_cookie(cookie_name, path='/')
    return response
