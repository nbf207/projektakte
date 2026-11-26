from django.apps import AppConfig
from django.db.backends.signals import connection_created

def configure_sqlite(sender, connection, **kwargs):
    if connection.vendor == 'sqlite':
        with connection.cursor() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.execute('PRAGMA synchronous=FULL')

class AkteConfig(AppConfig):
    name = 'akte'
    def ready(self):
        import warnings
        from PIL import Image
        from django.conf import settings
        Image.MAX_IMAGE_PIXELS=settings.MAX_IMAGE_PIXELS
        warnings.simplefilter('error',Image.DecompressionBombWarning)
        from . import account_events
        connection_created.connect(configure_sqlite, dispatch_uid='akte-sqlite')
