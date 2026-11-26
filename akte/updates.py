"""Startup safety, atomic secret generation, downgrade guard, and SQLite rollback helpers."""
import os
import re
import secrets
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.db import connections
from django.db.migrations.executor import MigrationExecutor

from .operations import operation_gate

PRE_MIGRATE_DIRNAME = 'pre-migrate'
VERSION_FILE = '.app_version'
SECRET_KEY_FILE = '.secret-key'
_SAFE_SNAPSHOT_RE = re.compile(r'^[A-Za-z0-9._-]+\.sqlite3$')


class StartupError(RuntimeError):
    """Raised when startup safety checks abort before serving traffic."""


class DowngradeError(StartupError):
    """Raised when the database appears newer than the running application image."""


def parse_version(text):
    """Parse semantic-like version strings into comparable tuples.

    Examples:
        '0.7.30'    -> ((0, 7, 30), 1, 0)
        '0.10.0'    -> ((0, 10, 0), 1, 0)
        '1.0.0-rc1' -> ((1, 0, 0), 0, 1)
        '1.0.0'     -> ((1, 0, 0), 1, 0)
    """
    raw = (text or '').strip().lstrip('vV')
    if not raw:
        raise ValueError('Empty version string.')
    match = re.match(r'^(\d+(?:\.\d+)*)(?:[-._]?(?:rc|a|alpha|b|beta)(\d*))?$', raw, re.IGNORECASE)
    if not match:
        raise ValueError(f'Invalid version format: {text!r}')
    nums = tuple(int(part) for part in match.group(1).split('.'))
    while len(nums) < 3:
        nums = nums + (0,)
    if match.group(2) is None and '-' not in raw and not re.search(r'[a-zA-Z]', raw):
        return (nums, 1, 0)
    pre_num = int(match.group(2)) if match.group(2) else 0
    return (nums, 0, pre_num)


def atomic_write_text(path, content, mode=0o600):
    """Write text atomically using a sibling temp file + os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    tmp = Path(tmp_name)
    try:
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        with os.fdopen(fd, 'w', encoding='ascii') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    finally:
        tmp.unlink(missing_ok=True)
    return path


def ensure_secret_key(data_dir):
    """Ensure a persistent secret key exists at <data_dir>/.secret-key atomically.

    Never overwrites an existing valid key. Uses operation_gate() when available
    or O_CREAT|O_EXCL semantics via atomic replace guarded by a lockfile.
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    key_path = data_dir / SECRET_KEY_FILE
    if key_path.exists():
        existing = key_path.read_text(encoding='ascii').strip()
        if existing:
            return existing
    lock_path = data_dir / '.secret-key.lock'
    with lock_path.open('a+b') as lock_file:
        if os.name == 'nt':
            import msvcrt
            lock_file.seek(0)
            lock_file.write(b'0')
            lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            if key_path.exists():
                existing = key_path.read_text(encoding='ascii').strip()
                if existing:
                    return existing
            generated = secrets.token_urlsafe(64)
            atomic_write_text(key_path, generated + '\n', mode=0o600)
            return generated
        finally:
            if os.name == 'nt':
                import msvcrt
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def check_sqlite_integrity(db_path):
    """Verify SQLite database integrity; raise StartupError on corruption."""
    db_path = Path(db_path)
    if not db_path.exists():
        return
    try:
        with closing(sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=10)) as conn:
            result = conn.execute('PRAGMA integrity_check').fetchone()
            if not result or result[0] != 'ok':
                raise StartupError(f'SQLite integrity check failed for {db_path.name}: {result}')
    except sqlite3.DatabaseError as exc:
        raise StartupError(f'SQLite database {db_path.name} is unreadable or corrupted: {exc}') from exc


def unknown_applied_migrations(connection):
    """Return sorted (app, name) migrations present in django_migrations but missing from disk."""
    executor = MigrationExecutor(connection)
    disk_keys = set(executor.loader.disk_migrations.keys())
    applied = executor.loader.applied_migrations
    unknown = sorted(key for key in applied if key not in disk_keys)
    return unknown


def check_downgrade(data_dir, current_version, connection=None):
    """Abort if stored version tuple or applied migrations are newer than current code."""
    data_dir = Path(data_dir)
    version_path = data_dir / VERSION_FILE
    if version_path.exists():
        stored_raw = version_path.read_text(encoding='ascii').strip()
        if stored_raw:
            try:
                stored_tuple = parse_version(stored_raw)
                current_tuple = parse_version(current_version)
            except ValueError:
                stored_tuple = None
                current_tuple = None
            if stored_tuple and current_tuple and stored_tuple > current_tuple:
                raise DowngradeError(
                    f'Database was last used by Projektakte {stored_raw}, but this image is {current_version}. '
                    f'Pin image {stored_raw} or restore a pre-migration snapshot with "python manage.py rollback_db".'
                )
    if connection is not None:
        db_name = connection.settings_dict.get('NAME')
        if db_name and str(db_name) != ':memory:' and Path(db_name).exists():
            unknown = unknown_applied_migrations(connection)
            if unknown:
                sample = ', '.join(f'{app}.{name}' for app, name in unknown[:5])
                raise DowngradeError(
                    f'Database contains migrations unknown to Projektakte {current_version} ({sample}). '
                    f'Use the newer image or restore a matching pre-migration snapshot with "python manage.py rollback_db".'
                )


def pending_migrations(connection):
    """Return the list of unapplied migrations for the given database connection."""
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    return executor.migration_plan(targets)


def pre_migrate_dir(data_dir):
    return Path(data_dir) / 'backups' / PRE_MIGRATE_DIRNAME


def create_pre_migrate_snapshot(db_path, data_dir, app_version, keep=5):
    """Create a verified, atomic SQLite snapshot before applying migrations."""
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    target_dir = pre_migrate_dir(data_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    safe_ver = re.sub(r'[^A-Za-z0-9._-]', '_', str(app_version))
    destination = target_dir / f'db-before-v{safe_ver}-{ts}.sqlite3'
    suffix = 1
    while destination.exists():
        destination = target_dir / f'db-before-v{safe_ver}-{ts}-{suffix}.sqlite3'
        suffix += 1
    fd, tmp_name = tempfile.mkstemp(dir=target_dir, prefix='.snapshot-', suffix='.tmp')
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with operation_gate():
            with closing(sqlite3.connect(str(db_path), timeout=20)) as src, closing(sqlite3.connect(str(tmp))) as dst:
                src.backup(dst)
                res = dst.execute('PRAGMA integrity_check').fetchone()
                if not res or res[0] != 'ok':
                    raise StartupError(f'Pre-migration snapshot integrity check failed: {res}')
            os.replace(tmp, destination)
            try:
                os.chmod(destination, 0o600)
            except OSError:
                pass
    finally:
        tmp.unlink(missing_ok=True)

    # Rotate older automatic pre-migration snapshots (keep newest N).
    if keep and keep > 0:
        snaps = sorted(
            (p for p in target_dir.glob('db-before-v*.sqlite3') if p.is_file()),
            key=lambda p: (p.stat().st_mtime, p.name),
            reverse=True,
        )
        for old in snaps[keep:]:
            old.unlink(missing_ok=True)
    return destination


def list_snapshots(data_dir):
    """Return available pre-migration SQLite snapshots, newest first."""
    target_dir = pre_migrate_dir(data_dir)
    if not target_dir.is_dir():
        return []
    return sorted(
        (p for p in target_dir.iterdir() if p.is_file() and _SAFE_SNAPSHOT_RE.match(p.name)),
        key=lambda p: (p.stat().st_mtime, p.name),
        reverse=True,
    )


def restore_snapshot(data_dir, snapshot_name, db_path=None):
    """Verify and atomically restore a pre-migration SQLite snapshot under operation_gate."""
    data_dir = Path(data_dir)
    db_path = Path(db_path) if db_path else (data_dir / 'db.sqlite3')
    if snapshot_name == 'latest':
        snaps = [p for p in list_snapshots(data_dir) if p.name.startswith('db-before-v')]
        if not snaps:
            raise StartupError('No pre-migration snapshots found.')
        source = snaps[0]
    else:
        if not _SAFE_SNAPSHOT_RE.match(snapshot_name or ''):
            raise StartupError(f'Invalid snapshot filename: {snapshot_name!r}')
        source = pre_migrate_dir(data_dir) / snapshot_name
        if not source.is_file():
            raise StartupError(f'Snapshot not found: {snapshot_name}')

    check_sqlite_integrity(source)
    target_dir = pre_migrate_dir(data_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    with operation_gate():
        connections.close_all()
        safety_copy = None
        if db_path.exists():
            ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            safety_copy = target_dir / f'db-before-rollback-{ts}.sqlite3'
            with closing(sqlite3.connect(str(db_path), timeout=20)) as src, closing(sqlite3.connect(str(safety_copy))) as dst:
                src.backup(dst)
            try:
                os.chmod(safety_copy, 0o600)
            except OSError:
                pass

        fd, tmp_name = tempfile.mkstemp(dir=db_path.parent, prefix='.restore-', suffix='.tmp')
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            with closing(sqlite3.connect(str(source), timeout=20)) as src, closing(sqlite3.connect(str(tmp))) as dst:
                src.backup(dst)
                res = dst.execute('PRAGMA integrity_check').fetchone()
                if not res or res[0] != 'ok':
                    raise StartupError(f'Restored database failed integrity check: {res}')
            for sidecar in (Path(str(db_path) + '-wal'), Path(str(db_path) + '-shm')):
                sidecar.unlink(missing_ok=True)
            os.replace(tmp, db_path)
            try:
                os.chmod(db_path, 0o600)
            except OSError:
                pass
        finally:
            tmp.unlink(missing_ok=True)

        # Clear version marker so the older target image can start cleanly.
        (data_dir / VERSION_FILE).unlink(missing_ok=True)
        return source, safety_copy


def write_app_version(data_dir, app_version):
    return atomic_write_text(Path(data_dir) / VERSION_FILE, str(app_version).strip() + '\n', mode=0o644)


def prepare_startup(data_dir=None, app_version=None, auto_migrate=None, keep=5, connection_alias='default'):
    """Run startup safety checks, optional pre-migration snapshot, and auto-migrations."""
    data_dir = Path(data_dir or settings.DATA_DIR)
    app_version = app_version or settings.APP_VERSION
    if auto_migrate is None:
        auto_migrate = os.environ.get('AUTO_MIGRATE', '1') != '0'

    conn = connections[connection_alias]
    db_name = conn.settings_dict.get('NAME')
    db_path = Path(db_name) if db_name and str(db_name) != ':memory:' else None

    if db_path and db_path.exists():
        check_sqlite_integrity(db_path)
    check_downgrade(data_dir, app_version, connection=conn)

    snapshot = None
    if auto_migrate:
        plan = pending_migrations(conn)
        if plan:
            if db_path and db_path.exists():
                snapshot = create_pre_migrate_snapshot(db_path, data_dir, app_version, keep=keep)
            call_command('migrate', database=connection_alias, interactive=False, verbosity=1)

    write_app_version(data_dir, app_version)
    return snapshot
