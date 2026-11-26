"""Isolated, real-process integrity checks. Never points at existing application data."""
import argparse
from contextlib import closing
import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


def setup(data):
    os.environ.update(DATA_DIR=str(data), DEMO_MODE='1', DJANGO_SETTINGS_MODULE='config.settings')
    import django
    django.setup()


def wait_file(file, timeout=30):
    deadline = time.monotonic() + timeout
    while not file.exists():
        if time.monotonic() > deadline:
            raise TimeoutError(str(file))
        time.sleep(.02)


def child(mode, data, ready, start):
    setup(data)
    from django.contrib.auth import get_user_model
    from django.core.files.uploadedfile import SimpleUploadedFile
    from akte.demo import synthetic_pdf
    from akte.models import Asset, Document, PhotoLink, Project
    from akte.operations import operation_gate
    from akte.services import accept_document, receive_document
    owner = get_user_model().objects.get(username='concurrency')
    project = Project.objects.get(owner=owner)
    ready.touch()
    wait_file(start)
    if mode == 'upload':
        doc, new = receive_document(owner, SimpleUploadedFile('same.pdf', (data/'upload.pdf').read_bytes()), project)
        print(json.dumps({'document': str(doc.pk), 'new': new}))
    elif mode == 'accept':
        doc = Document.objects.get(owner=owner)
        source = accept_document(owner, doc, {'gross_total': '75.00', 'currency': 'EUR', 'positions': []},
                                 [{'project': project, 'amount': '75.00'}], expected_version=doc.version)
        print(json.dumps({'source': str(source.pk)}))
    elif mode == 'writer':
        with operation_gate():
            project.name = 'After snapshot'
            project.save(update_fields=['name'])
            for asset in Asset.objects.filter(mime__startswith='image/'):
                PhotoLink.objects.filter(asset=asset).delete()
                original = Path(asset.file.path)
                asset.delete()
                original.unlink()
            (data / 'writer-finished').touch()
        print('{}')


def run_checks(parent):
    from django.contrib.auth import get_user_model
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.core.management import call_command
    from django.utils import timezone
    from akte.backup import backup, restore
    from akte.demo import synthetic_photo
    from akte.exports import portable_export, verify_archive
    from akte.models import Asset, CostSource, Document, Entry, PhotoLink, Project
    from akte.operations import operation_gate
    from akte.services import ingest_asset
    from akte.snapshots import export_snapshot, media_path
    call_command('migrate', verbosity=0)
    owner = get_user_model().objects.create_user('concurrency')
    project = Project.objects.create(owner=owner, name='Before snapshot')
    from akte.demo import synthetic_pdf
    (parent/'upload.pdf').write_bytes(synthetic_pdf('identical'))
    children = []
    def launch(mode, number):
        ready, start = parent / (number+'-ready'), parent / (number+'-start')
        process = subprocess.Popen([sys.executable, __file__, '--child', mode, '--data', str(parent),
                                    '--ready', str(ready), '--start', str(start)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        children.append(process)
        wait_file(ready)
        return process, start
    def finish(process):
        out, err = process.communicate(timeout=40)
        if process.returncode:
            raise AssertionError(err)
        return json.loads(out)
    try:
        a, sa = launch('upload', 'upload-a')
        b, sb = launch('upload', 'upload-b')
        sa.touch(); sb.touch()
        results = [finish(a), finish(b)]
        assert len({x['document'] for x in results}) == 1
        assert sum(x['new'] for x in results) == 1
        assert Asset.objects.count() == Document.objects.count() == 1
        assert len(list((parent/'media/originals').iterdir())) == 1
        doc = Document.objects.get()
        doc.paperless_id = 42; doc.verified_at = timezone.now(); doc.status = 'review'; doc.save()
        a, sa = launch('accept', 'accept-a')
        b, sb = launch('accept', 'accept-b')
        sa.touch(); sb.touch()
        results = [finish(a), finish(b)]
        assert len({x['source'] for x in results}) == 1
        assert CostSource.objects.count() == 1 and project.cost == 75
        entry = Entry.objects.create(project=project, title='Frozen photo', kind='umbau')
        asset = ingest_asset(owner, SimpleUploadedFile('frozen.jpg', synthetic_photo(1)))
        PhotoLink.objects.create(asset=asset, entry=entry)
        with operation_gate():
            writer, start = launch('writer', 'blocked')
            start.touch()
            archive = backup(parent/'consistent-backup.zip')
            assert not (parent/'writer-finished').exists()
            with zipfile.ZipFile(archive) as z:
                assert 'media/'+asset.file.name in z.namelist()
        finish(writer)
        restore(archive, parent/'restore')
        # Restore proves media came from the same pre-writer state.
        assert (parent/'restore/media'/asset.file.name).is_file()
        import sqlite3
        with closing(sqlite3.connect(parent/'restore/db.sqlite3')) as db:
            assert db.execute('SELECT name FROM akte_project').fetchone()[0] == 'Before snapshot'
        # Another writer must be able to complete during rendering, while the
        # export retains its captured name, relationships and unlinked bytes.
        Project.objects.filter(pk=project.pk).update(name='Before export')
        asset = ingest_asset(owner, SimpleUploadedFile('frozen-again.jpg', synthetic_photo(2)))
        PhotoLink.objects.create(asset=asset, entry=entry)
        with export_snapshot(owner):
            writer, start = launch('writer', 'during-export')
            start.touch(); finish(writer)
            assert media_path(asset.file).is_file()
            portable_export(parent/'snapshot-export.zip', owner)
        verify_archive(parent/'snapshot-export.zip')
        with zipfile.ZipFile(parent/'snapshot-export.zip') as z:
            data = json.loads(z.read('daten.json'))
            projects = [x for x in data['objects'] if x['model']=='akte.project']
            assert projects[0]['fields']['name'] == 'Before export'
            assert 'medien/'+asset.file.name in z.namelist()
        return {'identical_uploads': 'passed (2 processes, one asset/document/original)',
                'double_accept': 'passed (2 processes, one source, 75.00 EUR)',
                'backup_write_gate': 'passed (writer blocked until archive complete)',
                'backup_restore': 'passed (pre-writer DB and original match)',
                'export_snapshot': 'passed (writer completed; old DB and unlinked photo preserved)',
                'synthetic_only': True}
    finally:
        for process in children:
            if process.poll() is None:
                process.kill(); process.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--child', choices=['upload', 'accept', 'writer'])
    parser.add_argument('--data', type=Path)
    parser.add_argument('--ready', type=Path)
    parser.add_argument('--start', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.child:
        child(args.child, args.data, args.ready, args.start)
        return
    if args.data:
        parser.error('--data ist ausschließlich für interne Kindprozesse; Hauptprüfung nutzt ein neues temporäres Verzeichnis.')
    with tempfile.TemporaryDirectory(prefix='projektakte-concurrency-') as temporary:
        parent = Path(temporary)
        setup(parent)
        try:
            result = run_checks(parent)
        finally:
            from django.db import connections
            connections.close_all()
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf8')
    print(text)


if __name__ == '__main__':
    main()
