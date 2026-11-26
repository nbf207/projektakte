from django.core.management.base import BaseCommand,CommandError
from akte.backup import restore
class Command(BaseCommand):
    def add_arguments(self,p): p.add_argument('archive');p.add_argument('target')
    def handle(self,*args,**o):
        try: self.stdout.write(str(restore(o['archive'],o['target'])))
        except (ValueError,OSError) as e: raise CommandError(str(e))
