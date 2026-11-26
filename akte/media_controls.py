"""Photo-only compression and reference-checked cleanup under the write gate."""
import hashlib
import os
from datetime import timedelta
from pathlib import Path
from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from PIL import Image, ImageOps
from .models import Asset, Document, Entry, PhotoLink
from .operations import guarded
from .preferences import preferences
from .purging import purge_item, source_fingerprint


def compress_photo(path, owner):
    pref=preferences(owner)
    oversized=path.stat().st_size>5*1024*1024
    if not pref.photo_width and not oversized:return False
    temporary=path.with_suffix('.photo.tmp')
    try:
        with Image.open(path) as source:
            image=ImageOps.exif_transpose(source)
            width=pref.photo_width or min(image.width,4096)
            resized=image.width>width
            if resized:
                image=image.resize((width,max(1,round(image.height*width/image.width))),Image.Resampling.LANCZOS)
            if image.mode in ('RGBA','LA') or 'transparency' in image.info:
                rgba=image.convert('RGBA');background=Image.new('RGB',rgba.size,'white');background.paste(rgba,mask=rgba.getchannel('A'));image=background
            image=image.convert('RGB')
            image.save(temporary,'JPEG',quality=pref.photo_quality)
            while temporary.stat().st_size>5*1024*1024:
                image=image.resize((max(1,image.width*3//4),max(1,image.height*3//4)),Image.Resampling.LANCZOS)
                image.save(temporary,'JPEG',quality=min(pref.photo_quality,85))
        if resized or temporary.stat().st_size<path.stat().st_size:
            os.replace(temporary,path);return True
        return False
    finally:temporary.unlink(missing_ok=True)


def compress_capture_photos(doc):
    """Only after AI has ruled out a receipt. Shared/invoice originals survive."""
    if doc.voice_data.get('result',{}).get('is_receipt',True):return
    from .files import ingest_asset
    from .purging import _remove_unused_files
    previous=[]
    for page in doc.pages.select_related('asset').filter(asset__mime__startswith='image/'):
        original=page.asset
        with original.file.open('rb') as stream:
            newer=ingest_asset(doc.owner,SimpleUploadedFile(original.name,stream.read()))
        if newer.pk==original.pk:continue
        page.asset=newer;page.save(update_fields=['asset'])
        PhotoLink.objects.filter(entry=doc.entry,asset=original).update(asset=newer)
        previous.append(original.pk)
    _remove_unused_files(doc.owner,previous,[])


def safe_media(relative):
    root=settings.MEDIA_ROOT.resolve();path=root/relative
    if path.is_symlink() or any(p.is_symlink() for p in path.parents if p!=root and p.is_relative_to(root)):
        raise ValidationError('Symbolische Dateiverknüpfung ausgeschlossen.')
    target=path.resolve()
    if not target.is_relative_to(root) or not target.is_file():raise ValidationError('Ungültiger Medienpfad.')
    return target


def referenced(relative):
    return Asset.objects.filter(Q(file=relative)|Q(thumbnail=relative)).exists()


@guarded
def orphan_files(owner):
    rows=[];cutoff=timezone.now().timestamp()-600
    for folder in ('originals','thumbs'):
        base=settings.MEDIA_ROOT/folder
        if not base.is_dir() or base.is_symlink():continue
        for path in base.rglob('*'):
            if len(rows)>=500:break
            if path.suffix=='.tmp':continue
            relative=path.relative_to(settings.MEDIA_ROOT).as_posix()
            try:target=safe_media(relative);stat=target.stat()
            except (ValidationError,OSError):continue
            if stat.st_mtime>cutoff or referenced(relative):continue
            token=signing.dumps({'owner':owner.pk,'path':relative,'size':stat.st_size,'mtime':stat.st_mtime_ns},salt='orphan074')
            rows.append({'name':relative,'size':stat.st_size,'token':token})
        if len(rows)>=500:break
    return rows


@guarded
def remove_orphans(owner,tokens):
    removed=0
    if len(tokens)>500:raise ValidationError('Höchstens 500 Dateien gleichzeitig bereinigen.')
    for token in tokens:
        try:data=signing.loads(token,salt='orphan074',max_age=600)
        except signing.BadSignature:raise ValidationError('Dateiliste abgelaufen. Erneut prüfen.') from None
        if data['owner']!=owner.pk:raise ValidationError('Fremde Dateiliste.')
        target=safe_media(data['path']);stat=target.stat()
        if referenced(data['path']) or stat.st_size!=data['size'] or stat.st_mtime_ns!=data['mtime']:
            raise ValidationError('Datei inzwischen verändert oder verwendet. Erneut prüfen.')
        target.unlink();removed+=1
    return removed


def trash_items(owner, before=None):
    queries=[Document.objects.filter(owner=owner,deleted_at__isnull=False),
        PhotoLink.objects.filter(entry__project__owner=owner,deleted_at__isnull=False),
        Entry.objects.filter(project__owner=owner,deleted_at__isnull=False)]
    return [item for query in queries for item in (query.filter(deleted_at__lte=before) if before else query).order_by('deleted_at')[:500]]


@guarded
def empty_trash(owner, *, before=None, remove_costs=False):
    removed=0;blocked=0
    from django.db.models.deletion import ProtectedError
    for item in trash_items(owner,before):
        if not type(item).objects.filter(pk=item.pk).exists():continue
        source=getattr(item,'source',None) if isinstance(item,Document) else None
        try:
            purge_item(owner,item,item.version,remove_costs=remove_costs,
                cost_version=source.version if source else None,cost_fingerprint=source_fingerprint(source) if source else None)
            removed+=1
        except (ValidationError,ProtectedError):blocked+=1
    return removed,blocked
