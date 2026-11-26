"""Explicit public-demo tenant boundary; never switch global settings per request."""
from contextvars import ContextVar
from contextlib import contextmanager
from django.core.exceptions import PermissionDenied
from django.db import connections

_active = ContextVar('public_demo', default=False)

def active():
    return _active.get()

def deny_live_operation():
    if active():
        raise PermissionDenied('Diese Funktion ist in der Demo gesperrt.')

class DemoRouter:
    def db_for_read(self, model, **hints):
        return 'demo' if active() else None
    db_for_write = db_for_read

    def allow_relation(self, first, second, **hints):
        if 'demo' in (first._state.db, second._state.db):
            return first._state.db == second._state.db
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        return True if db == 'demo' else None

@contextmanager
def demo_scope():
    def deny_default(execute, sql, params, many, context):
        raise PermissionDenied('Die Demo darf nicht auf die Live-Datenbank zugreifen.')
    token = _active.set(True)
    try:
        with connections['default'].execute_wrapper(deny_default):
            yield
    finally:
        _active.reset(token)
