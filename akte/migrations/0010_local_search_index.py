"""SQLite FTS5 trigram index, maintained by DB triggers including bulk writes.

If SQLite lacks FTS5/trigram the existing bounded LIKE search remains available.
No daemon or memory-resident search service is introduced.
"""
from django.db import migrations

VIEWS = {
    'entry': """SELECT e.id AS entity_id, p.owner_id AS owner_id,
        e.title||' '||e.description||' '||e.tags||' '||e.snapshot||' '||
        COALESCE((SELECT group_concat(s.title||' '||s.text,' ') FROM akte_step s WHERE s.entry_id=e.id),'')||' '||
        COALESCE((SELECT group_concat(f.caption,' ') FROM akte_photolink f WHERE f.entry_id=e.id AND f.deleted_at IS NULL),'')||' '||
        COALESCE((SELECT group_concat(h.snapshot,' ') FROM akte_partevent h WHERE h.entry_id=e.id),'')||' '||
        COALESCE((SELECT group_concat(d.ocr,' ') FROM akte_document d WHERE d.owner_id=p.owner_id AND
            (d.entry_id=e.id OR d.id IN (SELECT l.document_id FROM akte_documentlink l WHERE l.entry_id=e.id)
             OR d.id IN (SELECT c.document_id FROM akte_costsource c JOIN akte_allocation a ON a.source_id=c.id WHERE a.entry_id=e.id))),'') AS body
        FROM akte_entry e JOIN akte_project p ON e.project_id=p.id""",
    'document': "SELECT id AS entity_id, owner_id, ocr||' '||proposal||' '||edits AS body FROM akte_document",
    'part': "SELECT t.id AS entity_id,p.owner_id,t.name||' '||t.number||' '||t.notes AS body FROM akte_part t JOIN akte_project p ON t.project_id=p.id",
}


def install(apps,schema_editor):
    connection=schema_editor.connection
    try:
        connection.cursor().execute("CREATE VIRTUAL TABLE akte_search USING fts5(entity_id UNINDEXED,kind UNINDEXED,owner_id UNINDEXED,body,tokenize='trigram')")
    except Exception as exc:
        # Only unsupported SQLite features are allowed to fall back. Disk/DB
        # errors must still fail the migration rather than look successful.
        if connection.vendor=='sqlite' and ('no such module: fts5' in str(exc).lower() or 'no such tokenizer' in str(exc).lower()): return
        raise
    with connection.cursor() as cursor:
        for kind,select in VIEWS.items():
            cursor.execute(f'CREATE VIEW akte_search_{kind} AS {select}')
            cursor.execute(f"INSERT INTO akte_search SELECT entity_id,'{kind}',owner_id,body FROM akte_search_{kind}")
        # Each trigger rebuilds only affected entity rows. Related OCR is indexed
        # for both direct links and split cost allocations.
        dependencies={
            'entry': {'entry':'SELECT {row}.id'},
            'step': {'entry':'SELECT {row}.entry_id'},
            'photolink': {'entry':'SELECT {row}.entry_id'},
            'partevent': {'entry':'SELECT {row}.entry_id'},
            'documentlink': {'entry':'SELECT {row}.entry_id'},
            'allocation': {'entry':'SELECT {row}.entry_id'},
            'document': {'document':'SELECT {row}.id', 'entry':
                'SELECT {row}.entry_id UNION SELECT entry_id FROM akte_documentlink WHERE document_id={row}.id '
                'UNION SELECT a.entry_id FROM akte_allocation a JOIN akte_costsource c ON a.source_id=c.id WHERE c.document_id={row}.id'},
            'costsource': {'entry':'SELECT entry_id FROM akte_allocation WHERE source_id={row}.id'},
            'part': {'part':'SELECT {row}.id'},
            'project': {'entry':'SELECT id FROM akte_entry WHERE project_id={row}.id',
                        'part':'SELECT id FROM akte_part WHERE project_id={row}.id'},
        }
        for table,targets in dependencies.items():
            for event,rows in [('INSERT',['NEW']),('UPDATE',['OLD','NEW']),('DELETE',['OLD'])]:
                statements=[]
                for kind,query in targets.items():
                    ids=' UNION '.join(query.format(row=row) for row in rows)
                    statements.append(f"DELETE FROM akte_search WHERE kind='{kind}' AND entity_id IN ({ids});")
                    statements.append(f"INSERT INTO akte_search SELECT entity_id,'{kind}',owner_id,body FROM akte_search_{kind} WHERE entity_id IN ({ids});")
                cursor.execute(f"CREATE TRIGGER akte_search_{table}_{event.lower()} AFTER {event} ON akte_{table} BEGIN {' '.join(statements)} END")


def uninstall(apps,schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT name,type FROM sqlite_master WHERE name LIKE 'akte_search%'")
        objects=list(cursor.fetchall())
        for name,kind in objects:
            if kind in ('view','trigger'):
                cursor.execute(f'DROP {kind.upper()} IF EXISTS "{name}"')
        cursor.execute('DROP TABLE IF EXISTS akte_search')


class Migration(migrations.Migration):
    dependencies=[('akte','0009_independent_review_draft_version')]
    operations=[migrations.RunPython(install,uninstall)]
