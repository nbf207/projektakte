"""Owner-scoped JSON feedback and private image attachments on persistent storage."""
import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import Http404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .operations import guarded
from .private_files import private_path
from .storage import durable_replace


class FeedbackImage(forms.ImageField):
    def clean(self,data,initial=None):
        if data:
            if data.size>5*1024*1024:raise ValidationError('Bild ist größer als 5 MB.')
            from PIL import Image
            try:
                with Image.open(data,formats=('JPEG','PNG','WEBP')) as image:
                    if image.width*image.height>settings.MAX_IMAGE_PIXELS:raise ValidationError('Bildabmessungen zu groß.')
            except (OSError,Image.DecompressionBombError,Image.DecompressionBombWarning):raise ValidationError('Ungültiges oder zu großes Bild.') from None
            finally:data.seek(0)
        upload=super().clean(data,initial)
        if not upload:return None
        if upload.size>5*1024*1024 or upload.image.format not in ('JPEG','PNG','WEBP') or upload.image.width*upload.image.height>settings.MAX_IMAGE_PIXELS:
            raise ValidationError('Bild als JPEG, PNG oder WebP verwenden: höchstens 5 MB / 20 Megapixel.')
        return upload


def feedback_path(key):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',key):raise Http404
    return private_path('feedback',key+'.json')


def read_feedback(owner,key):
    path=feedback_path(key)
    try:
        if path.stat().st_size>100_000:raise ValueError()
        raw=path.read_bytes();record=json.loads(raw)
        if not isinstance(record,dict) or not isinstance(record.get('user'),dict) or not isinstance(record.get('text'),str):raise ValueError()
        if not isinstance(record.get('images',[]),list) or any(not isinstance(row,dict) for row in record.get('images',[])):raise ValueError()
        if record.get('user',{}).get('id')!=owner.pk:raise Http404
        return record,hashlib.sha256(raw).hexdigest()
    except (ValueError,OSError,TypeError):raise Http404 from None


def list_feedback(owner):
    rows=[];folder=settings.DATA_DIR/'feedback'
    if folder.is_dir():
        for path in folder.glob('*.json'):
            try:record,digest=read_feedback(owner,path.stem)
            except Http404:continue
            try:created=parse_datetime(record.get('timestamp',''))
            except (ValueError,TypeError):created=None
            rows.append({'key':path.stem,'record':record,'digest':digest,'created':created})
    # Sort normalized datetimes, not untrusted JSON strings or mixed value types.
    def order(row):
        created=row['created']
        if created is None:return float('-inf')
        if timezone.is_naive(created):created=timezone.make_aware(created,timezone.get_default_timezone())
        return created.timestamp()
    return sorted(rows,key=order,reverse=True)


def feedback_prompt(rows):
    lines=['Bitte bearbeite die offenen Feedbackpunkte für Projektakte '+settings.APP_VERSION+'.',
        'Prüfe gezielt den betroffenen Code, sichere Daten und Kostenbuchungen und ergänze direkte Tests.']
    for index,row in enumerate([r for r in rows if r['record'].get('status')!='done'],1):
        data=row['record']
        lines.extend(['',f'{index}. {data.get("text","")}',
            'Kontext: '+str(data.get('page_title') or '')+' · '+str(data.get('url') or ''),
            'Projekt: '+str(data.get('project') or '–')+' · Vorgang: '+str(data.get('entry') or '–')+' · Version: '+str(data.get('app_version') or ''),
            'Umsetzung: '+('Von Codex umgesetzt, Nutzerprüfung offen' if data.get('implemented') else 'Noch offen'),
            'Feedback-ID: '+row['key']])
    return '\n'.join(lines)


def attach_image(record,upload):
    if upload is None:return None
    folder=settings.DATA_DIR/'feedback/images';folder.mkdir(parents=True,mode=0o700,exist_ok=True)
    identity=uuid.uuid4().hex;extension={'JPEG':'.jpg','PNG':'.png','WEBP':'.webp'}[upload.image.format]
    path=folder/(identity+extension)
    with path.open('xb') as stream:
        os.chmod(path,0o600)
        for chunk in upload.chunks():stream.write(chunk)
        stream.flush();os.fsync(stream.fileno())
    record.setdefault('images',[]).append({'id':identity,'file':'images/'+path.name,'name':Path(upload.name.replace('\\','/')).name[:150]})
    return path


def write_feedback(path,record):
    path.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    temporary=path.parent/(uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf8') as stream:
            os.chmod(temporary,0o600);json.dump(record,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
        durable_replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


@guarded
def edit_feedback(owner,key,text,done,expected,image=None,implemented=None):
    record,digest=read_feedback(owner,key)
    if digest!=expected:raise ValidationError('Feedback wurde zwischenzeitlich geändert. Bitte neu laden.')
    record.update(text=text,status='done' if done else 'open',updated=timezone.now().isoformat())
    if implemented is not None:record['implemented']=bool(implemented)
    attachment=None
    try:
        attachment=attach_image(record,image);write_feedback(feedback_path(key),record)
    except BaseException:
        if attachment:attachment.unlink(missing_ok=True)
        raise


def image_path(owner,key,identity):
    record,_=read_feedback(owner,key)
    row=next((row for row in record.get('images',[]) if row.get('id')==identity),None)
    if row is None or not re.fullmatch(r'images/[a-f0-9]{32}\.(jpg|png|webp)',row.get('file','')):raise Http404
    return private_path('feedback',row['file'])
