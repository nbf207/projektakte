"""Consistent app backup using a short write-gate snapshot and dedicated backup lock."""
import hashlib,json,os,shutil,sqlite3,tempfile,uuid,zipfile,tarfile
from pathlib import Path
from contextlib import closing
from django.conf import settings
from django.utils import timezone
from .exports import verify_archive
from .operations import OperationBusy, backup_gate, operation_gate
from .storage import durable_replace

STORED_EXTENSIONS = frozenset((
    '.jpg', '.jpeg', '.png', '.webp', '.gif', '.heic', '.heif',
    '.pdf', '.mp4', '.mov', '.mp3', '.zip', '.gz', '.webm', '.ogg', '.m4a', '.flac',
))
PROGRESS_FILE = 'progress.json'


def write_backup_progress(folder, payload):
    folder = Path(folder)
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    target = folder / PROGRESS_FILE
    temporary = folder / (PROGRESS_FILE + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf8') as stream:
            json.dump(payload, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        durable_replace(temporary, target)
        try:
            target.chmod(0o600)
        except OSError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def read_backup_progress(folder):
    path = Path(folder) / PROGRESS_FILE
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
            return None
        data = json.loads(path.read_text(encoding='utf8'))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def latest_backup_progress():
    root = settings.DATA_DIR / 'backups'
    if not root.is_dir():
        return None
    best = None
    best_mtime = -1.0
    for path in root.glob('*/' + PROGRESS_FILE):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            mtime = path.stat().st_mtime
            data = read_backup_progress(path.parent)
            if not data:
                continue
            if data.get('status') == 'running':
                return data
            if mtime > best_mtime:
                best_mtime = mtime
                best = data
        except OSError:
            continue
    return best


def cleanup_stale_backups():
    """Remove leftover .staging-* dirs and mark orphaned running progress files as aborted."""
    try:
        with backup_gate(timeout=0):
            backups_dir = settings.DATA_DIR / 'backups'
            for parent, prefix in ((backups_dir, '.staging-'), (settings.DATA_DIR, 'backup-')):
                if not parent.is_dir():
                    continue
                for entry in parent.iterdir():
                    if entry.name.startswith(prefix) and entry.is_dir() and not entry.is_symlink():
                        shutil.rmtree(entry, ignore_errors=True)
            if backups_dir.is_dir():
                for path in backups_dir.glob('*/' + PROGRESS_FILE):
                    data = read_backup_progress(path.parent)
                    if data and data.get('status') == 'running':
                        data['status'] = 'aborted'
                        data['phase'] = 'aborted'
                        data['updated_at'] = timezone.now().isoformat()
                        try:
                            write_backup_progress(path.parent, data)
                        except OSError:
                            pass
    except OperationBusy:
        # A live backup or restore currently holds .backup.lock; never touch its staging dir.
        pass


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


def _stage_file(src, staged_root, arcname):
    """Try zero-copy hardlink inside staging dir; fall back to direct source path (never copy under lock)."""
    target = staged_root / arcname
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.link(src, target)
        return target
    except OSError:
        return src


def backup(destination, paperless_bundle=None, *, complete_control=None, progress=None):
    with backup_gate(timeout=30):
        if paperless_bundle:
            validate_bundle(paperless_bundle)
        destination = Path(destination).resolve()
        if destination.exists():
            raise ValueError('Zieldatei existiert bereits; kein Überschreiben.')
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_suffix('.tmp')
        if tmp.exists():
            raise ValueError('Temporäre Sicherungsdatei vorhanden; zuerst prüfen.')

        staging_parent = settings.DATA_DIR / 'backups'
        staging_parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        staging = Path(tempfile.mkdtemp(dir=staging_parent, prefix='.staging-'))
        staged_files = []  # list of (source_path, arcname, size)
        try:
            # Phase A: Short exclusive write gate (< 2s) for consistent SQLite snapshot + hardlink staging.
            if progress:
                progress('snapshot', 0, 0, 0, 0)
            with operation_gate():
                db = staging / 'db.sqlite3'
                with closing(sqlite3.connect(str(settings.DATA_DIR / 'db.sqlite3'))) as src, closing(sqlite3.connect(str(db))) as dst:
                    src.backup(dst)
                    # A restored manager backup must not repeat its own long-running job.
                    if complete_control is not None:
                        columns = {row[1] for row in dst.execute('PRAGMA table_info(akte_job)')}
                        if 'control_id' in columns:
                            dst.execute("UPDATE akte_job SET status='done',lease_token=NULL,leased_until=NULL WHERE control_id=?", (complete_control.hex,))
                            dst.commit()
                    if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise ValueError('Datenbankintegrität fehlgeschlagen.')
                staged_files.append((db, 'db.sqlite3', db.stat().st_size))

                for file in settings.MEDIA_ROOT.rglob('*'):
                    try:
                        if file.is_file() and not file.is_symlink() and file.relative_to(settings.MEDIA_ROOT).parts[0] != 'incoming' and file.suffix != '.tmp':
                            arcname = 'media/' + file.relative_to(settings.MEDIA_ROOT).as_posix()
                            source_ref = _stage_file(file, staging, arcname)
                            staged_files.append((source_ref, arcname, source_ref.stat().st_size))
                    except OSError:
                        continue

                feedback = settings.DATA_DIR / 'feedback'
                if feedback.exists():
                    for file in feedback.glob('*.json'):
                        if file.is_file() and not file.is_symlink():
                            arcname = 'feedback/' + file.name
                            source_ref = _stage_file(file, staging, arcname)
                            staged_files.append((source_ref, arcname, source_ref.stat().st_size))
                    images = feedback / 'images'
                    if images.is_dir() and not images.is_symlink():
                        for file in images.iterdir():
                            if file.is_file() and not file.is_symlink() and file.suffix in ('.jpg', '.png', '.webp'):
                                arcname = 'feedback/images/' + file.name
                                source_ref = _stage_file(file, staging, arcname)
                                staged_files.append((source_ref, arcname, source_ref.stat().st_size))

                # Environment secrets are never guessed or silently copied into portable exports.
                key = settings.DATA_DIR / '.demo-key'
                if settings.DEMO_MODE and key.exists():
                    source_ref = _stage_file(key, staging, '.demo-key')
                    staged_files.append((source_ref, '.demo-key', source_ref.stat().st_size))

            # Phase B: Heavy archiving & verification OUTSIDE operation_gate (app stays 100% responsive).
            if paperless_bundle:
                bundle = Path(paperless_bundle).resolve()
                if not bundle.is_file():
                    raise ValueError('Paperless-Sicherungsarchiv fehlt.')
                staged_files.append((bundle, 'paperless/' + bundle.name, bundle.stat().st_size))

            total_bytes = sum(size for _, _, size in staged_files)
            total_files = len(staged_files)
            done_bytes = 0
            checks = {}

            if progress:
                progress('archive', 0, total_bytes, 0, total_files)

            with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as z:
                for idx, (file, name, size) in enumerate(staged_files, 1):
                    if not Path(file).is_file():
                        continue
                    compression = zipfile.ZIP_STORED if Path(file).suffix.lower() in STORED_EXTENSIONS else zipfile.ZIP_DEFLATED
                    z.write(file, name, compress_type=compression)
                    with open(file, 'rb') as f:
                        checks[name] = hashlib.file_digest(f, 'sha256').hexdigest()
                    done_bytes += size
                    if progress:
                        progress('archive', done_bytes, total_bytes, idx, total_files)

                metadata = {
                    'format': 'projektakte-backup',
                    'schema_version': 1,
                    'app_version': settings.APP_VERSION,
                    'created': timezone.now().isoformat(),
                    'demo': settings.DEMO_MODE,
                    'paperless_bundle': bool(paperless_bundle),
                    'consistency': 'snapshot-write-gate',
                    'paperless_consistency': 'external-bundle-not-coordinated-by-app',
                    'note': 'App-Schreibvorgänge während des Snapshots technisch gesperrt. Live-Konfiguration separat verschlüsselt sichern; Paperless-Bundle unabhängig zeitlich koordinieren und nach eigenem offiziellen Verfahren rücksichern.',
                }
                raw = json.dumps(metadata, indent=2).encode()
                z.writestr('backup.json', raw)
                checks['backup.json'] = hashlib.sha256(raw).hexdigest()
                z.writestr('manifest.json', json.dumps({'sha256': checks, 'schema_version': 1}, indent=2))

            if progress:
                progress('verify', 0, total_bytes, 0, total_files)
            verify_archive(
                tmp,
                backup=True,
                on_progress=(lambda bd, bt, fd, ft: progress('verify', bd, bt, fd, ft)) if progress else None,
            )
            durable_replace(tmp, destination)
            try:
                destination.chmod(0o600)
            except OSError:
                pass
        finally:
            tmp.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)
        return destination


def restore(archive, target):
    with backup_gate(timeout=5):
        target = Path(target).resolve()
        if target.exists() and any(target.iterdir()):
            raise ValueError('Restore nur in ein leeres getrenntes Verzeichnis erlaubt.')
        verify_archive(archive, backup=True)
        with zipfile.ZipFile(archive) as z:
            meta = json.loads(z.read('backup.json'))
            if meta.get('format') != 'projektakte-backup' or meta.get('schema_version') != 1:
                raise ValueError('Unbekanntes Sicherungsformat.')
            target.parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(dir=target.parent, prefix='restore-'))
            try:
                z.extractall(staging)  # Every member was path-checked before extraction.
                with closing(sqlite3.connect(str(staging / 'db.sqlite3'))) as db:
                    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise ValueError('Wiederhergestellte DB ist beschädigt.')
                if target.exists():
                    target.rmdir()  # Empty target only, checked above.
                os.replace(staging, target)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        return target
