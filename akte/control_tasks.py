"""Serial settings actions in the existing worker; no subprocesses or new services."""
import hashlib
import json
import os
import time
import uuid
import zipfile
from datetime import timedelta, datetime, timezone as utc
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .adapters import paperless, IntegrationError
from .models import ControlTask, Document, Job, ManagedBackup, Preferences
from .operations import guarded, operation_gate
from .preferences import preferences, require_system_access
from .storage import durable_replace

KINDS={'paperless_test','paperless_sync','mail','backup','backup_discover'}
MEDIA_USAGE_FILE='media-usage.json'


def read_media_usage():
    path=settings.DATA_DIR/MEDIA_USAGE_FILE
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size>4096:
            return None,None
        data=json.loads(path.read_text(encoding='utf8'))
        if not isinstance(data,dict):
            return None,None
        total=data.get('bytes')
        updated=parse_datetime(data.get('updated','')) if isinstance(data.get('updated'),str) else None
        if type(total) is not int or total<0:
            return None,None
        if updated is not None and timezone.is_naive(updated):
            updated=timezone.make_aware(updated,utc.utc)
        return total,updated
    except (OSError,ValueError,TypeError):
        return None,None


def refresh_media_usage():
    """Walk MEDIA_ROOT in the worker without holding the global write gate."""
    root=settings.MEDIA_ROOT
    total=0
    seen=0
    heartbeat=settings.DATA_DIR/'worker.heartbeat'
    stack=[root]
    while stack:
        current=stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if current==root and entry.name=='incoming':
                                continue
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total+=entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
                    seen+=1
                    if seen%5000==0:
                        try:heartbeat.touch()
                        except OSError:pass
        except OSError:
            continue
    target=settings.DATA_DIR/MEDIA_USAGE_FILE
    temporary=settings.DATA_DIR/(MEDIA_USAGE_FILE+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf8') as stream:
            json.dump({'bytes':total,'updated':timezone.now().isoformat()},stream)
            stream.flush()
            os.fsync(stream.fileno())
        durable_replace(temporary,target)
    finally:
        temporary.unlink(missing_ok=True)
    return total


@guarded
@transaction.atomic
def schedule_control(owner,kind,key):
    require_system_access(owner)
    if kind not in KINDS:raise ValidationError('Unbekannte Systemaktion.')
    identity=uuid.UUID(str(key))
    prior=ControlTask.objects.filter(request_key=identity).first()
    if prior:
        if prior.owner_id!=owner.pk or prior.kind!=kind:raise ValidationError('Ungültige Auftragskennung.')
        return prior
    current=ControlTask.objects.filter(owner=owner,kind=kind,job__status__in=['pending','running']).first()
    if current:return current
    task=ControlTask.objects.create(owner=owner,kind=kind,request_key=identity)
    Job.objects.create(kind='control',stage=kind,control=task)
    return task


def backup_path(relative):
    root=settings.DATA_DIR.resolve();path=root/relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts or not Path(relative).parts or Path(relative).parts[0] not in ('backups','safety'):
        raise ValidationError('Ungültiger Sicherungspfad.')
    if path.is_symlink() or any(p.is_symlink() for p in path.parents if p.is_relative_to(root) and p!=root):raise ValidationError('Verknüpfter Sicherungspfad ausgeschlossen.')
    if not path.resolve().is_relative_to((root/Path(relative).parts[0]).resolve()):raise ValidationError('Sicherung liegt außerhalb des Sicherungsordners.')
    return path


@guarded
def discover_backups(owner):
    require_system_access(owner)
    added=0
    for folder in (settings.DATA_DIR/'backups',settings.DATA_DIR/'safety'):
        if not folder.is_dir():continue
        for file in folder.rglob('*.zip'):
            relative=file.relative_to(settings.DATA_DIR).as_posix()
            try:
                file=backup_path(relative)
                if ManagedBackup.objects.filter(path=relative).exists():continue
                with zipfile.ZipFile(file) as archive:
                    info=archive.getinfo('backup.json')
                    if info.file_size>16000:continue
                    metadata=json.loads(archive.read(info))
                    if metadata.get('format')!='projektakte-backup':continue
                created=parse_datetime(metadata.get('created','')) or datetime.fromtimestamp(file.stat().st_mtime,utc.utc)
                ManagedBackup.objects.create(owner=owner,path=relative,size=file.stat().st_size,managed=False,created=created)
                added+=1
            except (OSError,ValidationError,ValueError,KeyError,zipfile.BadZipFile):continue
    return added


def create_backup(owner,identity):
    from .backup import backup
    from .documents import remote_signature
    require_system_access(owner)
    relative='backups/'+str(identity)+'/projektakte.zip';destination=backup_path(relative)
    folder=destination.parent;folder.mkdir(parents=True,mode=0o700,exist_ok=True)
    with operation_gate():
        if not destination.exists():
            bundle=folder/'paperless.zip'
            try:
                signatures={}
                with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED) as archive:
                    for doc in Document.objects.filter(paperless_id__isnull=False).select_related('owner'):
                        before=paperless(doc).read(doc.paperless_id,doc)
                        raw=paperless(doc).original(doc.paperless_id)
                        after=paperless(doc).read(doc.paperless_id,doc)
                        if remote_signature(before)!=remote_signature(after):raise ValidationError('Paperless-Beleg während der Sicherung geändert. Erneut versuchen.')
                        prefix=str(doc.pk)+'/'
                        archive.writestr(prefix+'original',raw)
                        archive.writestr(prefix+'metadata.json',json.dumps(after,ensure_ascii=False))
                        archive.writestr(prefix+'sha256',hashlib.sha256(raw).hexdigest())
                        signatures[str(doc.pk)]=remote_signature(after)
                    archive.writestr('signatures.json',json.dumps(signatures))
                bundle.chmod(0o600)
                backup(destination,bundle,complete_control=identity)
            finally:bundle.unlink(missing_ok=True)
        with zipfile.ZipFile(destination) as archive:
            if archive.testzip():raise ValidationError('Sicherungsarchiv beschädigt.')
        record,_=ManagedBackup.objects.get_or_create(path=relative,defaults={'owner':owner,'size':destination.stat().st_size,'managed':True})
        if record.owner_id!=owner.pk:raise ValidationError('Fremde Sicherung.')
        prune_backups(owner)
    return 'Sicherung erstellt und geprüft. Sie steht im Backup-Manager zum Download bereit.'


def paperless_sync(owner,task):
    require_system_access(owner)
    pref=preferences(owner);page=int(task.payload.get('page',1))
    params={'page':page,'page_size':30,'ordering':'id'}
    if pref.paperless_tags:params['tags__id__in']=','.join(map(str,pref.paperless_tags))
    adapter=paperless()
    if settings.DEMO_MODE:data={'results':[],'next':None}
    else:data=adapter.request('GET','api/documents/',params=params).json()
    from .documents import remote_signature
    from .scan_entries import ensure_scan_entry
    imported=int(task.payload.get('imported',0));checked=int(task.payload.get('checked',0))
    for remote in data.get('results',[]):
        if not isinstance(remote.get('id'),int) or remote.get('deleted_at'):continue
        with operation_gate(),transaction.atomic():
            doc,new=Document.objects.get_or_create(owner=owner,instance=settings.PAPERLESS_URL,paperless_id=remote['id'],
                defaults={'status':'review','ai_enabled':False,'auto_route':True,'ocr':remote.get('content') or '',
                          'verified_at':timezone.now(),'remote_signature':remote_signature(remote),'remote_state':'current'})
            if new:
                ensure_scan_entry(doc);imported+=1
            elif not doc.deleted_at:
                signature=remote_signature(remote)
                doc.remote_state='current' if doc.remote_signature==signature else 'changed'
                doc.remote_checked_at=timezone.now()
                if doc.remote_state=='changed':doc.error='Paperless-Beleg verändert. Prüfen; bestätigte Kosten bleiben erhalten.'
                doc.save(update_fields=['remote_state','remote_checked_at','error']);checked+=1
    more=bool(data.get('next'))
    if page>=1000 and more:raise ValidationError('Abgleichgrenze erreicht. Import-Tags einschränken.')
    task.payload={**task.payload,'page':page+1,'imported':imported,'checked':checked}
    return f'{imported} Belege neu im Eingang, {checked} abgeglichen. Keine KI-Kosten oder Kostenbuchungen.',more


def process_control(job):
    from .worker import commit_job
    task=job.control;require_system_access(task.owner)
    more=False
    if task.kind=='backup':result=create_backup(task.owner,task.pk)
    elif task.kind=='backup_discover':
        added=discover_backups(task.owner)
        result=f'{added} bisher nicht registrierte Sicherung(en) eingelesen.' if added else 'Keine neuen unregistrierten Sicherungen gefunden.'
    elif task.kind=='mail':
        from .mail_ingest import MailPoller
        from .mail_config import mail_config
        config=mail_config()
        if config is None:raise ValidationError('IMAP-Zugang ist auf dem Server noch nicht eingerichtet.')
        count=MailPoller().collect(config)
        result=f'{count} neue E-Mail(s) sicher übernommen. Verarbeitete Nachrichten sind gelesen.'
    elif task.kind=='paperless_test':
        if not settings.DEMO_MODE:paperless().request('GET','api/documents/',params={'page_size':1})
        result='Paperless-Verbindung und Lesezugriff erfolgreich.'
    else:result,more=paperless_sync(task.owner,task)
    with operation_gate(),transaction.atomic():
        if not Job.objects.filter(pk=job.pk,status='running',lease_token=job.lease_token,leased_until__gt=timezone.now()).exists():return
        task.result=result;task.save(update_fields=['payload','result'])
        commit_job(job,status='pending' if more else 'done',available=timezone.now(),leased_until=None,error='')


def periodic_housekeeping():
    from .media_controls import empty_trash
    from .account_events import error_event
    for identity in Preferences.objects.filter(trash_days__isnull=False,owner__is_active=True).values_list('pk',flat=True):
        with operation_gate():
            pref=Preferences.objects.filter(pk=identity,trash_days__isnull=False,owner__is_active=True).select_related('owner').first()
            if pref is None:continue
            removed,blocked=empty_trash(pref.owner,before=timezone.now()-timedelta(days=pref.trash_days),remove_costs=pref.trash_costs)
            if blocked:error_event('Papierkorb',f'{blocked} abgelaufene Einträge wegen Kosten oder Historien-Verknüpfungen aufbewahrt.',pref.owner)


@guarded
@transaction.atomic
def restart_stale(owner):
    now=timezone.now();count=0
    owned=Q(document__owner=owner)|Q(asset__owner=owner)|Q(export__owner=owner)|Q(control__owner=owner)
    for job in Job.objects.filter(owned,status='running').filter(Q(leased_until__lte=now)|Q(leased_until__isnull=True)):
        terminal=job.document_id and (job.document.status=='accepted' or job.document.deleted_at)
        job.status='done' if terminal else 'pending';job.lease_token=uuid.uuid4();job.leased_until=None;job.available=now;job.error='';job.save();count+=1
    return count


@guarded
def prune_backups(owner):
    require_system_access(owner)
    discover_backups(owner)
    keep=preferences(owner).backup_keep
    rows=list(ManagedBackup.objects.filter(owner=owner,managed=True).order_by('-created','-id'))
    # Retain only intact, successful archives in the count. Never prune before
    # at least 'keep' verified newer restore points exist.
    valid=[]
    for record in rows:
        path=backup_path(record.path)
        if not path.is_file():
            record.delete()
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                info=archive.getinfo('backup.json')
                if info.file_size>16000:continue
                meta=json.loads(archive.read(info))
                if meta.get('format')!='projektakte-backup':continue
                archive.getinfo('manifest.json')
        except (OSError,KeyError,ValueError,zipfile.BadZipFile):continue
        valid.append(record)
    for older in valid[keep:]:
        path=backup_path(older.path)
        path.unlink()
        older.delete()
        # Companion Paperless archive is already embedded in projektakte.zip.
        companion=path.parent/'paperless.zip'
        if path.name=='projektakte.zip' and path.parent!=(settings.DATA_DIR/'backups') and path.parent!=(settings.DATA_DIR/'safety') and not companion.is_symlink():
            companion.unlink(missing_ok=True)
        try:path.parent.rmdir()
        except OSError:pass
