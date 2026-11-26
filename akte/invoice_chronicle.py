"""Create/link chronology and attach the existing invoice source in one transaction."""
import uuid
from .categories import default_category
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .files import link_document
from .models import Document, Entry
from .operations import guarded
from .proposal_schema import ENTRY_FIELDS, validate_metadata
from .rules import audit, check_owner, parse_date


def entry_initial(proposal):
    values = proposal.get('entry') or {}
    return {'entry_' + key: (str(values.get(key) or '').replace('.', ',')
                             if key in ('meter', 'quantity_l') else values.get(key) or '')
            for key in ENTRY_FIELDS}


def attach_chronicle(owner, doc, proposal, choice, source=None):
    """Caller holds the write gate and transaction. Repeated attachment is terminal."""
    if doc.entry_id:
        from .scan_entries import own_scan_entry, confirm_scan_entry
        if own_scan_entry(doc):
            return confirm_scan_entry(owner,doc,proposal,choice,source)
        return doc.entry
    mode = choice.get('mode')
    if mode == 'document':
        return None
    project = choice.get('project')
    if mode == 'existing':
        entry = choice.get('entry')
        if not entry:
            raise ValidationError('Vorhandenen Chronikeintrag auswählen.')
        entry = Entry.objects.select_related('project').get(pk=entry.pk)
        check_owner(owner, entry)
        if entry.deleted_at:
            raise ValidationError('Der Chronikeintrag liegt im Papierkorb.')
        if project and entry.project_id != project.pk:
            raise ValidationError('Chronikeintrag gehört nicht zum gewählten Projekt.')
        project = entry.project
    elif mode == 'create':
        if not project:
            raise ValidationError('Projekt für den Chronikeintrag auswählen.')
        check_owner(owner, project)
        validate_metadata(proposal)
        values = proposal.get('entry') or {}
        start, precision = parse_date(values.get('date'))
        end, _ = parse_date(values.get('end_date'))
        snapshot = {key: values.get(key) or '' for key in ('oil', 'quantity_l', 'filter')}
        if start is None:
            start, precision = timezone.localdate(), 'day'
            snapshot['_date_source'] = 'capture'
            snapshot['_capture_date'] = start.isoformat()
        snapshot.update(
            project_fields=project.fields,
            _ai_source_document=str(doc.pk),
            _inferred_fields=[field[6:] for field in proposal.get('inferred_fields', [])
                              if field.startswith('entry.')],
        )
        # One deterministic nonce for each invoice, even after retries or API errors.
        entry, created = Entry.objects.get_or_create(
            request_key=uuid.uuid5(doc.pk, 'invoice-chronicle'),
            defaults={
                'category': default_category(project, values.get('kind')),
                'project': project, 'title': values.get('title') or '',
                'kind': values.get('kind') or '', 'status': values.get('status') or '',
                'date': start, 'precision': precision, 'end_date': end,
                'description': values.get('description') or '',
                'meter': Decimal(values['meter']) if values.get('meter') is not None else None,
                'meter_unit': values.get('meter_unit') or '',
                'meter_name': values.get('meter_name') or '',
                'tags': values.get('tags') or '', 'snapshot': snapshot,
            })
        if created and proposal.get('definition'):
            from .definitions import apply_definition
            apply_definition(entry,proposal['definition'],inferred_paths=proposal.get('inferred_fields',[]));entry.save()
        check_owner(owner, entry)
        if entry.project_id != project.pk or entry.deleted_at:
            raise ValidationError('Vorhandener Rechnungs-Chronikeintrag muss zuerst geprüft werden.')
        if created:
            audit(owner, entry, 'Aus Rechnung übernommen', {'document': str(doc.pk), **values,
                  'inferred_fields': snapshot['_inferred_fields']})
    else:
        raise ValidationError('Chronikziel auswählen.')
    doc.project = project
    from .template_maintenance import sync_entry
    sync_entry(entry)
    doc.entry = entry
    link_document(doc, project, entry)
    if source:
        # Only enrich project-only allocations. Never move an explicit entry/step.
        source.allocations.filter(project=project, entry__isnull=True, step__isnull=True).update(entry=entry)
        if source.allocations.filter(entry=entry).exists():
            entry.cost_state = 'known'
            entry.save(update_fields=['cost_state'])
    return entry


@guarded
@transaction.atomic
def attach_accepted(owner, document, proposal, choice, expected_version):
    doc = Document.objects.select_related('entry').get(pk=document.pk)
    check_owner(owner, doc)
    if doc.deleted_at: raise ValidationError('Beleg liegt im Papierkorb. Zuerst wiederherstellen.')
    if doc.entry_id:
        return doc.entry
    if doc.status != 'accepted' or doc.version != expected_version:
        raise ValidationError('Beleg inzwischen geändert. Bitte neu laden.')
    entry = attach_chronicle(owner, doc, proposal, choice, doc.source)
    if entry:
        doc.version += 1
        doc.save(update_fields=['project', 'entry', 'version'])
        audit(owner, doc, 'Chronik ergänzt', {'entry': str(entry.pk)})
    return entry


def pending_chronicles(owner, project):
    from django.db.models import Q
    docs = Document.objects.filter(owner=owner, entry__isnull=True,deleted_at__isnull=True).filter(
        Q(project=project) | Q(links__project=project)).distinct().order_by('-created')[:100]
    result = []
    for doc in docs:
        values = {**doc.proposal, **doc.edits}
        title = (values.get('entry') or {}).get('title')
        result.append({'doc': doc, 'title': title or 'Rechnung · Titel noch offen',
                       'inferred': 'entry.title' in values.get('inferred_fields', [])})
    return result


def retain_entry_inferences(previous, cleaned):
    fields = previous.snapshot.get('_inferred_fields', [])
    if not fields:
        return
    snapshot = cleaned.get('snapshot', {})
    retained = []
    for key in fields:
        if key in ('oil', 'quantity_l', 'filter'):
            before, after = previous.snapshot.get(key), snapshot.get(key)
        else:
            before, after = getattr(previous, key, None), cleaned.get(key, getattr(previous, key, None))
        if before == after:
            retained.append(key)
    snapshot['_inferred_fields'] = retained


def marked(entry, field, value):
    return str(value if value is not None else '') + ('*' if value not in (None, '') and
                              field in entry.snapshot.get('_inferred_fields', []) else '')
