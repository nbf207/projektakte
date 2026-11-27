"""Regression tests for v1.0.2: non-blocking backups, progress UI, crash cleanup, and middleware."""
import json
import threading
import time
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .backup import (
    STORED_EXTENSIONS,
    backup,
    cleanup_stale_backups,
    latest_backup_progress,
    read_backup_progress,
    write_backup_progress,
)
from .control_tasks import create_backup, schedule_control
from .middleware import MaintenanceMiddleware
from .models import ControlTask, Document, Job, Preferences, Project, SessionDevice
from .operations import OperationBusy, backup_gate, operation_gate
from .worker import reserve

User = get_user_model()


class Release102BackupTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='admin102', password='SafePassword123456!')
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save(update_fields=['is_superuser', 'is_staff'])
        Preferences.objects.get_or_create(owner=self.user)

    def test_compressed_media_stored_and_text_deflated(self):
        """Already-compressed media use ZIP_STORED; DB/JSON/text use ZIP_DEFLATED."""
        for ext in ('.jpg', '.png', '.webp', '.heic', '.mp4', '.mov', '.zip', '.gz'):
            self.assertIn(ext, STORED_EXTENSIONS)

        media_dir = settings.MEDIA_ROOT / 'test102'
        media_dir.mkdir(parents=True, exist_ok=True)
        samples = {
            'photo.heic': b'HEICDATA' * 200,
            'clip.mov': b'MOVDATA' * 200,
            'pic.jpg': b'JPGDATA' * 200,
            'note.txt': b'PLAIN TEXT ' * 200,
        }
        try:
            for name, content in samples.items():
                (media_dir / name).write_bytes(content)
            dest = settings.DATA_DIR / 'backups' / f'test-stored-{uuid.uuid4().hex}.zip'
            try:
                backup(dest)
                with zipfile.ZipFile(dest) as z:
                    self.assertEqual(z.getinfo('media/test102/photo.heic').compress_type, zipfile.ZIP_STORED)
                    self.assertEqual(z.getinfo('media/test102/clip.mov').compress_type, zipfile.ZIP_STORED)
                    self.assertEqual(z.getinfo('media/test102/pic.jpg').compress_type, zipfile.ZIP_STORED)
                    self.assertEqual(z.getinfo('media/test102/note.txt').compress_type, zipfile.ZIP_DEFLATED)
                    self.assertEqual(z.getinfo('db.sqlite3').compress_type, zipfile.ZIP_DEFLATED)
            finally:
                dest.unlink(missing_ok=True)
        finally:
            for name in samples:
                (media_dir / name).unlink(missing_ok=True)
            try:
                media_dir.rmdir()
            except OSError:
                pass

    def test_operation_gate_free_during_archive_and_verify(self):
        """operation_gate must be released before Phase B (archive & verify) so web writes never stall."""
        gate_free_in = {}
        backup_lock_held_in = {}

        def on_progress(phase, bd, bt, fd, ft):
            if phase in ('archive', 'verify') and phase not in gate_free_in:
                result = {'op_free': False, 'bk_busy': False}

                def probe():
                    try:
                        with operation_gate(timeout=0):
                            result['op_free'] = True
                    except OperationBusy:
                        result['op_free'] = False
                    try:
                        with backup_gate(timeout=0):
                            result['bk_busy'] = False
                    except OperationBusy:
                        result['bk_busy'] = True

                t = threading.Thread(target=probe)
                t.start()
                t.join(timeout=5)
                gate_free_in[phase] = result['op_free']
                backup_lock_held_in[phase] = result['bk_busy']

        dest = settings.DATA_DIR / 'backups' / f'test-lock-{uuid.uuid4().hex}.zip'
        try:
            backup(dest, progress=on_progress)
            self.assertTrue(gate_free_in.get('archive'), 'operation_gate should be free during archive phase')
            self.assertTrue(gate_free_in.get('verify'), 'operation_gate should be free during verify phase')
            self.assertTrue(backup_lock_held_in.get('archive'), '.backup.lock should be held during archive phase')
            self.assertTrue(backup_lock_held_in.get('verify'), '.backup.lock should be held during verify phase')
            # Staging dir must be cleaned up after backup.
            leftover = [p for p in (settings.DATA_DIR / 'backups').iterdir() if p.name.startswith('.staging-')]
            self.assertEqual(leftover, [])
        finally:
            dest.unlink(missing_ok=True)

    def test_cleanup_stale_backups_removes_staging_and_marks_aborted(self):
        """Worker startup cleanup removes leftover .staging-* dirs and marks stale running progress.json as aborted."""
        backups_dir = settings.DATA_DIR / 'backups'
        backups_dir.mkdir(parents=True, exist_ok=True)
        stale_staging = backups_dir / f'.staging-{uuid.uuid4().hex}'
        stale_staging.mkdir()
        (stale_staging / 'orphan.txt').write_text('x', encoding='utf8')

        stale_run = backups_dir / str(uuid.uuid4())
        write_backup_progress(stale_run, {
            'task_id': stale_run.name,
            'status': 'running',
            'phase': 'archive',
            'percent': 42,
        })
        try:
            cleanup_stale_backups()
            self.assertFalse(stale_staging.exists())
            data = read_backup_progress(stale_run)
            self.assertIsNotNone(data)
            self.assertEqual(data['status'], 'aborted')
            self.assertEqual(data['phase'], 'aborted')
        finally:
            if stale_staging.exists():
                for child in stale_staging.iterdir():
                    child.unlink(missing_ok=True)
                stale_staging.rmdir()
            for child in stale_run.iterdir():
                child.unlink(missing_ok=True)
            stale_run.rmdir()

    def test_device_middleware_non_blocking_when_gate_busy(self):
        """DeviceMiddleware must not block or 500 when operation_gate is held by another thread."""
        client = Client()
        self.assertTrue(client.login(username='admin102', password='SafePassword123456!'))
        # Make last_seen stale (> 1 minute old) so DeviceMiddleware attempts an update.
        SessionDevice.objects.filter(owner=self.user).update(last_seen=timezone.now() - timedelta(minutes=5))

        gate_held = threading.Event()
        release_gate = threading.Event()

        def hold_gate():
            with operation_gate(timeout=5):
                gate_held.set()
                release_gate.wait(timeout=5)

        holder = threading.Thread(target=hold_gate)
        holder.start()
        self.assertTrue(gate_held.wait(timeout=3))
        try:
            start = time.monotonic()
            resp = client.get(reverse('settings_status') + '?monitor=1')
            elapsed = time.monotonic() - start
            self.assertEqual(resp.status_code, 200)
            self.assertLess(elapsed, 2.0, 'DeviceMiddleware must skip last_seen non-blockingly')
        finally:
            release_gate.set()
            holder.join(timeout=5)

    def test_maintenance_middleware_returns_503_on_operation_busy_get(self):
        """Any OperationBusy during GET returns clean HTTP 503 (and JSON when requested), never 500."""
        factory = RequestFactory()

        def raising_view(request):
            raise OperationBusy('Busy')

        mw = MaintenanceMiddleware(raising_view)
        req_html = factory.get('/')
        resp_html = mw(req_html)
        self.assertEqual(resp_html.status_code, 503)
        self.assertEqual(resp_html['Retry-After'], '5')

        req_json = factory.get('/einstellungen/status/', HTTP_ACCEPT='application/json')
        resp_json = mw(req_json)
        self.assertEqual(resp_json.status_code, 503)
        self.assertEqual(json.loads(resp_json.content)['error'][:9], 'Sicherung')

    def test_worker_reserve_separates_backup_and_regular_slots(self):
        """A running backup job must not block reserving regular jobs."""
        backup_task = schedule_control(self.user, 'backup', uuid.uuid4())
        bjob = reserve(only_backup=True)
        self.assertIsNotNone(bjob)
        self.assertEqual(bjob.control_id, backup_task.pk)
        # Second backup slot must not be reserved while first backup is running.
        self.assertIsNone(reserve(only_backup=True))

        # Regular job can still be reserved concurrently while backup is running!
        other_task = schedule_control(self.user, 'backup_discover', uuid.uuid4())
        regular = reserve(allow_backup=False)
        self.assertIsNotNone(regular)
        self.assertEqual(regular.control_id, other_task.pk)

    def test_backup_progress_ui_and_status_endpoint(self):
        """Settings system page renders native <progress> and status API exposes backup_progress."""
        client = Client()
        self.assertTrue(client.login(username='admin102', password='SafePassword123456!'))
        run_dir = settings.DATA_DIR / 'backups' / str(uuid.uuid4())
        write_backup_progress(run_dir, {
            'task_id': run_dir.name,
            'status': 'running',
            'phase': 'archive',
            'percent': 55,
            'bytes_done': 550_000_000,
            'bytes_total': 1_000_000_000,
            'files_done': 120,
            'files_total': 220,
        })
        try:
            page = client.get(reverse('settings_center', args=['system']))
            self.assertEqual(page.status_code, 200)
            html = page.content.decode('utf8')
            self.assertIn('<progress data-bp-bar', html)
            self.assertIn('data-backup-progress', html)

            status_resp = client.get(reverse('settings_status') + '?monitor=1')
            self.assertEqual(status_resp.status_code, 200)
            payload = status_resp.json()
            self.assertIsNotNone(payload['backup_progress'])
            self.assertEqual(payload['backup_progress']['status'], 'running')
            self.assertEqual(payload['backup_progress']['percent'], 55)
            self.assertEqual(payload['backup_progress']['phase'], 'archive')
        finally:
            for child in run_dir.iterdir():
                child.unlink(missing_ok=True)
            run_dir.rmdir()

    def test_settings_js_binds_lg_navigate(self):
        """settings.js must re-initialize and clean up polling on lg:navigate."""
        js = (settings.BASE_DIR / 'static' / 'settings.js').read_text(encoding='utf8')
        self.assertIn("addEventListener('lg:navigate'", js)
        self.assertIn('clearTimeout', js)
