"""Audited, reversible, idempotent merging of duplicate entries and strong-reference linking."""
import re
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Max
from django.utils import timezone
from .files import link_document
from .models import (Allocation, Document, DocumentLink, Draft, Entry,
                     KnowledgeItem, KnowledgeVideo, MaintenanceCompletion,
                     PartEvent, PhotoLink, Step, Task)
from .operations import guarded
from .rules import Conflict, audit, check_owner

_REF_TOKEN = re.compile(r'(?<!\w)(?:[A-Z]{1,6}[-/]?\d{4,}|\d{5,})(?!\w)', re.IGNORECASE)


def _norm(value):
    return re.sub(r'[\s/-]+', '', str(value or '')).upper()


def extract_references(text):
    """Extract non-trivial order/invoice reference tokens (>=5 chars, contains digits)."""
    result = set()
    for match in _REF_TOKEN.findall(str(text or '')):
        token = _norm(match)
        if len(token) >= 5 and any(ch.isdigit() for ch in token):
            result.add(token)
    return result


def entry_references(entry):
    """Collect explicit invoice/order references already associated with an entry."""
    refs = set()
    for doc in entry.linked_documents:
        inv = (doc.edits or {}).get('invoice_number') or (doc.proposal or {}).get('invoice_number')
        if inv:
            token = _norm(inv)
            if len(token) >= 4:
                refs.add(token)
    for alloc in entry.allocations.select_related('source'):
        if alloc.source.invoice_number:
            token = _norm(alloc.source.invoice_number)
            if len(token) >= 4:
                refs.add(token)
    refs.update(extract_references(entry.title))
    refs.update(extract_references(entry.description))
    return refs


def strong_open_entry_match(doc, result):
    """Return an existing open entry ONLY on high-confidence + explicit reference overlap.

    Same vehicle, similar title, vendor or date alone never trigger automatic linking.
    """
    match = (result or {}).get('open_entry') or {}
    if not match.get('id') or match.get('confidence') != 'high':
        return None
    if not doc.project_id or doc.project.is_scan_inbox or not doc.entry_id:
        return None
    target = Entry.objects.filter(
        pk=match['id'], project=doc.project, project__owner=doc.owner,
        deleted_at__isnull=True, is_provisional=False,
    ).exclude(status='erledigt').exclude(pk=doc.entry_id).first()
    if target is None:
        return None
    proposal = (result or {}).get('proposal') or {}
    incoming = set()
    inv = proposal.get('invoice_number')
    if inv:
        token = _norm(inv)
        if len(token) >= 4:
            incoming.add(token)
    incoming.update(extract_references(doc.ocr))
    incoming.update(extract_references(doc.voice_data.get('text', '')))
    mail = doc.voice_data.get('mail') or {}
    incoming.update(extract_references(mail.get('subject', '')))
    incoming.update(extract_references(mail.get('full_text', '')))
    if not incoming:
        return None
    target_refs = entry_references(target)
    return target if (incoming & target_refs) else None


def merge_preview(owner, source, target):
    """Inventory every relation on source before merging into target."""
    check_owner(owner, source)
    check_owner(owner, target)
    if source.pk == target.pk:
        raise ValidationError('Quelle und Ziel sind derselbe Vorgang.')
    if source.project_id != target.project_id:
        raise ValidationError('Nur Vorgänge desselben Projekts können verbunden werden.')
    if source.deleted_at or target.deleted_at:
        raise ValidationError('Beide Vorgänge müssen aktiv (nicht im Papierkorb) sein.')
    target_costs = {
        (a.source.amount, a.source.currency, _norm(a.source.invoice_number))
        for a in target.allocations.select_related('source')
    }
    source_allocs = list(source.allocations.select_related('source'))
    duplicate_costs = [
        a for a in source_allocs
        if (a.source.amount, a.source.currency, _norm(a.source.invoice_number)) in target_costs
    ]
    return {
        'documents': Document.objects.filter(entry=source).count(),
        'document_links': DocumentLink.objects.filter(entry=source).count(),
        'allocations': len(source_allocs),
        'duplicate_costs': duplicate_costs,
        'steps': Step.objects.filter(entry=source).count(),
        'photos': PhotoLink.objects.filter(entry=source).count(),
        'part_events': PartEvent.objects.filter(entry=source).count(),
        'tasks': Task.objects.filter(entry=source).count(),
        'knowledge_items': KnowledgeItem.objects.filter(entry=source).count(),
        'knowledge_videos': KnowledgeVideo.objects.filter(entry=source).count(),
        'maintenance_completions': MaintenanceCompletion.objects.filter(entry=source).count(),
        'drafts': Draft.objects.filter(entry=source).count(),
    }


@guarded
@transaction.atomic
def merge_entries(owner, source, target, *, source_version=None, target_version=None, reason=''):
    """Move all documents, allocations, steps, media and references from source to target.

    Preserves all existing financial amounts, CostSources and Paperless originals.
    Retires source reversibly into the trash (deleted_at) with an audit trail.
    Repeated calls for an already-merged pair are idempotent.
    """
    source = Entry.objects.select_related('project').get(pk=source.pk)
    target = Entry.objects.select_related('project').get(pk=target.pk)
    check_owner(owner, source)
    check_owner(owner, target)
    if source.pk == target.pk:
        return target
    if source.deleted_at and source.snapshot.get('merged_into') == str(target.pk):
        return target
    if source.deleted_at or target.deleted_at:
        raise ValidationError('Beide Vorgänge müssen aktiv (nicht im Papierkorb) sein.')
    if source.project_id != target.project_id:
        raise ValidationError('Nur Vorgänge desselben Projekts können verbunden werden.')
    if source_version is not None and source.version != source_version:
        raise Conflict('Quellvorgang wurde zwischenzeitlich geändert. Bitte neu laden.')
    if target_version is not None and target.version != target_version:
        raise Conflict('Zielvorgang wurde zwischenzeitlich geändert. Bitte neu laden.')
    if MaintenanceCompletion.objects.filter(entry=source).exists():
        raise ValidationError('Der Quellvorgang besitzt bestätigte Wartungsabschlüsse. Bitte zuerst im Wartungsplan prüfen.')

    moved_docs = list(Document.objects.filter(entry=source).values_list('pk', flat=True))
    Document.objects.filter(entry=source).update(entry=target, project=target.project, version=F('version') + 1)

    for link in list(DocumentLink.objects.filter(entry=source).select_related('document')):
        if not link.document.deleted_at:
            link_document(link.document, target.project, target)
        link.delete()

    moved_allocs = Allocation.objects.filter(entry=source).update(entry=target)

    step_offset = (target.steps.aggregate(m=Max('position'))['m'] or 0) + 1
    Step.objects.filter(entry=source).update(entry=target, position=F('position') + step_offset)

    photo_offset = (target.photos.aggregate(m=Max('position'))['m'] or 0) + 1
    PhotoLink.objects.filter(entry=source).update(entry=target, position=F('position') + photo_offset)

    PartEvent.objects.filter(entry=source).update(entry=target)
    Task.objects.filter(entry=source).update(entry=target)
    KnowledgeItem.objects.filter(entry=source).update(entry=target)
    KnowledgeVideo.objects.filter(entry=source).update(entry=target)
    Draft.objects.filter(entry=source).update(entry=target, project=target.project)

    merged_list = list(target.snapshot.get('merged_entries', []))
    if str(source.pk) not in merged_list:
        merged_list.append(str(source.pk))
    target.snapshot = {**target.snapshot, 'merged_entries': merged_list}
    if target.allocations.exists() and target.cost_state != 'known':
        target.cost_state = 'known'
    target.version += 1
    target.save(update_fields=['snapshot', 'cost_state', 'version'])

    source_snapshot = {
        k: v for k, v in source.snapshot.items()
        if k not in ('_scan_document', '_scan_last_ai', 'capture_related_entry')
    }
    source_snapshot['merged_into'] = str(target.pk)
    source.snapshot = source_snapshot
    source.is_provisional = False
    source.deleted_at = timezone.now()
    source.version += 1
    source.save(update_fields=['snapshot', 'is_provisional', 'deleted_at', 'version'])

    from .template_maintenance import sync_entry
    sync_entry(target)
    sync_entry(source)

    details = {
        'source': str(source.pk), 'target': str(target.pk),
        'documents': [str(pk) for pk in moved_docs], 'allocations': moved_allocs,
        'reason': reason[:300],
    }
    audit(owner, target, 'Vorgang zusammengeführt', details)
    audit(owner, source, 'In Vorgang zusammengeführt', details)
    return target
