"""Server-only IMAP configuration; never include a credential in repr/log output."""
import os
from dataclasses import dataclass,field
from django.conf import settings


@dataclass(frozen=True)
class MailConfig:
    host: str
    user: str
    password: str=field(repr=False)
    sender: str='sender@example.invalid'
    capture_user: str=''


def mail_config():
    keys=('MAIL_IMAP_HOST','MAIL_IMAP_USER','MAIL_IMAP_PASSWORD','MAIL_ALLOWED_SENDER','MAIL_CAPTURE_USER')
    values={}
    for filename in (settings.BASE_DIR/'.env',settings.DATA_DIR/'.env'):
        if not filename.is_file(): continue
        for line in filename.read_text(encoding='utf8').splitlines():
            if not line.strip() or line.lstrip().startswith('#') or '=' not in line: continue
            key,value=line.split('=',1);key=key.strip()
            if key not in keys: continue
            value=value.strip()
            if len(value)>1 and value[0]==value[-1] and value[0] in ('"',"'"): value=value[1:-1]
            values[key]=value
    values.update({key:os.environ[key] for key in keys if key in os.environ})
    if not any(values.get(key) for key in keys[:3]): return None
    if not all(values.get(key) for key in keys[:4]): raise ValueError('IMAP-Konfiguration unvollständig.')
    return MailConfig(values['MAIL_IMAP_HOST'],values['MAIL_IMAP_USER'],values['MAIL_IMAP_PASSWORD'],values['MAIL_ALLOWED_SENDER'],values.get('MAIL_CAPTURE_USER',''))
