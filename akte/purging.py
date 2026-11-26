"""Explicit permanent deletion; shared files and the AI spending ledger survive."""
import hashlib
import json
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models import F, Q
from .models import (Allocation, ArchiveReference, Asset, CostSource, Document, Entry,
                     Job, MaintenanceCompletion, Part, PhotoLink, Revision)
from .operations import guarded
from .rules import Conflict, check_owner


def item_owner(item):
    return item.entry.project.owner_id if isinstance(item, PhotoLink) else (item.project.owner_id if isinstance(item, Entry) else item.owner_id)


def source_fingerprint(source):
    values={'id':str(source.pk),'version':source.version,'amount':str(source.amount),'currency':source.currency,
        'allocations':list(source.allocations.order_by('id').values('id','project_id','entry_id','step_id','amount','position_ref'))}
    return hashlib.sha256(json.dumps(values,sort_keys=True,default=str).encode()).hexdigest()


def purge_context(item):
    source = CostSource.objects.filter(document=item).first() if isinstance(item, Document) else None
    return {'item': item, 'source': source,'cost_fingerprint':source_fingerprint(source) if source else '',
            'allocations': source.allocations.select_related('project','entry') if source else [],
            'is_document': isinstance(item, Document), 'is_entry': isinstance(item, Entry),
            'photos_count': item.photos.count() if isinstance(item, Entry) else 0,
            'steps_count': item.steps.count() if isinstance(item, Entry) else 0}


def _remove_unused_files(owner, asset_ids, warnings):
    candidates = Asset.objects.filter(owner=owner,pk__in=asset_ids,links__isnull=True,
        document__isnull=True,document_pages__isnull=True,importbatch__isnull=True).distinct()
    files = []
    for asset in candidates:
        if Job.objects.filter(asset=asset,status='running').exists():
            raise ValidationError('Eine Bildvorschau wird gerade erstellt. Bitte kurz warten und erneut löschen.')
        files.extend(field.name for field in (asset.file,asset.thumbnail) if field)
        asset.delete()
    def remove():
        from .operations import operation_gate
        with operation_gate():
            for name in files:
                try:
                    referenced=Asset.objects.filter(Q(file=name)|Q(thumbnail=name)).exists()
                except DatabaseError:
                    warnings.append('Dateirest wegen Datenbankfehler aufbewahrt; bei der Wartung erneut prüfen.')
                    continue
                if referenced:
                    continue
                target=(settings.MEDIA_ROOT/name).resolve()
                if not target.is_relative_to(settings.MEDIA_ROOT.resolve()):
                    warnings.append('Unsicherer Dateipfad wurde nicht entfernt.')
                    continue
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    warnings.append('Ein Dateirest ist noch belegt und muss bei der Wartung bereinigt werden.')
    transaction.on_commit(remove)


@guarded
@transaction.atomic
def purge_item(owner, item, version, *, remove_costs=False, cost_version=None, cost_fingerprint=None):
    item=type(item).objects.get(pk=item.pk)
    if item_owner(item)!=owner.pk:
        raise ValidationError('Dieser Papierkorbeintrag gehört einem anderen Konto.')
    if not item.deleted_at:
        raise ValidationError('Zuerst in den Papierkorb verschieben.')
    if item.version!=version:
        raise Conflict('Zwischenzeitlich geändert oder wiederhergestellt. Papierkorb neu öffnen.')
    identity=item.pk
    assets=[]
    warnings=[]
    if isinstance(item, Document):
        source=CostSource.objects.filter(document=item).first()
        if source:
            if not remove_costs:
                raise ValidationError('Die zugehörige Kostenbuchung samt Zuordnungen ausdrücklich zum Löschen bestätigen.')
            if source.version!=cost_version:
                raise Conflict('Kostenbuchung inzwischen geändert. Löschansicht neu öffnen.')
            check_owner(owner,source)
            if cost_fingerprint!=source_fingerprint(source):
                raise Conflict('Kostenzuordnung inzwischen geändert. Löschansicht neu öffnen.')
            if source.allocations.exclude(project__owner=owner).exists():
                raise ValidationError('Fremde Kostenzuordnung: zuerst Datenbestand prüfen.')
            affected=list(source.allocations.exclude(entry=None).values_list('entry_id',flat=True))
            Revision.objects.filter(owner=owner,entity=source.pk).delete()
            source.delete()
            for entry in Entry.objects.filter(pk__in=affected,cost_state='known'):
                if not entry.allocations.exists():
                    entry.cost_state='unknown';entry.save(update_fields=['cost_state'])
        if item.asset_id:
            assets.append(item.asset_id)
            if item.paperless_id or item.task_id or item.submitted_at:
                ArchiveReference.objects.update_or_create(owner=owner,instance=item.instance,sha256=item.asset.sha256,
                    defaults={'paperless_id':item.paperless_id,'submission_key':item.submission_key,
                              'task_id':item.task_id,'submitted_at':item.submitted_at})
        assets.extend(item.pages.values_list('asset_id',flat=True))
        Part.objects.filter(source_document=item).update(source_document=None,source_position=None)
        if item.entry_id and item.entry.snapshot.get('_scan_document')==str(identity):
            entry=item.entry
            snapshot={key:value for key,value in entry.snapshot.items() if key not in ('_scan_document','_scan_last_ai')}
            snapshot['scan_document_deleted']=True
            Entry.objects.filter(pk=entry.pk).update(snapshot=snapshot,is_provisional=False,version=F('version')+1)
    elif isinstance(item, Entry):
        from .knowledge_video import path_for
        videos=list(item.knowledge_videos.all())
        if any(v.state=='processing' for v in videos):
            raise ValidationError('Video wird gerade verarbeitet. Bitte vor endgültigem Löschen abwarten.')
        video_paths=[path_for(v,suffix) for v in videos for suffix in ('.source','.mp4','.mp4.tmp')]
        item.knowledge_videos.all().delete()
        def remove_videos():
            from .operations import operation_gate
            with operation_gate():
                for path in video_paths:
                    try:path.unlink(missing_ok=True)
                    except OSError:warnings.append('Video-Dateirest konnte nicht entfernt werden.')
        transaction.on_commit(remove_videos)
        if item.part_events.exists() or MaintenanceCompletion.objects.filter(entry=item).exists():
            raise ValidationError('Eintrag ist mit Einbauhistorie oder Wartungsplan verknüpft. Diese Verbindung zuerst klären.')
        allocations=Allocation.objects.filter(Q(entry=item)|Q(step__entry=item))
        if allocations.exclude(project__owner=owner).exists() or allocations.exclude(source__owner=owner).exists():
            raise ValidationError('Fremde Kostenzuordnung: zuerst Datenbestand prüfen.')
        # Keep amounts and source records; only remove the deleted work target.
        sources=list(allocations.values_list('source_id',flat=True))
        allocations.update(entry=None,step=None)
        CostSource.objects.filter(pk__in=sources).update(version=F('version')+1)
        assets.extend(item.photos.values_list('asset_id',flat=True))
    elif isinstance(item, PhotoLink):
        assets.append(item.asset_id)
    else:
        raise ValidationError('Dieser Datentyp kann hier nicht gelöscht werden.')
    Revision.objects.filter(owner=owner,entity=identity).delete()
    item.delete()
    _remove_unused_files(owner,assets,warnings)
    return warnings
