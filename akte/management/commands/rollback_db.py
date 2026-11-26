"""List or restore automatic pre-migration SQLite snapshots."""
from datetime import datetime, timezone
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from akte.updates import StartupError, list_snapshots, restore_snapshot


class Command(BaseCommand):
    help = 'List or atomically restore automatic pre-migration SQLite snapshots.'

    def add_arguments(self, parser):
        parser.add_argument('--list', action='store_true', dest='list_only', help='List available pre-migration snapshots.')
        parser.add_argument('--restore', metavar='SNAPSHOT', help='Snapshot filename (or "latest") to restore atomically.')

    def handle(self, *args, **options):
        if options['list_only'] or not options['restore']:
            snaps = list_snapshots(settings.DATA_DIR)
            if not snaps:
                self.stdout.write('No pre-migration SQLite snapshots found.')
                return
            for snap in snaps:
                st = snap.stat()
                ts = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')
                self.stdout.write(f'{snap.name}\t{st.st_size} bytes\t{ts}')
            return

        try:
            source, safety = restore_snapshot(settings.DATA_DIR, options['restore'])
        except (StartupError, OSError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f'Restored SQLite snapshot: {source.name}')
        if safety:
            self.stdout.write(f'Previous database saved to: {safety.name}')
