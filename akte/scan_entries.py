"""A stable work entry exists before OCR, AI, project choice or invoice booking."""
import uuid
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from .models import Document, DocumentLink, Draft, Entry, Project, Task
from .operations import guarded
from .rules import audit, parse_date


def own_scan_entry(document):
    if not document.entry_id:
        return None
    entry = Entry.objects.select_related('project','category','title_template').filter(pk=document.entry_id).first()
    if entry is None:return None
    document.entry=entry
    return entry if entry.is_provisional and entry.snapshot.get('_scan_document') == str(document.pk) else None


def entry_values(entry):
    start = (entry.date.strftime('%Y' if entry.precision == 'year' else '%Y-%m')
             if entry.precision in ('year', 'month') else entry.date.isoformat()) if entry.date else None
    result = {key: getattr(entry, key) or None for key in
              ('title', 'kind', 'status', 'description', 'meter_unit', 'meter_name', 'tags')}
    result.update(date=start, end_date=entry.end_date.isoformat() if entry.end_date else None,
                  meter=str(entry.meter) if entry.meter is not None else None)
    result.update({key: str(entry.snapshot[key]) if entry.snapshot.get(key) not in (None, '') else None
                   for key in ('oil', 'quantity_l', 'filter')})
    return result


@guarded
@transaction.atomic
def ensure_scan_entry(document):
    doc = Document.objects.select_related('entry').get(pk=document.pk)
    if doc.deleted_at or doc.status == 'accepted' or doc.entry_id:
        return doc
    project = doc.project
    if project is None:
        project, _ = Project.objects.get_or_create(owner=doc.owner, is_scan_inbox=True,
            defaults={'name': 'Noch zuzuordnen', 'kind': 'frei',
                      'description': 'Gespeicherte Scans mit Bildern. Das tatsächliche Projekt kann später gewählt werden.'})
    capture_date = timezone.localdate()
    entry, created = Entry.objects.get_or_create(request_key=uuid.uuid5(doc.pk, 'invoice-chronicle'),
        defaults={'project': project, 'is_provisional': True, 'title': 'Neuer Beleg · Prüfung offen',
                  'kind': '', 'status': '', 'date': capture_date, 'precision': 'day', 'description': doc.scan_note,
                  'meter_unit': '', 'meter_name': '',
                  'snapshot': {'_scan_document': str(doc.pk), '_date_source': 'capture',
                               '_capture_date': capture_date.isoformat(), 'project_fields': project.fields}})
    if not entry.is_provisional or entry.project.owner_id != doc.owner_id or entry.deleted_at:
        raise ValidationError('Der zugehörige Eintrag muss zuerst geprüft oder wiederhergestellt werden.')
    if created:
        entry.snapshot['_scan_last_ai'] = entry_values(entry)
        entry.save(update_fields=['snapshot'])
        audit(doc.owner, entry, 'Scan sofort erfasst', {'document': str(doc.pk), 'costs_booked': False})
    doc.entry = entry
    doc.save(update_fields=['entry'])
    if doc.proposal:
        apply_scan_proposal(doc, doc.proposal)
    return doc


def move_scan_entry(doc, project):
    """Called within the job/route write transaction. Photos and steps keep their IDs."""
    entry = own_scan_entry(doc)
    if entry is None:
        if doc.entry_id and doc.entry.project_id != project.pk:
            raise ValidationError('Beleg gehört bereits zu einem Vorgang eines anderen Projekts.')
        return
    if entry.deleted_at:
        raise ValidationError('Eintrag liegt im Papierkorb. Zuerst wiederherstellen.')
    if entry.project_id == project.pk:
        return
    if entry.allocations.exists() or entry.part_events.exists() or Task.objects.filter(entry=entry).exists():
        raise ValidationError('Eintrag besitzt bereits Kosten, Teilehistorie oder Aufgaben. Projektwechsel zuerst gesondert prüfen.')
    previous = entry.project_id
    entry.project = project
    if entry.category_id and entry.category.project_id!=project.pk:
        if entry.snapshot.get('extra_fields'):
            entry.snapshot.setdefault('_previous_extra_fields',[]).append({'title':entry.snapshot.get('title_template_name',''),'fields':entry.snapshot.pop('extra_fields')})
        entry.snapshot['title_template_name']=''
        from .categories import default_category
        entry.category=default_category(project,entry.kind);entry.title_template=None;entry.title_suffix=''
    entry.snapshot['project_fields'] = project.fields
    entry.version += 1
    entry.save(update_fields=['project','category','title_template','title_suffix','snapshot','version'])
    Draft.objects.filter(entry=entry).update(project=project)
    Document.objects.filter(entry=entry, project_id=previous).exclude(pk=doc.pk).update(
        project=project, auto_route=False, version=F('version') + 1)
    from .files import link_document
    for link in list(DocumentLink.objects.filter(entry=entry).select_related('document')):
        if link.project_id != project.pk:
            link_document(link.document, project, entry)
            link.delete()
    # Keep the relation cache in the caller consistent with this transaction.
    doc.entry = entry


def apply_scan_proposal(doc, proposal, *, confirmed=False, manual_definition=False):
    entry = own_scan_entry(doc)
    if entry is None or entry.deleted_at or doc.deleted_at or (doc.status == 'accepted' and not confirmed):
        return
    before=Entry.objects.select_related('title_template').get(pk=entry.pk)
    values = proposal.get('entry') or {}
    if not values:
        return
    previous = entry.snapshot.get('_scan_last_ai', {})
    current = entry_values(entry)
    applied = previous.copy()
    inferred = set(entry.snapshot.get('_inferred_fields', []))
    for key, value in values.items():
        if not manual_definition and (entry.snapshot.get('_definition_selected') or entry.snapshot.get('_category_selected')) and key in ('title','kind','oil','quantity_l','filter'):continue
        # Images/steps do not block enrichment. Human edits to a field do.
        unchanged = current.get(key) == previous.get(key)
        if key in ('meter', 'quantity_l') and current.get(key) not in (None, '') and previous.get(key) not in (None, ''):
            unchanged = Decimal(str(current[key])) == Decimal(str(previous[key]))
        baseline=((doc.edits or doc.proposal).get('entry') or {}).get(key)
        if confirmed and not unchanged and value==baseline:
            # Unchanged invoice fields must not undo later corrections on the entry.
            continue
        if not confirmed and not unchanged:
            continue
        if not confirmed and value in (None, ''):
            continue
        if key in ('oil', 'quantity_l', 'filter'):
            entry.snapshot[key] = value or ''
        elif key == 'date':
            entry.date, entry.precision = parse_date(value)
            if value not in (None, '') or confirmed:
                entry.snapshot.pop('_date_source', None)
        elif key == 'end_date':
            entry.end_date = parse_date(value)[0]
        elif key == 'meter':
            entry.meter = Decimal(value) if value is not None else None
        else:
            setattr(entry, key, value or '')
        applied[key] = value or None
        inferred.discard(key)
        if 'entry.' + key in proposal.get('inferred_fields', []) and value not in (None, ''):
            inferred.add(key)
    entry.snapshot['_scan_last_ai'] = applied
    definition=proposal.get('definition')
    if definition and definition.get('category_id') and not entry.project.is_scan_inbox:
        from .models import Category
        source_project=Category.objects.filter(pk=definition['category_id']).values_list('project_id',flat=True).first()
        if source_project is not None and source_project!=entry.project_id:
            # A manual project correction invalidates the old project's template IDs.
            definition=None
    if definition and (manual_definition or not (entry.snapshot.get('_definition_selected') or entry.snapshot.get('_category_selected'))) and not entry.project.is_scan_inbox:
        from .definitions import apply_definition
        apply_definition(entry,definition,manual=manual_definition,inferred_paths=proposal.get('inferred_fields',[]))
    if not definition and not entry.category_id and not entry.snapshot.get('_category_selected'):
        from .categories import default_category
        entry.category=default_category(entry.project,entry.kind)
    entry.snapshot['_inferred_fields'] = sorted(inferred)
    if confirmed:
        entry.is_provisional = False
    from .definitions import sync_classification
    sync_classification(entry)
    entry.version += 1
    entry.save()
    from .template_maintenance import sync_entry
    sync_entry(entry,before)
    doc.entry = entry


def confirm_scan_entry(owner, doc, proposal, choice, source):
    entry = own_scan_entry(doc)
    project = choice.get('project') or doc.project
    if choice.get('mode') == 'document' or (choice.get('entry') and choice['entry'].pk != entry.pk):
        raise ValidationError('Dieser Scan besitzt bereits einen Eintrag mit möglichen Arbeitsbildern. Bitte diesen Eintrag prüfen; keine zweite Chronik anlegen.')
    if not project or project.owner_id != owner.pk or project.is_scan_inbox or project.archived:
        raise ValidationError('Vor der Übernahme das tatsächliche aktive Projekt auswählen.')
    if entry.deleted_at or entry.project_id != project.pk:
        raise ValidationError('Eintrag zuerst wiederherstellen bzw. über Projektzuordnung in das richtige Projekt verschieben.')
    apply_scan_proposal(doc, proposal, confirmed=True,manual_definition=choice.get('manual_definition',False))
    from .files import link_document
    link_document(doc, project, doc.entry)
    if source:
        source.allocations.filter(project=project, entry__isnull=True, step__isnull=True).update(entry=entry)
        if source.allocations.filter(entry=entry).exists():
            doc.entry.cost_state = 'known'
            doc.entry.save(update_fields=['cost_state'])
    audit(owner, doc.entry, 'Scan geprüft', {'document': str(doc.pk)})
    return doc.entry
