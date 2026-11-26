"""Durable originals, deduplication, thumbnails and document intake."""
import hashlib
import os
import uuid
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from PIL import Image
from .models import ArchiveReference, Asset, Document, DocumentLink, DocumentPage, Job
from .operations import guarded, operation_gate
from .rules import check_owner
from .storage import durable_replace

def ingest_asset(owner, upload, *, document=False, import_source=False, audio=False):
    """Stream to SSD; fsync and atomic rename before acknowledging acceptance."""
    from .demo_boundary import deny_live_operation
    deny_live_operation()
    limit=5*1024*1024 if import_source else (settings.MAX_DOCUMENT_BYTES if document else settings.MAX_PHOTO_BYTES)
    if audio: limit=8*1024*1024
    incoming=settings.MEDIA_ROOT/'incoming';incoming.mkdir(parents=True,exist_ok=True)
    tmp=incoming/(str(uuid.uuid4())+'.tmp');digest=hashlib.sha256();size=0
    target=None
    try:
        with tmp.open('wb') as f:
            for chunk in upload.chunks():
                size+=len(chunk)
                if size>limit: raise ValidationError(f'Datei zu groß. Grenze: {limit//1024//1024} MB.')
                digest.update(chunk);f.write(chunk)
            f.flush();os.fsync(f.fileno())
        with tmp.open('rb') as f: head=f.read(8)
        if audio:
            from .voice import audio_mime
            with tmp.open('rb') as stream: audio_head=stream.read(64)
            mime,ext=audio_mime(audio_head)
        elif import_source and Path(upload.name).suffix.lower() in ('.csv','.json'):
            tmp.read_text(encoding='utf-8-sig')
            mime,ext=('text/csv','.csv') if Path(upload.name).suffix.lower()=='.csv' else ('application/json','.json')
        elif head.startswith(b'%PDF-') and document:
            from .pdf_limits import validate_pdf
            validate_pdf(tmp)
            mime,ext='application/pdf','.pdf'
        else:
            try:
                with Image.open(tmp,formats=('JPEG','PNG','WEBP')) as img:
                    if img.format not in ('JPEG','PNG','WEBP') or img.width*img.height>settings.MAX_IMAGE_PIXELS: raise ValidationError(f'Bildformat/Abmessungen nicht unterstützt (maximal {settings.MAX_IMAGE_PIXELS//1_000_000} Megapixel).')
                    mime,ext={'JPEG':('image/jpeg','.jpg'),'PNG':('image/png','.png'),'WEBP':('image/webp','.webp')}[img.format]
                    img.verify()
            except (OSError,Image.DecompressionBombError,Image.DecompressionBombWarning): raise ValidationError('Ungültiges Bild. JPEG, PNG, WebP oder als Beleg PDF verwenden.')
        if not document and not audio and not import_source:
            from .media_controls import compress_photo
            if compress_photo(tmp,owner):
                mime,ext='image/jpeg','.jpg';size=tmp.stat().st_size
                with tmp.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256')
        with operation_gate(), transaction.atomic():
            old=Asset.objects.filter(owner=owner,sha256=digest.hexdigest()).first()
            if old: return old
            name='originals/'+str(uuid.uuid4())+ext
            target=settings.MEDIA_ROOT/name;target.parent.mkdir(exist_ok=True)
            durable_replace(tmp,target)
            try:
                display_name=Path(upload.name.replace('\\','/')).name
                display_name=''.join(character for character in display_name if ord(character)>=32 and ord(character)!=127)[:250]
                asset=Asset.objects.create(owner=owner,file=name,sha256=digest.hexdigest(),name=display_name or ('Upload'+ext),mime=mime,size=size)
                if mime.startswith('image/'):
                    Job.objects.create(asset=asset,kind='thumbnail',stage='thumbnail')
            except BaseException:
                target.unlink(missing_ok=True)
                raise
            asset._created_now=True
            return asset
    except BaseException:
        if target is not None:
            # An interruption can happen AFTER SQLite committed. Never remove a
            # referenced original; if DB outcome is unknown retain it for recovery.
            try: referenced=Asset.objects.filter(file=name).exists()
            except DatabaseError: referenced=True
            if not referenced: target.unlink(missing_ok=True)
        raise
    finally:
        tmp.unlink(missing_ok=True)

@guarded
def receive_document(owner,upload,project=None,entry=None,*,note='',auto_route=False,create_entry=False):
    if not isinstance(note,str) or len(note)>1000: raise ValidationError('Zusatztext auf höchstens 1.000 Zeichen begrenzen.')
    if project: check_owner(owner,project)
    if entry:
        check_owner(owner,entry)
        if project and entry.project_id!=project.pk: raise ValidationError('Unpassender Vorgang.')
    asset=ingest_asset(owner,upload,document=True)
    try:
      with transaction.atomic():
        doc,new=Document.objects.get_or_create(owner=owner,asset=asset,defaults={'project':project,'entry':entry,'ai_with_original':bool(create_entry and asset.size<=settings.GEMINI_ORIGINAL_MAX_BYTES),'scan_note':note,'auto_route':bool(auto_route and not project and not entry),'instance':'demo' if settings.DEMO_MODE else settings.PAPERLESS_URL,'status':'paperless'})
        if doc.deleted_at: raise ValidationError('Dieser Beleg liegt im Papierkorb. Dort wiederherstellen; kein zweiter Upload und keine zweite Kostenquelle.')
        if new:
            previous=ArchiveReference.objects.filter(owner=owner,instance=doc.instance,sha256=asset.sha256).first()
            if previous:
                doc.paperless_id=previous.paperless_id
                doc.submission_key=previous.submission_key
                doc.task_id=previous.task_id
                doc.submitted_at=previous.submitted_at
                doc.status='ocr'
                doc.save(update_fields=['paperless_id','submission_key','task_id','submitted_at','status'])
            Job.objects.create(document=doc,stage='reuse' if previous else 'upload')
        if create_entry:
            from .scan_entries import ensure_scan_entry
            doc=ensure_scan_entry(doc)
    except BaseException:
        if getattr(asset,'_created_now',False) and not asset.links.exists() and not Document.objects.filter(asset=asset).exists():
            name=Path(asset.file.path)
            asset.delete()
            name.unlink(missing_ok=True)
        raise
    link_document(doc,project,entry)
    return doc,new

@guarded
def thumbnail(asset):
    from PIL import ImageOps
    target=settings.MEDIA_ROOT/'thumbs'/f'{asset.pk}.jpg';target.parent.mkdir(exist_ok=True)
    with Image.open(asset.file.path,formats=('JPEG','PNG','WEBP')) as original:
        image=ImageOps.exif_transpose(original)
        image.thumbnail((1000,750))
        temporary=target.with_suffix('.tmp')
        try:
            image.convert('RGB').save(temporary,'JPEG',quality=82)
            durable_replace(temporary,target)
        finally: temporary.unlink(missing_ok=True)
    asset.thumbnail=f'thumbs/{asset.pk}.jpg';asset.error='';asset.save(update_fields=['thumbnail','error'])



@guarded
def link_document(doc, project=None, entry=None):
    if doc.deleted_at: raise ValidationError('Beleg liegt im Papierkorb. Zuerst wiederherstellen.')
    project = entry.project if entry else project
    if project:
        check_owner(doc.owner, project)
        identity = uuid.uuid5(doc.pk, str(project.pk)+':'+str(entry.pk if entry else ''))
        DocumentLink.objects.get_or_create(identity=identity, defaults={'document': doc, 'project': project, 'entry': entry})


@guarded
def receive_bundle(owner, uploads, project=None, entry=None,*,note='',auto_route=False,create_entry=False):
    """PDF/image pages become one deterministic bill; original pages are retained."""
    if len(uploads)==1:
        return receive_document(owner,uploads[0],project,entry,note=note,auto_route=auto_route,create_entry=create_entry)
    if not 1 < len(uploads) <= 20 or sum(upload.size for upload in uploads)>settings.MAX_DOCUMENT_BYTES:
        raise ValidationError('Zusammen höchstens 20 Dateien und 50 MB pro Rechnung verwenden.')
    from django.core.files.uploadedfile import SimpleUploadedFile
    from .pdf_limits import merge_pdf_files
    assets=[]
    try:
        for upload in uploads:
            assets.append(ingest_asset(owner,upload,document=True))
        combined=merge_pdf_files(assets)
        doc,new=receive_document(owner,SimpleUploadedFile('Rechnung-mehrseitig.pdf',combined),project,entry,note=note,auto_route=auto_route,create_entry=create_entry)
        with transaction.atomic():
            for position,asset in enumerate(assets):
                DocumentPage.objects.get_or_create(document=doc,position=position,defaults={'asset':asset})
        return doc,new
    except BaseException:
        # Failed grouping has not acknowledged the individual source pages.
        for asset in assets:
            if getattr(asset,'_created_now',False) and not asset.links.exists() and not asset.document_pages.exists() and not Document.objects.filter(asset=asset).exists():
                original=Path(asset.file.path)
                asset.delete();original.unlink(missing_ok=True)
        raise
