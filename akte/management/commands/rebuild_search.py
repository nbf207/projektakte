"""Rebuild after an older SQLite fallback or future schema changes."""
from importlib import import_module
from django.core.management.base import BaseCommand
from django.db import connection,transaction
from akte.operations import operation_gate


class Command(BaseCommand):
    help='SQLite-Suchindex und Trigger aus dem festgelegten Schema neu aufbauen.'
    def handle(self,*args,**options):
        schema=import_module('akte.migrations.0010_local_search_index')
        with operation_gate(),connection.schema_editor() as editor:
            schema.uninstall(None,editor)
            schema.install(None,editor)
        self.stdout.write('Suchindex neu aufgebaut; bei SQLite ohne FTS5 bleibt die normale Suche verfügbar.')
