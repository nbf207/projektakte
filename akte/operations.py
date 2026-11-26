"""Process-safe, reentrant gate for application mutations and consistent archives.

All bundled write entry points acquire this gate BEFORE opening a DB transaction.
SQLite still enforces database transactions. The additional OS lock covers media
renames/deletions too and is released automatically if a process dies.
"""
import os
import threading
import time
from contextlib import contextmanager
from functools import wraps

from django.conf import settings


class OperationBusy(TimeoutError):
    pass


_mutexes = {}
_registry_lock = threading.Lock()
_local = threading.local()


@contextmanager
def operation_gate(timeout=30):
    key = str(settings.DATA_DIR.resolve())
    with _registry_lock:
        mutex = _mutexes.setdefault(key, threading.RLock())
    if not mutex.acquire(timeout=timeout):
        raise OperationBusy('Sicherung oder anderer Schreibvorgang läuft. Bitte erneut versuchen.')
    held = getattr(_local, 'held', {})
    _local.held = held
    handle = None
    try:
        if key in held:
            yield
            return
        lock_path = settings.DATA_DIR / '.operations.lock'
        handle = lock_path.open('a+b')
        if lock_path.stat().st_size == 0:
            handle.write(b'0')
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise OperationBusy('Sicherung oder anderer Schreibvorgang läuft. Bitte erneut versuchen.')
                time.sleep(.05)
        held[key] = True
        try:
            yield
        finally:
            held.pop(key, None)
            if os.name == 'nt':
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        if handle is not None:
            handle.close()
        mutex.release()


def guarded(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with operation_gate():
            return function(*args, **kwargs)
    return wrapped
