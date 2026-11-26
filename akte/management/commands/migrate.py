from django.core.management.commands.migrate import Command as DjangoMigrateCommand
from akte.operations import operation_gate


class Command(DjangoMigrateCommand):
    def handle(self,*args,**options):
        with operation_gate():
            return super().handle(*args,**options)
