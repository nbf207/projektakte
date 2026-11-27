"""Durable Snap & Talk intake and fenced application of one Gemini result."""
import hashlib
import json
import uuid
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.utils import timezone
from .capture_ai import MAX_CAPTURE_BYTES, MAX_CAPTURE_FILES, analyze_capture
from .files import ingest_asset, link_document, receive_bundle
from .models import Document, DocumentPage, Entry, Job, PhotoLink, Task
from .operations import guarded
from .rules import audit, check_owner
from .scan_entries import ensure_scan_entry


@guarded
def receive_capture(owner, *, text='', uploads=(), audio=None, project=None, token=None, keep_audio=None, source_document=None, mail_source=None):
    if keep_audio is None:
        from .preferences import preferences
        keep_audio=preferences(owner).keep_audio
    text=text.strip()
    if len(text)>8000: raise ValidationError('Notiz auf höchstens 8000 Zeichen begrenzen.')
    if not text and not uploads and not audio: raise ValidationError('Text, Aufnahme oder mindestens eine Datei hinzufügen.')
    if len(uploads)>MAX_CAPTURE_FILES or sum(item.size for item in uploads)+(audio.size if audio else 0)+(source_document.size if source_document else 0)>MAX_CAPTURE_BYTES:
        raise ValidationError('Höchstens 8 Fotos/PDFs und zusammen mit Audio maximal 12 MB verwenden.')
    if project:
        check_owner(owner,project)
        if project.archived or project.is_scan_inbox: raise ValidationError('Aktives eigenes Projekt auswählen.')
    key=uuid.UUID(str(token))
    from .models import DiscardedDraft
    if DiscardedDraft.objects.filter(owner=owner,token=key).exists():raise ValidationError('Dieser Entwurf wurde gelöscht. Bitte eine neue Erfassung beginnen.')
    prior=Document.objects.filter(submission_key=key).first()
    if prior:
        if prior.owner_id!=owner.pk or prior.capture_type!='snap': raise ValidationError('Ungültige Erfassungskennung.')
        if prior.deleted_at: raise ValidationError('Erfassung liegt im Papierkorb.')
        return prior
    assets=[]
    try:
        for upload in uploads:
            if upload.size>8*1024*1024: raise ValidationError('Einzelne Fotos/PDFs dürfen höchstens 8 MB groß sein.')
            asset=ingest_asset(owner,upload,document=True)
            if asset.pk not in {item.pk for item in assets}: assets.append(asset)
        if audio:
            asset=ingest_asset(owner,audio,audio=True)
            if asset.pk not in {item.pk for item in assets}: assets.append(asset)
        if source_document:
            assets.append(ingest_asset(owner,source_document,document=True))
        fingerprint=hashlib.sha256(json.dumps({'text':text,'files':[a.sha256 for a in assets],
            'project':str(project.pk) if project else None},sort_keys=True).encode()).hexdigest()
        if assets:
            prior=Document.objects.filter(owner=owner,capture_type='snap',voice_data__fingerprint=fingerprint).first()
            if prior:
                if prior.deleted_at: raise ValidationError('Diese Erfassung liegt im Papierkorb. Dort wiederherstellen oder endgültig löschen.')
                from .purging import _remove_unused_files
                _remove_unused_files(owner,[asset.pk for asset in assets if getattr(asset,'_created_now',False)],[])
                return prior
        from .preferences import ai_is_active
        from django.utils.translation import gettext as _
        use_ai = ai_is_active()
        has_pdf = any(asset.mime == 'application/pdf' for asset in assets)
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), '')
        manual_title = (first_line or (assets[0].name if assets else '') or _('Schnellerfassung'))[:180]
        with transaction.atomic():
            doc=Document.objects.create(owner=owner,submission_key=key,capture_type='snap',project=project,
                keep_audio=keep_audio,auto_route=bool(use_ai and not project),ai_enabled=use_ai,
                ai_with_original=bool(use_ai and assets),instance='capture',
                status='ai' if use_ai else 'review',
                scan_note=text[:1000],ocr=text,voice_data={'text':text,'fingerprint':fingerprint,**({'mail':mail_source} if mail_source else {})})
            doc=ensure_scan_entry(doc)
            if not use_ai and not has_pdf and project:
                doc.status='accepted'
                doc.save(update_fields=['status'])
            entry=doc.entry
            entry.title=_('Schnellerfassung · wird ausgewertet') if use_ai else manual_title
            entry.description=text
            if not use_ai and project:
                entry.is_provisional=False
            entry.snapshot['_scan_last_ai'].update(title=entry.title,description=text or None)
            entry.snapshot['capture_document']=str(doc.pk)
            entry.save(update_fields=['title','description','is_provisional','snapshot'])
            for position,asset in enumerate(assets):
                DocumentPage.objects.create(document=doc,asset=asset,position=position)
                if asset.mime.startswith('image/'):
                    PhotoLink.objects.get_or_create(request_key=uuid.uuid5(doc.pk,str(asset.pk)),
                        defaults={'asset':asset,'entry':entry,'position':position})
            link_document(doc,project,entry)
            if use_ai:
                Job.objects.create(document=doc,kind='capture',stage='capture')
            return doc
    except BaseException:
        from .purging import _remove_unused_files
        _remove_unused_files(owner,[asset.pk for asset in assets if getattr(asset,'_created_now',False)],[])
        raise


def read_inputs(doc):
    result=[];size=0
    for page in doc.pages.select_related('asset').order_by('position'):
        with page.asset.file.open('rb') as stream: raw=stream.read(MAX_CAPTURE_BYTES+1-size)
        size+=len(raw)
        if size>MAX_CAPTURE_BYTES: raise ValidationError('Gespeicherte Erfassung überschreitet 12 MB.')
        result.append({'name':page.asset.name,'mime':page.asset.mime,'data':raw})
    return result


def discard_capture_audio(doc):
    if doc.keep_audio: return
    pages=doc.pages.filter(asset__mime__startswith='audio/')
    ids=list(pages.values_list('asset_id',flat=True));pages.delete()
    from .purging import _remove_unused_files
    _remove_unused_files(doc.owner,ids,[])


def record_capture_details(doc,result):
    entry=doc.entry;p=result['proposal'];values=p['entry']
    entry.snapshot.update(capture_category=result['category'],capture_parts=p.get('positions') or [],
        capture_amount=p.get('gross_total'),capture_currency=p.get('currency'),capture_transcript=result['transcript'],
        capture_missing=[label for key,label in [('date','Datum'),('meter','Zählerstand'),('kind','Vorgangsart')] if values.get(key) is None])
    match=result['open_entry']
    related=Entry.objects.filter(pk=match['id'],project=entry.project,deleted_at__isnull=True).exclude(status='erledigt').first() if match['id'] else None
    if related:
        entry.snapshot['capture_related_entry']={'id':str(related.pk),'title':related.title,
            'reason':match['reason'],'confidence':match['confidence']}
    else: entry.snapshot.pop('capture_related_entry',None)
    entry.save(update_fields=['snapshot'])


def finish_chronicle(doc):
    """No financial booking: an extracted amount remains an explicit cost note."""
    entry=doc.entry
    from .media_controls import compress_capture_photos
    compress_capture_photos(doc)
    if not doc.project_id or entry.project.is_scan_inbox:
        doc.status='review';doc.save(update_fields=['status']);return
    entry.is_provisional=False;entry.version+=1;entry.save(update_fields=['is_provisional','version'])
    from .template_maintenance import sync_entry
    sync_entry(entry)
    if doc.voice_data.get('result',{}).get('category')=='aufgabe':
        due_date=entry.date if entry.precision=='day' and entry.snapshot.get('_date_source')!='capture' else None
        Task.objects.get_or_create(pk=uuid.uuid5(doc.pk,'capture-task'),defaults={'project':entry.project,'entry':entry,
            'category':entry.category,
            'title':entry.title or 'Aufgabe aus Schnellerfassung','due':due_date,
            'done':entry.status=='erledigt'})
    doc.status='accepted';doc.version+=1;doc.save(update_fields=['status','version'])


def receipt_draft(doc,result,inputs):
    """Reuse normal invoice identity checks and the existing Paperless pipeline."""
    uploads=[SimpleUploadedFile(inputs[i]['name'],inputs[i]['data'],content_type=inputs[i]['mime']) for i in result['receipt_files']]
    receipt,new=receive_bundle(doc.owner,uploads,doc.project,doc.entry,note=doc.voice_data.get('text','')[:1000])
    if new:
        receipt.proposal=result['proposal'];receipt.ai_enabled=False
        receipt.save(update_fields=['proposal','ai_enabled'])
        doc.entry.snapshot['_scan_document']=str(receipt.pk)
        doc.entry.save(update_fields=['snapshot'])
    else:
        # Existing receipt and its booked costs are authoritative, never replaced.
        doc.entry.is_provisional=False;doc.entry.save(update_fields=['is_provisional'])
    doc.voice_data['receipt_id']=str(receipt.pk)
    doc.status='accepted';doc.version+=1;doc.save(update_fields=['voice_data','status','version'])


@guarded
@transaction.atomic
def commit_capture(job,result,inputs,version):
    from .worker import commit_job
    current=Job.objects.filter(pk=job.pk,status='running',lease_token=job.lease_token,leased_until__gt=timezone.now()).first()
    if not current: return False
    doc=Document.objects.select_related('entry__project','project').get(pk=job.document_id)
    if doc.status=='accepted' or doc.deleted_at:
        commit_job(job,status='done',leased_until=None);return False
    if not doc.entry_id or doc.entry.deleted_at:
        commit_job(job,{'status':'review','error':'Eintrag fehlt oder liegt im Papierkorb. Bitte zuerst wiederherstellen.'},status='blocked',leased_until=None);return False
    if not commit_job(job,{'proposal':result['proposal'],'ocr':result['transcript'],
        'voice_data':{**doc.voice_data,'result':result},'status':'review','error':''},
        project_assignment=result['proposal']['project_assignment'],routing_version=version,status='done',leased_until=None,error='',attempts=0): return False
    doc=Document.objects.select_related('entry__project','project').get(pk=doc.pk)
    record_capture_details(doc,result)
    from .entry_merge import strong_open_entry_match, merge_entries
    matched_entry=strong_open_entry_match(doc,result)
    if matched_entry is not None:
        merge_entries(doc.owner,doc.entry,matched_entry,reason=(result.get('open_entry') or {}).get('reason') or 'Eindeutige Referenz')
        doc=Document.objects.select_related('entry__project','project').get(pk=doc.pk)
    from .knowledge import record_suggestions
    record_suggestions(doc,result.get('knowledge',[]))
    if result['is_receipt']: receipt_draft(doc,result,inputs)
    else: finish_chronicle(doc)
    discard_capture_audio(doc)
    audit(doc.owner,doc,'Schnellerfassung ausgewertet',{'entry':str(doc.entry_id),'receipt':result['is_receipt'],'costs_booked':False})
    return True


def process_capture(job):
    doc=Document.objects.select_related('project','owner').get(pk=job.document_id)
    if doc.deleted_at or doc.status=='accepted':
        from .worker import commit_job
        commit_job(job,status='done',leased_until=None);return
    inputs=read_inputs(doc)
    result=analyze_capture(doc,text=doc.voice_data.get('mail',{}).get('full_text',doc.voice_data.get('text','')),attachments=inputs)
    commit_capture(job,result,inputs,doc.version)
