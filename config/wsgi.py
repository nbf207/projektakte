import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
from django.core.wsgi import get_wsgi_application
from akte.proxy_boundary import ProxyHeaderBoundary
application = ProxyHeaderBoundary(get_wsgi_application())
