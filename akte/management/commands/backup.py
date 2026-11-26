from django.core.management.base import BaseCommand,CommandError
from django.conf import settings
from akte.backup import backup
class Command(BaseCommand):
    help='Konsistente App-Sicherung unter Prozesssperre. Gemeinsamen Paperless-Stand separat koordinieren.'
    def add_arguments(self,p):
        p.add_argument('destination');p.add_argument('--offline',action='store_true');p.add_argument('--paperless-bundle')
    def handle(self,*args,**o):
        # --offline remains accepted for older maintenance scripts. The actual
        # application write gate is enforced regardless of this flag.
        if not settings.DEMO_MODE and settings.PAPERLESS_URL and not o['paperless_bundle']:
            raise CommandError('Live-Sicherung mit aktiviertem Paperless braucht zusammengehöriges Paperless-Bundle (--paperless-bundle). Konfiguration separat geschützt sichern.')
        try: self.stdout.write(str(backup(o['destination'],o['paperless_bundle'])))
        except (ValueError,OSError) as e: raise CommandError(str(e))
