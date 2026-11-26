from pathlib import Path
from django.conf import settings
from akte.operations import guarded
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from akte.models import Asset,PhotoLink,Entry
class Command(BaseCommand):
    help='Nach 30 Tagen referenzgeprüft bereinigen. Ohne --apply nur Vorschau.'
    def add_arguments(self,p): p.add_argument('--apply',action='store_true')
    @guarded
    def handle(self,*args,**o):
        cutoff=timezone.now()-timedelta(days=30)
        with transaction.atomic():
            links=PhotoLink.objects.filter(deleted_at__lt=cutoff)
            self.stdout.write(f'Abgelaufene Bildzuordnungen: {links.count()}')
            if o['apply']: links.delete()
            # Entries with costs, documents, parts or tasks are deliberately retained.
            entries=Entry.objects.filter(deleted_at__lt=cutoff,allocations__isnull=True,documents__isnull=True,part_events__isnull=True,task__isnull=True,documentlink__isnull=True,maintenancecompletion__isnull=True)
            self.stdout.write(f'Referenzfreie Vorgänge: {entries.count()}')
            if o['apply']: entries.delete()
            assets=Asset.objects.filter(created__lt=cutoff,links__isnull=True,document__isnull=True,document_pages__isnull=True,importbatch__isnull=True)
            self.stdout.write(f'Referenzfreie Dateien: {assets.count()}')
            if o['apply']:
                files=[(a.file,a.thumbnail) for a in assets]
                assets.delete()
                def remove():
                    for original,thumb in files:
                        original.delete(save=False)
                        if thumb: thumb.delete(save=False)
                transaction.on_commit(remove)

        # Recover unacknowledged remnants after a hard process interruption too.
        # Only known media subdirectories, age >30 days, no symbolic links and
        # no current DB reference. Exports/backups are never pruned here.
        referenced=set(Asset.objects.values_list('file',flat=True))|set(Asset.objects.exclude(thumbnail='').values_list('thumbnail',flat=True))
        candidates=[]
        media=settings.MEDIA_ROOT.resolve()
        for directory in ('incoming','originals','thumbs'):
            folder=media/directory
            if not folder.exists(): continue
            for file in folder.iterdir():
                if file.is_symlink() or not file.is_file(): continue
                if not file.resolve().is_relative_to(media): continue
                if file.relative_to(media).as_posix() in referenced: continue
                if file.stat().st_mtime<cutoff.timestamp(): candidates.append(file)
        self.stdout.write(f'Alte unreferenzierte Dateireste: {len(candidates)}')
        if o['apply']:
            for file in candidates:
                try: file.unlink()
                except OSError: self.stderr.write('Dateirest derzeit belegt; bei nächster Wartung erneut prüfen.')
