"""Consistent app backup under the shared OS write gate; external storage is separate."""
import hashlib,json,os,shutil,sqlite3,tempfile,zipfile,tarfile
from pathlib import Path
from contextlib import closing
from django.conf import settings
from django.utils import timezone
from .exports import verify_archive
from .operations import guarded
from .storage import durable_replace

def validate_bundle(path):
    path=Path(path)
    if not path.is_file(): raise ValueError('Paperless-Sicherungsarchiv fehlt.')
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            if z.testzip(): raise ValueError('Paperless-Bundle ist beschädigt.')
    elif tarfile.is_tarfile(path):
        with tarfile.open(path,'r:*') as tar:
            for member in tar:
                if member.isfile():
                    f=tar.extractfile(member)
                    if f:
                        with f:
                            while f.read(1024*1024): pass
    else: raise ValueError('Paperless-Bundle muss ein intaktes ZIP- oder TAR-Archiv sein.')

@guarded
def backup(destination,paperless_bundle=None,*,complete_control=None):
    if paperless_bundle: validate_bundle(paperless_bundle)
    destination=Path(destination).resolve()
    if destination.exists(): raise ValueError('Zieldatei existiert bereits; kein Überschreiben.')
    destination.parent.mkdir(parents=True,exist_ok=True)
    tmp=destination.with_suffix('.tmp')
    if tmp.exists(): raise ValueError('Temporäre Sicherungsdatei vorhanden; zuerst prüfen.')
    with tempfile.TemporaryDirectory(dir=settings.DATA_DIR,prefix='backup-') as folder:
        db=Path(folder)/'db.sqlite3'
        with closing(sqlite3.connect(str(settings.DATA_DIR/'db.sqlite3'))) as src,closing(sqlite3.connect(str(db))) as dst:
            src.backup(dst)
            # A restored manager backup must not repeat its own long-running job.
            if complete_control is not None:
                columns={row[1] for row in dst.execute('PRAGMA table_info(akte_job)')}
                if 'control_id' in columns:
                    dst.execute("UPDATE akte_job SET status='done',lease_token=NULL,leased_until=NULL WHERE control_id=?",(complete_control.hex,))
                    dst.commit()
            if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('Datenbankintegrität fehlgeschlagen.')
        checks={}
        try:
            stored=frozenset(('.jpg','.jpeg','.png','.webp','.gif','.pdf','.mp4','.mp3','.zip','.gz','.webm','.ogg','.m4a','.flac'))
            with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as z:
                def add(file,name):
                    compression=zipfile.ZIP_STORED if Path(file).suffix.lower() in stored else zipfile.ZIP_DEFLATED
                    z.write(file,name,compress_type=compression)
                    with open(file,'rb') as f: checks[name]=hashlib.file_digest(f,'sha256').hexdigest()
                add(db,'db.sqlite3')
                for file in settings.MEDIA_ROOT.rglob('*'):
                    if file.is_file() and not file.is_symlink() and file.relative_to(settings.MEDIA_ROOT).parts[0]!='incoming' and file.suffix!='.tmp': add(file,'media/'+file.relative_to(settings.MEDIA_ROOT).as_posix())
                feedback=settings.DATA_DIR/'feedback'
                if feedback.exists():
                    for file in feedback.glob('*.json'):
                        if file.is_file() and not file.is_symlink():add(file,'feedback/'+file.name)
                    images=feedback/'images'
                    if images.is_dir() and not images.is_symlink():
                        for file in images.iterdir():
                            if file.is_file() and not file.is_symlink() and file.suffix in ('.jpg','.png','.webp'):add(file,'feedback/images/'+file.name)
                # Environment secrets are never guessed or silently copied into portable exports.
                key=settings.DATA_DIR/'.demo-key'
                if settings.DEMO_MODE and key.exists(): add(key,'.demo-key')
                if paperless_bundle:
                    bundle=Path(paperless_bundle).resolve()
                    if not bundle.is_file(): raise ValueError('Paperless-Sicherungsarchiv fehlt.')
                    add(bundle,'paperless/'+bundle.name)
                metadata={'format':'projektakte-backup','schema_version':1,'app_version':settings.APP_VERSION,'created':timezone.now().isoformat(),'demo':settings.DEMO_MODE,'paperless_bundle':bool(paperless_bundle),'consistency':'application-write-gate', 'paperless_consistency':'external-bundle-not-coordinated-by-app', 'note':'App-Schreibvorgänge technisch gesperrt. Live-Konfiguration separat verschlüsselt sichern; Paperless-Bundle unabhängig zeitlich koordinieren und nach eigenem offiziellen Verfahren rücksichern.'}
                raw=json.dumps(metadata,indent=2).encode();z.writestr('backup.json',raw);checks['backup.json']=hashlib.sha256(raw).hexdigest()
                z.writestr('manifest.json',json.dumps({'sha256':checks,'schema_version':1},indent=2))
            verify_archive(tmp,backup=True);durable_replace(tmp,destination)
            try: destination.chmod(0o600)
            except OSError: pass
        finally: tmp.unlink(missing_ok=True)
    return destination

def restore(archive,target):
    target=Path(target).resolve()
    if target.exists() and any(target.iterdir()): raise ValueError('Restore nur in ein leeres getrenntes Verzeichnis erlaubt.')
    verify_archive(archive,backup=True)
    with zipfile.ZipFile(archive) as z:
        meta=json.loads(z.read('backup.json'))
        if meta.get('format')!='projektakte-backup' or meta.get('schema_version')!=1: raise ValueError('Unbekanntes Sicherungsformat.')
        target.parent.mkdir(parents=True,exist_ok=True)
        staging=Path(tempfile.mkdtemp(dir=target.parent,prefix='restore-'))
        try:
            z.extractall(staging)  # Every member was path-checked before extraction.
            with closing(sqlite3.connect(str(staging/'db.sqlite3'))) as db:
                if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('Wiederhergestellte DB ist beschädigt.')
            if target.exists(): target.rmdir()  # Empty target only, checked above.
            os.replace(staging,target)
        finally:
            if staging.exists(): shutil.rmtree(staging)
    return target
