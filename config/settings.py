import os
import re
from pathlib import Path
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get('DATA_DIR', BASE_DIR / 'data')).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
def server_secret(name):
    value = os.environ.get(name, '')
    filename = os.environ.get(name + '_FILE', '')
    if value and filename:
        raise RuntimeError(name + ': entweder Wert oder Serverdatei konfigurieren.')
    if filename:
        try:
            raw = Path(filename).read_bytes()
            if not 1 <= len(raw) <= 8192:
                raise ValueError()
            value = raw.decode('utf-8').strip()
        except (OSError, UnicodeError, ValueError):
            raise RuntimeError(name + ': geschützte Serverdatei nicht lesbar.') from None
    return value

SECRET_KEY = server_secret('SECRET_KEY')
DEBUG = os.environ.get('DEBUG', '0') == '1'
DEMO_MODE = os.environ.get('DEMO_MODE', '0') == '1'
if not SECRET_KEY:
    if DEMO_MODE:
        # Persistent random key, local demo only. Never a hard-coded production key.
        import secrets
        keyfile = DATA_DIR / '.demo-key'
        if not keyfile.exists():
            try:
                with keyfile.open('x', encoding='ascii') as f:
                    f.write(secrets.token_urlsafe(64))
            except FileExistsError:
                pass
        SECRET_KEY = keyfile.read_text(encoding='ascii').strip()
    else:
        from akte.updates import ensure_secret_key
        SECRET_KEY = ensure_secret_key(DATA_DIR)
configured_hosts = os.environ.get('ALLOWED_HOSTS', '').split(',')
PUBLIC_HOST = os.environ.get('PUBLIC_HOST', '').strip() or next((
    host.strip() for host in configured_hosts
    if host.strip() and host.strip() not in ('localhost', '127.0.0.1', '[::1]')
), 'localhost')
ALLOWED_HOSTS = [host.strip() for host in os.environ.get('ALLOWED_HOSTS', '127.0.0.1,localhost,[::1]' if DEMO_MODE else PUBLIC_HOST).split(',') if host.strip()]
if not ALLOWED_HOSTS or any('*' in host or host.startswith('.') for host in ALLOWED_HOSTS):
    raise RuntimeError('ALLOWED_HOSTS muss ausdrücklich erlaubte Hosts enthalten.')
CSRF_TRUSTED_ORIGINS = [x.strip() for x in os.environ.get('CSRF_TRUSTED_ORIGINS', '' if DEMO_MODE else 'https://' + PUBLIC_HOST).split(',') if x.strip()]
INSTALLED_APPS = ['django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles','akte.apps.AkteConfig']
MIDDLEWARE = [
    'akte.security.SecurityHeadersMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'akte.security.PreAuthBodyMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.middleware.gzip.GZipMiddleware',
    'akte.public_demo.DemoBoundaryMiddleware',
    'akte.security.GuardedSessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'akte.i18n.UserLanguageMiddleware',
    'akte.security.PreAuthRateMiddleware',
    'akte.security.AuthenticationBoundaryMiddleware',
    'akte.middleware.MaintenanceMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'akte.account_events.DeviceMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'projektakte'},
    'security': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'projektakte-security',
                 'TIMEOUT': 900, 'OPTIONS': {'MAX_ENTRIES': 4096, 'CULL_FREQUENCY': 3}},
}
DATABASE_ROUTERS = ['akte.demo_boundary.DemoRouter', 'akte.snapshots.SnapshotRouter']
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[BASE_DIR/'templates'],'APP_DIRS':True,'OPTIONS':{'context_processors':['django.template.context_processors.request','django.template.context_processors.i18n','django.contrib.auth.context_processors.auth','django.contrib.messages.context_processors.messages','akte.context.common']}}]
WSGI_APPLICATION = 'config.wsgi.application'
DATABASES = {'default': {'ENGINE':'django.db.backends.sqlite3','NAME':DATA_DIR/'db.sqlite3','OPTIONS':{'timeout':20,'transaction_mode':'IMMEDIATE'}}}
AUTH_PASSWORD_VALIDATORS = [{'NAME':'django.contrib.auth.password_validation.MinimumLengthValidator','OPTIONS':{'min_length':12}},{'NAME':'django.contrib.auth.password_validation.CommonPasswordValidator'}]
LANGUAGES = [('de', 'Deutsch'), ('en', 'English')]
DEFAULT_LANGUAGE = os.environ.get('DEFAULT_LANGUAGE', 'de').strip().lower().split('-', 1)[0] or 'de'
if DEFAULT_LANGUAGE not in ('de', 'en'):
    raise RuntimeError('DEFAULT_LANGUAGE muss "de" oder "en" sein.')
LANGUAGE_CODE = DEFAULT_LANGUAGE
LOCALE_PATHS = [BASE_DIR / 'locale']
TIME_ZONE = (os.environ.get('TIME_ZONE') or os.environ.get('TZ') or 'Europe/Berlin').strip()
try:
    ZoneInfo(TIME_ZONE)
except (ZoneInfoNotFoundError, KeyError, ValueError):
    raise RuntimeError(f'TIME_ZONE "{TIME_ZONE}" ist keine gültige IANA-Zeitzone (z. B. Europe/Berlin oder UTC).') from None
DEFAULT_CURRENCY = os.environ.get('DEFAULT_CURRENCY', 'EUR').strip().upper() or 'EUR'
if not re.fullmatch(r'[A-Z]{3}', DEFAULT_CURRENCY):
    raise RuntimeError('DEFAULT_CURRENCY muss ein dreistelliger ISO-4217-Code sein (z. B. EUR, USD, GBP, CHF).')
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR/'staticfiles'
STATICFILES_DIRS = [BASE_DIR/'static']
WHITENOISE_USE_FINDERS = DEMO_MODE
WHITENOISE_MAX_AGE = 0 if DEMO_MODE else 31536000
MEDIA_ROOT = DATA_DIR/'media'
MEDIA_ROOT.mkdir(parents=True,exist_ok=True)
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = os.environ.get('HTTPS', '0' if DEMO_MODE else '1') == '1'
ALLOW_INSECURE_HTTP = os.environ.get('ALLOW_INSECURE_HTTP', '0') == '1'
if not DEMO_MODE and DEBUG:
    raise RuntimeError('Livebetrieb benötigt DEBUG=0.')
if not DEMO_MODE and not SESSION_COOKIE_SECURE and not ALLOW_INSECURE_HTTP:
    raise RuntimeError('Livebetrieb benötigt HTTPS=1 (oder ausdrücklich ALLOW_INSECURE_HTTP=1 für reines Heimnetz).')
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
CSRF_COOKIE_HTTPONLY = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO','https') if SESSION_COOKIE_SECURE else None
SECURE_SSL_REDIRECT = SESSION_COOKIE_SECURE
SECURE_REDIRECT_EXEMPT = []
SECURE_HSTS_SECONDS = 31536000 if SESSION_COOKIE_SECURE else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = SESSION_COOKIE_SECURE
SECURE_HSTS_PRELOAD = SESSION_COOKIE_SECURE
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
FILE_UPLOAD_MAX_MEMORY_SIZE = 512*1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 2*1024*1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 2500
MAX_PHOTO_BYTES = int(os.environ.get('MAX_PHOTO_MB','20'))*1024*1024
MAX_IMAGE_PIXELS = int(os.environ.get('MAX_IMAGE_PIXELS','20000000'))
if not 1_000_000<=MAX_IMAGE_PIXELS<=40_000_000: raise RuntimeError('MAX_IMAGE_PIXELS muss zwischen 1 und 40 Millionen liegen.')
MAX_DOCUMENT_BYTES = int(os.environ.get('MAX_DOCUMENT_MB','50'))*1024*1024
PAPERLESS_URL = os.environ.get('PAPERLESS_URL','').rstrip('/')
PAPERLESS_TOKEN = server_secret('PAPERLESS_TOKEN')
PAPERLESS_API_VERSION = os.environ.get('PAPERLESS_API_VERSION','9')
FILE_UPLOAD_TEMP_DIR = DATA_DIR/'tmp'
FILE_UPLOAD_TEMP_DIR.mkdir(exist_ok=True)
ALLOWED_GEMINI_MODELS = ('gemini-3.5-flash-lite', 'gemini-3.8-flash')
GEMINI_API_BASE = os.environ.get('GEMINI_API_BASE','https://generativelanguage.googleapis.com/v1beta')
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', ALLOWED_GEMINI_MODELS[0]).strip()
if GEMINI_MODEL not in ALLOWED_GEMINI_MODELS:
    raise RuntimeError('GEMINI_MODEL muss eines der unterstützten Modelle sein: ' + ', '.join(ALLOWED_GEMINI_MODELS) + '.')
GEMINI_API_KEY = server_secret('GEMINI_API_KEY')
AI_ENABLED = os.environ.get('AI_ENABLED','1') == '1'
FEEDBACK_WIDGET_ENABLED = os.environ.get('FEEDBACK_WIDGET_ENABLED', '0') == '1'
AI_MONTHLY_WARN = Decimal(os.environ.get('AI_MONTHLY_WARN','1'))
AI_MONTHLY_LIMIT = Decimal(os.environ.get('AI_MONTHLY_LIMIT','2'))
# Live pricing must be consciously configured. A reservation remains charged on timeout.
AI_INPUT_USD_PER_M = Decimal(os.environ.get('AI_INPUT_USD_PER_M','0'))
AI_OUTPUT_USD_PER_M = Decimal(os.environ.get('AI_OUTPUT_USD_PER_M','0'))
AI_REQUEST_RESERVE = Decimal(os.environ.get('AI_REQUEST_RESERVE','0.05'))
AI_MAX_OUTPUT = 4096
GEMINI_ORIGINAL_MAX_BYTES = int(os.environ.get('GEMINI_ORIGINAL_MAX_MB','8'))*1024*1024
if not 0<GEMINI_ORIGINAL_MAX_BYTES<=12*1024*1024: raise RuntimeError('GEMINI_ORIGINAL_MAX_MB muss zwischen 1 und 12 liegen.')
AI_VISION_INPUT_RESERVE = int(os.environ.get('AI_VISION_INPUT_RESERVE','1100000'))
APP_VERSION = '1.0.0'

for name in ('AI_MONTHLY_WARN','AI_MONTHLY_LIMIT','AI_INPUT_USD_PER_M','AI_OUTPUT_USD_PER_M','AI_REQUEST_RESERVE'):
    value=globals()[name]
    if not value.is_finite() or value<0: raise RuntimeError(name+' muss endlich und nicht negativ sein.')
if AI_MONTHLY_WARN>AI_MONTHLY_LIMIT: raise RuntimeError('AI_MONTHLY_WARN darf AI_MONTHLY_LIMIT nicht überschreiten.')

# ReportLab retains PDF objects until publication; bound one report on the 2 GB Pi.
PDF_MAX_ENTRIES = int(os.environ.get('PDF_MAX_ENTRIES','500'))
PDF_MAX_PHOTOS = int(os.environ.get('PDF_MAX_PHOTOS','200'))
PDF_MAX_TEXT_CHARS = int(os.environ.get('PDF_MAX_TEXT_CHARS','500000'))
if min(PDF_MAX_ENTRIES,PDF_MAX_PHOTOS,PDF_MAX_TEXT_CHARS)<1: raise RuntimeError('PDF-Grenzen müssen positiv sein.')

LOGGING = {'version':1,'disable_existing_loggers':False,
 'handlers':{'safe_events':{'class':'akte.safe_logging.SafeErrorHandler','level':'WARNING'}},
 'loggers':{'django.request':{'handlers':['safe_events'],'level':'WARNING','propagate':True},
            'akte.mail_ingest':{'handlers':['safe_events'],'level':'WARNING','propagate':True}}}

# Public demo is a separate database; it never uses live workers or media.
DATABASES['demo'] = {**DATABASES['default'], 'NAME': DATA_DIR / 'demo.sqlite3', 'OPTIONS': {'timeout': 3, 'transaction_mode': 'IMMEDIATE'}}
