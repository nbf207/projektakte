"""Bounded IMAP intake in the existing serial worker. No outgoing mail."""
import hashlib
import imaplib
import io
import logging
import re
import ssl
import time
import uuid
from dataclasses import replace
from email import policy
from email.parser import BytesParser,BytesHeaderParser
from email.utils import getaddresses
from html.parser import HTMLParser
from pathlib import Path
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from .capture import receive_capture
from .capture_ai import MAX_CAPTURE_BYTES,MAX_CAPTURE_FILES
from .mail_config import mail_config
from .models import Project

logger=logging.getLogger(__name__)
MAX_MAIL_BYTES=18*1024*1024
MAX_ATTACHMENT_BYTES=8*1024*1024


class BoundedIMAP(imaplib.IMAP4_SSL):
    literal_limit=65536
    def read(self,size):
        if size<0 or size>self.literal_limit:
            raise imaplib.IMAP4.abort('IMAP literal exceeds configured limit')
        return super().read(size)
    def readline(self):
        # Use the same buffer as literal reads (Python 3.14 no longer uses
        # self.file). Reading through the base reader also bounds hostile lines
        # before allocating more than 64 KB.
        line=bytearray()
        while len(line)<=65536:
            char=super().read(1)
            if not char:break
            line.extend(char)
            if char==b'\n':break
        if len(line)>65536:raise imaplib.IMAP4.abort('IMAP line exceeds configured limit')
        return bytes(line)


class BoundedBytesIO(io.BytesIO):
    def write(self,value):
        if self.tell()+len(value)>MAX_ATTACHMENT_BYTES:raise ValidationError('Bildanhang ist nach Umwandlung zu groß.')
        return super().write(value)


class MailText(HTMLParser):
    def __init__(self): super().__init__(convert_charrefs=True);self.parts=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','head'):self.hidden+=1
        if not self.hidden and tag in ('br','p','div','tr','li','h1','h2'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style','head'):self.hidden=max(0,self.hidden-1)
        elif not self.hidden and tag in ('p','div','tr','li'):self.parts.append('\n')
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)


def allowed_sender(message,allowed):
    addresses=getaddresses(message.get_all('From',[]))
    return len(addresses)==1 and addresses[0][1].strip().casefold()==allowed.casefold()


def authenticated_sender(message,config):
    if not allowed_sender(message,config.sender):return False
    if config.host.casefold()!='imap.gmail.com':return True
    # Gmail prepends its own receiver authentication results. Never accept a
    # later sender-supplied header or a display-name-only sender match.
    results=message.get_all('Authentication-Results',[])
    if not results:return False
    result=str(results[0])
    return bool(re.match(r'^\s*mx\.google\.com\s*;',result,re.IGNORECASE)
        and re.search(r'\bdmarc=pass\b',result,re.IGNORECASE)
        and re.search(r'header\.from='+re.escape(config.sender.rsplit('@',1)[-1])+r'(?:[;\s]|$)',result,re.IGNORECASE))


def subject_project(owner,subject):
    exact=[];fallback=[]
    for project in Project.objects.filter(owner=owner,archived=False,is_scan_inbox=False):
        aliases={project.name}
        first=re.split(r'[\s-]+',project.name)[0]
        prefix=first if len(first)>=2 else None
        for key,value in project.fields.items():
            if key.casefold() in ('kuerzel','kürzel','kurzname','short_name','short','code') and isinstance(value,dict) and value.get('value'):
                aliases.add(str(value['value']))
        if any(re.search(r'(?<!\w)'+re.escape(alias)+r'(?!\w)',subject,re.IGNORECASE) for alias in aliases if alias.strip()):exact.append(project)
        elif prefix and re.search(r'(?<!\w)'+re.escape(prefix)+r'(?!\w)',subject,re.IGNORECASE):fallback.append(project)
    matches=exact or fallback
    return matches[0] if len(matches)==1 else None


def body_text(message):
    body=message.get_body(preferencelist=('plain','html'))
    if body is None:return ''
    encoded=body.get_payload()
    if not isinstance(encoded,str) or len(encoded)>384*1024:raise ValidationError('E-Mail-Text zu groß.')
    text=body.get_content()
    if not isinstance(text,str):raise ValidationError('E-Mail-Text nicht lesbar.')
    if body.get_content_type()=='text/html':
        parser=MailText();parser.feed(text);text=''.join(parser.parts)
    return text.strip()


def mail_source_pdf(text):
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import simpleSplit
    out=io.BytesIO();pdf=canvas.Canvas(out,pagesize=(595,842),invariant=1)
    pdf.setTitle('E-Mail-Quelle – keine eigenständige Rechnung');y=790
    for line in ('E-MAIL-QUELLE - KEINE EIGENSTAENDIGE RECHNUNG\n'+text).splitlines():
        for wrapped in simpleSplit(line,'Helvetica',10,500) or ['']:
            if y<50:pdf.showPage();y=790
            pdf.setFont('Helvetica',10);pdf.drawString(45,y,wrapped);y-=15
    pdf.save()
    return SimpleUploadedFile('Projektakte-Mailquelle.pdf',out.getvalue(),content_type='application/pdf')


def image_upload(name,mime,data):
    if mime in ('image/jpeg','image/png','image/webp'):return SimpleUploadedFile(name,data,content_type=mime)
    from PIL import Image
    with Image.open(io.BytesIO(data),formats=('JPEG','PNG','WEBP','GIF')) as image:
        if image.width*image.height>settings.MAX_IMAGE_PIXELS or getattr(image,'n_frames',1)>1:
            raise ValidationError('Mehrbild- oder übergroßen Bildanhang als PDF/JPEG senden.')
        out=BoundedBytesIO();image.convert('RGB').save(out,'PNG')
    return SimpleUploadedFile(name+'.png',out.getvalue(),content_type='image/png')


def import_message(owner,config,raw):
    if len(raw)>MAX_MAIL_BYTES:raise ValidationError('E-Mail überschreitet 18 MB.')
    if raw.lower().count(b'content-type:')>128:raise ValidationError('E-Mail zu stark verschachtelt.')
    message=BytesParser(policy=policy.default).parsebytes(raw)
    if not authenticated_sender(message,config):return None
    subject=str(message.get('Subject','')).strip()
    text='Betreff: '+subject+'\nAbsender: '+config.sender+'\nE-Mail-Datum (kein Arbeits-/Rechnungsdatum): '+str(message.get('Date',''))+'\n\n'+body_text(message)
    if len(text)>60000:raise ValidationError('E-Mail-Text überschreitet 60000 Zeichen.')
    uploads=[];decoded_size=0
    for position,part in enumerate(message.walk()):
        if part.is_multipart():continue
        mime=part.get_content_type();filename=part.get_filename() or ''
        if not (mime.startswith('image/') or mime=='application/pdf' or filename.lower().endswith(('.pdf','.jpg','.jpeg','.png','.webp'))):continue
        if len(uploads)>=MAX_CAPTURE_FILES:raise ValidationError('Mehr als acht Anhänge.')
        encoded=part.get_payload()
        if not isinstance(encoded,str) or len(encoded)>12*1024*1024:raise ValidationError('Anhang zu groß.')
        if str(part.get('Content-Transfer-Encoding','')).casefold()=='base64':
            encoded_size=len(encoded)-sum(encoded.count(char) for char in '\r\n\t ')
            estimate=(encoded_size//4)*3-encoded.rstrip()[-2:].count('=')
        else:estimate=len(encoded)
        if estimate>MAX_ATTACHMENT_BYTES or decoded_size+estimate>MAX_CAPTURE_BYTES:raise ValidationError('Anhang überschreitet Dekodiergrenze.')
        payload=part.get_payload(decode=True)
        if payload is None or part.defects:raise ValidationError('Bild-/PDF-Anhang beschädigt.')
        if len(payload)>MAX_ATTACHMENT_BYTES or decoded_size+len(payload)>MAX_CAPTURE_BYTES:raise ValidationError('Anhänge zu groß.')
        decoded_size+=len(payload)
        filename=Path(filename.replace('\\','/')).name or ('Anhang-'+str(position)+('.pdf' if mime=='application/pdf' else '.png'))
        upload=image_upload(filename,mime,payload) if mime.startswith('image/') else SimpleUploadedFile(filename,payload,content_type=mime)
        uploads.append(upload)
    if len(uploads)>MAX_CAPTURE_FILES:raise ValidationError('E-Mail enthält mehr als acht Bild-/PDF-Anhänge.')
    source=mail_source_pdf(text)
    if sum(upload.size for upload in uploads)+source.size>MAX_CAPTURE_BYTES:raise ValidationError('Anhänge und Mailquelle überschreiten zusammen 12 MB.')
    fingerprint=hashlib.sha256(raw).hexdigest()
    key=uuid.uuid5(uuid.NAMESPACE_URL,'projektakte-imap:'+config.host.casefold()+':'+config.user.casefold()+':'+fingerprint)
    metadata={'subject':subject,'sender':config.sender,'message_hash':fingerprint,'full_text':text}
    return receive_capture(owner,text=text[:8000],uploads=uploads,project=subject_project(owner,subject),token=key,
        source_document=source,mail_source=metadata)


def fetch_pair(response):
    for item in response or []:
        if isinstance(item,tuple) and len(item)==2 and isinstance(item[1],bytes):return item
    raise ValidationError('IMAP-Nachricht nicht lesbar.')


def capture_owner(config):
    users=get_user_model().objects.filter(is_active=True)
    if config.capture_user:return users.get(username=config.capture_user)
    ids=list(Project.objects.filter(owner__is_active=True,is_scan_inbox=False).values_list('owner_id',flat=True).distinct()[:2])
    if len(ids)!=1:raise ValueError('MAIL_CAPTURE_USER für das Zielkonto konfigurieren.')
    return users.get(pk=ids[0])


class MailPoller:
    def __init__(self):self.next_check=0;self.cursor=0
    def tick(self):
        if time.monotonic()<self.next_check or (settings.DATA_DIR/'.maintenance').exists():return 0
        self.next_check=time.monotonic()+120
        try:
            config=mail_config()
            if config is None:return 0
            return self.collect(config)
        except imaplib.IMAP4.error:
            logger.warning('IMAP-Anmeldung oder Postfachzugriff fehlgeschlagen; beim nächsten Intervall erneut versuchen.')
        except (OSError,ValueError,ValidationError,get_user_model().DoesNotExist):
            logger.warning('IMAP-Abruf nicht abgeschlossen; Konfiguration/Verbindung prüfen. Nicht übernommene Nachrichten bleiben ungelesen.')
        except Exception:
            logger.warning('IMAP-Abruf abgebrochen. Keine Nachricht wird ohne bestätigte lokale Ablage als gelesen markiert.')
        return 0

    def collect(self,config):
        owner=capture_owner(config);count=0;deadline=time.monotonic()+25
        from .preferences import preferences
        pref=preferences(owner);allowed=pref.mail_senders if pref.mail_override else [config.sender]
        if not allowed:return 0
        for sender in allowed:
            address=getaddresses([sender])
            if len(address)!=1 or address[0][1]!=sender or any(c in sender for c in '\r\n" '):raise ValueError('Ungültiger erlaubter Absender.')
        with BoundedIMAP(config.host,993,ssl_context=ssl.create_default_context(),timeout=10) as client:
            client.sock.settimeout(10);client.login(config.user,config.password)
            if client.select('INBOX',readonly=False)[0]!='OK':raise imaplib.IMAP4.error('select')
            status,rows=client.uid('search',None,'(UNSEEN)')
            if status!='OK':raise imaplib.IMAP4.error('search')
            ids=sorted(int(value) for value in (rows[0] or b'').split())
            ordered=[uid for uid in ids if uid>self.cursor]+[uid for uid in ids if uid<=self.cursor]
            for uid in ordered[:20]:
                if count>=5 or time.monotonic()>deadline:break
                self.cursor=uid
                try:
                    client.literal_limit=65536
                    status,rows=client.uid('fetch',str(uid),'(RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM)])')
                    if status!='OK':continue
                    info,headers=fetch_pair(rows);size=re.search(rb'RFC822.SIZE (\d+)',info)
                    if not size or int(size[1])>MAX_MAIL_BYTES or len(headers)>65536:raise ValidationError('E-Mail zu groß.')
                    header=BytesHeaderParser(policy=policy.default).parsebytes(headers)
                    sender=next((sender for sender in allowed if allowed_sender(header,sender)),None)
                    if sender is None:continue
                    client.literal_limit=MAX_MAIL_BYTES
                    status,rows=client.uid('fetch',str(uid),f'(BODY.PEEK[]<0.{MAX_MAIL_BYTES+1}>)')
                    if status!='OK':continue
                    doc=import_message(owner,replace(config,sender=sender),fetch_pair(rows)[1])
                    if doc is None:continue
                    # BODY.PEEK never marks a message read. Do so only after
                    # receive_capture committed the document, files and job.
                    if client.uid('store',str(uid),'+FLAGS.SILENT',r'(\Seen)')[0]!='OK':raise imaplib.IMAP4.error('store')
                    count+=1
                except (ValidationError,OSError):
                    logger.warning('IMAP-Nachricht konnte nicht vollständig übernommen werden (Format/Größe/Datei). Sie bleibt ungelesen.')
        if count:logger.info('IMAP: %s Nachricht(en) sicher übernommen.',count)
        return count
