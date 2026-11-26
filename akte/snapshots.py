"""Short write pause to capture DB + immutable media, then read-only rendering.

Hard links preserve originals even when a later cleanup unlinks the live path.
Thumbnails are replaced atomically too. Copy fallback supports filesystems without
hard links. No service, broker or second Django process is needed.
"""
import contextvars
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

from django.conf import settings
from django.db import DatabaseError, connection, connections

from .models import Asset
from .operations import operation_gate

_active = contextvars.ContextVar('export_snapshot', default=None)


class SnapshotRouter:
    def db_for_read(self, model, **hints):
        active = _active.get()
        return active[0] if active else None

    def db_for_write(self, model, **hints):
        if _active.get():
            raise DatabaseError('Export-Momentaufnahmen sind schreibgeschützt.')
        return None

    def allow_relation(self, obj1, obj2, **hints):
        if _active.get():
            return True
        return None


def media_path(field):
    active = _active.get()
    return active[1] / field.name if active else Path(field.path)


@contextmanager
def export_snapshot(owner):
    if _active.get():
        yield
        return
    if connection.in_atomic_block:
        # A caller already holds a transaction (e.g. Django TestCase). Taking a
        # second SQLite backup connection here could deadlock. Retain the gate
        # until its read finishes. Production export jobs have no outer atomic.
        with operation_gate():
            yield
        return
    with tempfile.TemporaryDirectory(dir=settings.DATA_DIR, prefix='export-snapshot-') as temporary:
        folder = Path(temporary)
        media = folder / 'media'
        database = folder / 'db.sqlite3'
        alias = 'snapshot_' + uuid.uuid4().hex
        with operation_gate():
            connection.ensure_connection()
            destination = sqlite3.connect(database)
            try:
                connection.connection.backup(destination)
                if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Export-Momentaufnahme beschädigt.')
            finally:
                destination.close()
            # Missing unrelated files need not prevent an excerpt. Rendering
            # checks that every file actually selected for export exists.
            for asset in Asset.objects.filter(owner=owner).iterator(chunk_size=200):
                for field in (asset.file, asset.thumbnail):
                    if not field or not Path(field.path).is_file():
                        continue
                    target = media / field.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        target.hardlink_to(field.path)
                    except OSError:
                        shutil.copy2(field.path, target)
        configuration = connections.databases['default'].copy()
        configuration['NAME'] = database
        configuration['OPTIONS'] = {'timeout': 20}
        connections.databases[alias] = configuration
        token = _active.set((alias, media))
        try:
            # Defense in depth: raw SQL cannot mutate the snapshot either.
            with connections[alias].cursor() as cursor:
                cursor.execute('PRAGMA query_only=ON')
            yield
        finally:
            _active.reset(token)
            connections[alias].close()
            del connections[alias]
            connections.databases.pop(alias, None)


def consistent_export(function):
    @wraps(function)
    def wrapped(destination, owner, *args, **kwargs):
        with export_snapshot(owner):
            return function(destination, owner, *args, **kwargs)
    return wrapped
