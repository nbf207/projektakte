from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from akte.onboarding import create_owner_invitation

class Command(BaseCommand):
    help='Ein persönliches Konto mit einmaligem 24-Stunden-Einrichtungslink anlegen.'
    def add_arguments(self,parser):
        parser.add_argument('username')
        parser.add_argument('--base-url',required=True)
    def handle(self,*args,**options):
        base=options['base_url'].rstrip('/')
        if not (base.startswith('https://') or base.startswith('http://127.0.0.1:')):
            raise CommandError('HTTPS oder lokaler SSH-Tunnel erforderlich.')
        try:
            invite,token=create_owner_invitation(options['username'])
        except ValidationError as exc:
            raise CommandError('; '.join(exc.messages)) from None
        self.stdout.write(base+'/einrichten/'+str(invite.pk)+'/'+token+'/')
