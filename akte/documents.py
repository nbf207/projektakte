"""Paperless reconciliation never changes confirmed accounting or saved corrections."""
import hashlib
import json
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .adapters import DocumentMissing, IntegrationError, paperless
from .models import Document, Job
from .operations import operation_gate


def remote_signature(data):
    # Modified catches metadata/version changes; content catches changed OCR even
    # when a third-party installation does not expose a modification timestamp.
    fields = {key: data.get(key) for key in ('id', 'modified', 'content', 'original_file_name', 'checksum', 'version', 'versions', 'root_document', 'deleted_at', 'mime_type', 'page_count')}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def sync_document(doc):
    if doc.deleted_at: raise ValidationError('Beleg liegt im Papierkorb. Zuerst wiederherstellen.')
    identity = (doc.instance, doc.paperless_id)
    if not doc.paperless_id:
        raise ValidationError('Paperless-ID fehlt; zuerst Upload abgleichen.')
    try:
        data = paperless(doc).read(doc.paperless_id, doc)
        if int(data.get('id', 0)) != doc.paperless_id:
            raise IntegrationError('Paperless-Dokumentidentität stimmt nicht überein.')
        signature = remote_signature(data)
        missing = bool(data.get('deleted_at'))
    except DocumentMissing:
        data, signature, missing = None, '', True
    with operation_gate(), transaction.atomic():
        current = Document.objects.get(pk=doc.pk)
        if current.deleted_at: raise ValidationError('Beleg liegt inzwischen im Papierkorb.')
        if (current.instance, current.paperless_id) != identity:
            raise ValidationError('Dokumentzuordnung wurde inzwischen verändert. Erneut abgleichen.')
        if missing:
            current.remote_state = 'missing'
            current.error = 'Dokument in Paperless gelöscht oder nicht mehr zugänglich. Lokale Historie und Kosten bleiben erhalten.'
        elif not current.remote_signature:
            # Legacy rows have no trustworthy fingerprint. Do not silently claim
            # that today's content is the content formerly checked by the user.
            if current.ocr != (data.get('content') or ''):
                current.remote_state = 'changed'
                current.error = 'Paperless-Inhalt weicht vom gespeicherten OCR ab. Prüfen; bestätigte Kosten bleiben unverändert.'
            else:
                current.remote_signature = signature
                current.remote_state = 'current'
                current.error = ''
        elif signature != current.remote_signature:
            current.remote_state = 'changed'
            current.error = 'Paperless-Dokument wurde verändert. Original und Prüfstand vergleichen; keine automatische Neubuchung.'
        else:
            current.remote_state = 'current'
            current.error = ''
        current.remote_checked_at = timezone.now()
        current.save(update_fields=['remote_state', 'remote_signature', 'remote_checked_at', 'error'])
        return current


def schedule_document(doc, action, *, with_original=False, existing_id=None, identity_checked=False):
    """Manual retries invalidate old leases and re-read current terminal state."""
    if doc.deleted_at: raise ValidationError('Beleg liegt im Papierkorb. Zuerst wiederherstellen.')
    if action == 'sync':
        return sync_document(doc)
    if action == 'ack_remote':
        return acknowledge_remote(doc,identity_checked)
    # A reconciliation request is read-only externally. Never hold a DB
    # transaction while waiting for Paperless.
    result = paperless(doc).reconcile(doc) if action == 'reconcile' else None
    if action == 'link':
        if not identity_checked: raise ValidationError('Originalidentität zuerst ausdrücklich bestätigen.')
        try: result=int(existing_id)
        except (ValueError,TypeError): raise ValidationError('Gültige vorhandene Paperless-ID eingeben.')
        data=paperless(doc).read(result)
        if data.get('deleted_at'): raise DocumentMissing('Dokument liegt im Paperless-Papierkorb.')
        if result<1 or int(data.get('id',0))!=result: raise IntegrationError('Dokumentidentität stimmt nicht überein.')
    with operation_gate(), transaction.atomic():
        current = Document.objects.get(pk=doc.pk)
        if current.deleted_at: raise ValidationError('Beleg liegt inzwischen im Papierkorb.')
        if current.status == 'accepted':
            return current
        job, _ = Job.objects.get_or_create(document=current)
        if job.status == 'running' and job.leased_until and job.leased_until > timezone.now():
            raise ValidationError('Auftrag läuft bereits. Bitte Verarbeitung abwarten.')
        if action in ('reconcile','link'):
            if Document.objects.filter(owner=current.owner,instance=current.instance,paperless_id=result).exclude(pk=current.pk).exists():
                raise ValidationError('Diese Paperless-ID ist bereits einem Beleg im Eingang zugeordnet. Vorhandenen Beleg verwenden; keine zweite Übernahme.')
            current.paperless_id = result
            current.status = 'ocr'
            current.error = ''
            job.stage = 'ocr'
        elif action == 'manual':
            if not current.verified_at:
                raise ValidationError('Zuerst Paperless-Übergabe bestätigen. Das lokale Original bleibt erhalten.')
            current.ai_enabled = False
            current.status = 'review'
            current.error = ''
        elif action == 'ai':
            if not current.verified_at or current.remote_state in ('changed', 'missing'):
                raise ValidationError('OCR/Original zuerst mit Paperless abgleichen.')
            current.ai_enabled = True
            current.ai_with_original = with_original
            current.status = 'ai'
            job.stage = 'ai'
        elif action == 'refresh':
            if not current.paperless_id:
                raise ValidationError('Paperless-ID fehlt.')
            job.stage = 'ocr'
        elif action != 'retry':
            raise ValidationError('Unbekannte Auftragsaktion.')
        elif current.status == 'uncertain':
            raise ValidationError('Upload unklar: zuerst abgleichen. Kein erneuter Upload.')
        job.status = 'done' if action == 'manual' else 'pending'
        job.attempts = 0
        job.available = timezone.now()
        job.leased_until = None
        job.lease_token = uuid.uuid4()
        job.error = ''
        current.version += 1
        current.save()
        job.save()
        return current


def create_parts(doc, project, selected):
    from .models import Part
    from .rules import check_owner
    check_owner(doc.owner, project)
    with operation_gate(), transaction.atomic():
        current=Document.objects.get(pk=doc.pk)
        if current.deleted_at or current.status!='accepted':
            raise ValidationError('Rechnung zuerst geprüft übernehmen.')
        positions=current.source.positions
        for value in selected:
            try: index=int(value)
            except (ValueError,TypeError): raise ValidationError('Ungültige Teileposition.')
            if not 0<=index<len(positions): raise ValidationError('Rechnungsposition fehlt.')
            position=positions[index]
            Part.objects.get_or_create(project=project,source_document=current,source_position=index,
                defaults={'name':position['description'][:150], 'number':position.get('part_number','')[:100],
                          'state':'vorhanden'})[0].documents.add(current)


def acknowledge_remote(doc, identity_checked):
    from .rules import audit
    if doc.deleted_at: raise ValidationError('Beleg liegt im Papierkorb. Zuerst wiederherstellen.')
    if not identity_checked:
        raise ValidationError('Aktuelles Paperless-Original mit bestätigtem Beleg ausdrücklich vergleichen.')
    data=paperless(doc).read(doc.paperless_id)
    if int(data.get('id',0))!=doc.paperless_id:
        raise IntegrationError('Dokumentidentität stimmt nicht überein.')
    if data.get('deleted_at'): raise DocumentMissing('Dokument liegt im Paperless-Papierkorb. Ursprüngliche Kosten bleiben erhalten.')
    signature=remote_signature(data)
    with operation_gate(), transaction.atomic():
        current=Document.objects.get(pk=doc.pk)
        if current.deleted_at: raise ValidationError('Beleg liegt inzwischen im Papierkorb.')
        if current.version!=doc.version or current.paperless_id!=doc.paperless_id:
            raise ValidationError('Prüfstand inzwischen geändert. Neu laden.')
        old=current.remote_signature
        current.remote_signature=signature;current.remote_state='current'
        current.remote_checked_at=timezone.now();current.error='';current.version+=1
        current.save(update_fields=['remote_signature','remote_state','remote_checked_at','error','version'])
        audit(current.owner,current,'Paperless-Stand verglichen',{'previous_signature':old,'current_signature':signature,
              'note':'Identität manuell bestätigt; ursprüngliche Prüfwerte und Kosten unverändert.'})
        return current
