"""Indexed substring search; short terms and unsupported SQLite use the old query."""
from django.db import connections
from django.db.models.expressions import RawSQL
from django.db.models import UUIDField


def indexed_ids(queryset,kind,term):
    if len(term)<3: return None
    connection=connections[queryset.db]
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM sqlite_master WHERE name='akte_search' AND type='table'")
        if not cursor.fetchone(): return None
    phrase='"'+term.replace('"','""')+'"'
    return RawSQL('SELECT entity_id FROM akte_search WHERE kind=%s AND body MATCH %s',
                  [kind,phrase],output_field=UUIDField())
