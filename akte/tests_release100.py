"""Tests for v1.0.0 readiness: startup safety, atomic secret key, downgrade guard, rollback_db, /setup/, and standalone backup."""
import sqlite3
import tempfile
from contextlib import closing
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings

from akte.updates import (
    DowngradeError,
    StartupError,
    VERSION_FILE,
    check_downgrade,
    create_pre_migrate_snapshot,
    ensure_secret_key,
    list_snapshots,
    parse_version,
    prepare_startup,
    restore_snapshot,
    write_app_version,
)


class VersionAndSecretKeyTests(TestCase):
    def test_parse_version_tuple_ordering(self):
        self.assertLess(parse_version('0.7.30'), parse_version('0.10.0'))
        self.assertLess(parse_version('0.10.0'), parse_version('1.0.0-rc1'))
        self.assertLess(parse_version('1.0.0-rc1'), parse_version('1.0.0-rc2'))
        self.assertLess(parse_version('1.0.0-rc2'), parse_version('1.0.0'))
        self.assertEqual(parse_version('v1.0.0'), parse_version('1.0.0'))
        with self.assertRaises(ValueError):
            parse_version('not-a-version')

    def test_ensure_secret_key_atomic_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            first = ensure_secret_key(data_dir)
            self.assertGreaterEqual(len(first), 50)
            second = ensure_secret_key(data_dir)
            self.assertEqual(first, second)
            self.assertEqual((data_dir / '.secret-key').read_text(encoding='ascii').strip(), first)


class DowngradeAndSnapshotTests(TestCase):
    def _make_sqlite(self, path, rows=None):
        with closing(sqlite3.connect(str(path))) as conn:
            conn.execute('CREATE TABLE items (id INTEGER PRIMARY KEY, label TEXT)')
            for label in (rows or ['alpha']):
                conn.execute('INSERT INTO items (label) VALUES (?)', (label,))
            conn.commit()

    def test_downgrade_detected_by_version_tuple(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            write_app_version(data_dir, '0.10.0')
            with self.assertRaises(DowngradeError):
                check_downgrade(data_dir, '0.7.30', connection=None)
            # Newer version is allowed (including 0.10.0 > 0.7.30, which fails naive string compare!)
            check_downgrade(data_dir, '0.10.0', connection=None)
            check_downgrade(data_dir, '1.0.0', connection=None)

    def test_downgrade_detected_by_unknown_migrations(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            write_app_version(data_dir, '0.7.30')
            fake_db = data_dir / 'db.sqlite3'
            self._make_sqlite(fake_db)
            fake_conn = mock.MagicMock()
            fake_conn.settings_dict = {'NAME': str(fake_db)}
            with mock.patch('akte.updates.unknown_applied_migrations', return_value=[('akte', '9999_future_migration')]):
                with self.assertRaises(DowngradeError) as ctx:
                    check_downgrade(data_dir, '0.7.30', connection=fake_conn)
            self.assertIn('akte.9999_future_migration', str(ctx.exception))

    def test_pre_migrate_snapshot_creation_and_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db_path = data_dir / 'db.sqlite3'
            self._make_sqlite(db_path, ['v1'])
            with override_settings(DATA_DIR=data_dir):
                s1 = create_pre_migrate_snapshot(db_path, data_dir, '0.7.30', keep=2)
                s2 = create_pre_migrate_snapshot(db_path, data_dir, '0.7.31', keep=2)
                s3 = create_pre_migrate_snapshot(db_path, data_dir, '0.7.32', keep=2)
                snaps = list_snapshots(data_dir)
                self.assertEqual(len(snaps), 2)
                self.assertIn(s3, snaps)
                self.assertIn(s2, snaps)
                self.assertFalse(s1.exists())

    def test_prepare_startup_snapshots_only_when_migrations_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db_path = data_dir / 'db.sqlite3'
            self._make_sqlite(db_path, ['initial'])
            fake_conn = mock.MagicMock()
            fake_conn.settings_dict = {'NAME': str(db_path)}
            with override_settings(DATA_DIR=data_dir), \
                 mock.patch('akte.updates.connections', {'default': fake_conn}), \
                 mock.patch('akte.updates.unknown_applied_migrations', return_value=[]):
                # No pending migrations -> no snapshot, version marker written
                with mock.patch('akte.updates.pending_migrations', return_value=[]), \
                     mock.patch('akte.updates.call_command') as mock_cmd:
                    snap = prepare_startup(data_dir=data_dir, app_version='0.7.30', auto_migrate=True)
                    self.assertIsNone(snap)
                    mock_cmd.assert_not_called()
                    self.assertEqual((data_dir / VERSION_FILE).read_text(encoding='ascii').strip(), '0.7.30')

                # Pending migrations -> snapshot created BEFORE migrate runs
                with mock.patch('akte.updates.pending_migrations', return_value=[('fake', False)]), \
                     mock.patch('akte.updates.call_command') as mock_cmd:
                    snap = prepare_startup(data_dir=data_dir, app_version='0.7.31', auto_migrate=True)
                    self.assertIsNotNone(snap)
                    self.assertTrue(snap.is_file())
                    mock_cmd.assert_called_once_with('migrate', database='default', interactive=False, verbosity=1)
                    self.assertEqual((data_dir / VERSION_FILE).read_text(encoding='ascii').strip(), '0.7.31')

    def test_rollback_db_restore_and_path_traversal_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db_path = data_dir / 'db.sqlite3'
            self._make_sqlite(db_path, ['before_update'])
            with override_settings(DATA_DIR=data_dir):
                snap = create_pre_migrate_snapshot(db_path, data_dir, '0.8.0', keep=5)
                write_app_version(data_dir, '0.8.0')
                # Mutate DB after snapshot
                with closing(sqlite3.connect(str(db_path))) as conn:
                    conn.execute("INSERT INTO items (label) VALUES ('after_update')")
                    conn.commit()

                # Reject path traversal
                with self.assertRaises(StartupError):
                    restore_snapshot(data_dir, '../db.sqlite3', db_path=db_path)

                # List via management command
                out = StringIO()
                call_command('rollback_db', '--list', stdout=out)
                self.assertIn(snap.name, out.getvalue())

                # Restore latest
                restored, safety = restore_snapshot(data_dir, 'latest', db_path=db_path)
                self.assertEqual(restored, snap)
                self.assertIsNotNone(safety)
                self.assertTrue(safety.is_file())
                self.assertFalse((data_dir / VERSION_FILE).exists())

                with closing(sqlite3.connect(str(db_path))) as conn:
                    labels = [row[0] for row in conn.execute('SELECT label FROM items').fetchall()]
                self.assertEqual(labels, ['before_update'])


class FirstRunSetupTests(TestCase):
    @override_settings(DEMO_MODE=False)
    def test_setup_flow_validation_and_permanent_404(self):
        User = get_user_model()
        User.objects.all().delete()

        # /login/ redirects to /setup/ when 0 users exist
        res = self.client.get('/login/')
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res['Location'], '/setup/')

        # /setup/ GET renders onboarding form
        res = self.client.get('/setup/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Erstes Konto anlegen')

        # Weak password (< 12 chars) is rejected by Django password validators
        res = self.client.post('/setup/', {
            'username': 'owner',
            'password1': 'short123',
            'password2': 'short123',
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(User.objects.count(), 0)

        # Strong password creates superuser, logs in, redirects to home
        res = self.client.post('/setup/', {
            'username': 'owner',
            'password1': 'Sicheres-Projektakte-Passwort-2026!',
            'password2': 'Sicheres-Projektakte-Passwort-2026!',
        })
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res['Location'], '/')
        self.assertEqual(User.objects.count(), 1)
        user = User.objects.get(username='owner')
        self.assertTrue(user.is_superuser and user.is_staff)

        # Once >= 1 user exists, /setup/ permanently returns 404 (GET & POST)
        self.client.logout()
        self.assertEqual(self.client.get('/setup/').status_code, 404)
        self.assertEqual(self.client.post('/setup/', {
            'username': 'second',
            'password1': 'Sicheres-Projektakte-Passwort-2026!',
            'password2': 'Sicheres-Projektakte-Passwort-2026!',
        }).status_code, 404)
        self.assertEqual(User.objects.count(), 1)
        # /login/ renders normally now
        self.assertEqual(self.client.get('/login/').status_code, 200)

    @override_settings(DEMO_MODE=True)
    def test_setup_disabled_in_demo_mode(self):
        get_user_model().objects.all().delete()
        self.assertEqual(self.client.get('/setup/').status_code, 404)


class StandaloneBackupCommandTests(TestCase):
    def test_backup_without_paperless_allowed_when_paperless_unconfigured(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'standalone.zip'
            with override_settings(DEMO_MODE=False, PAPERLESS_URL=''), \
                 mock.patch('akte.management.commands.backup.backup', return_value=dest) as mock_bk:
                out = StringIO()
                call_command('backup', str(dest), stdout=out)
                mock_bk.assert_called_once_with(str(dest), None)
                self.assertIn('standalone.zip', out.getvalue())

    def test_backup_requires_paperless_bundle_when_paperless_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'with_paperless.zip'
            with override_settings(DEMO_MODE=False, PAPERLESS_URL='http://paperless:8000'):
                with self.assertRaises(CommandError):
                    call_command('backup', str(dest))


class Paket1SelfHosterAndSecurityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('paket1-user', password='Sicheres-Passwort-2026!')
        self.client.force_login(self.user)

    @override_settings(FEEDBACK_WIDGET_ENABLED=False)
    def test_feedback_widget_disabled_by_default_hides_ui_and_returns_404(self):
        home = self.client.get('/')
        self.assertEqual(home.status_code, 200)
        self.assertNotContains(home, 'feedback-open')
        settings_page = self.client.get('/einstellungen/konto/')
        self.assertNotContains(settings_page, '/einstellungen/feedback/')
        self.assertEqual(self.client.get('/einstellungen/feedback/').status_code, 404)
        self.assertEqual(self.client.post('/feedback/', {'text': 'hello'}).status_code, 404)

    @override_settings(FEEDBACK_WIDGET_ENABLED=True)
    def test_feedback_widget_enabled_shows_ui_and_allows_settings_tab(self):
        home = self.client.get('/')
        self.assertContains(home, 'feedback-open')
        self.assertEqual(self.client.get('/einstellungen/feedback/').status_code, 200)

    def test_allowed_gemini_models_single_source_of_truth_and_fallback(self):
        from django.conf import settings as django_settings
        from akte.models import Preferences
        from akte.preferences import model_spec
        self.assertEqual(django_settings.ALLOWED_GEMINI_MODELS, ('gemini-3.5-flash-lite', 'gemini-3.8-flash'))
        Preferences.objects.update_or_create(owner=self.user, defaults={'ai_mode': 'daily'})
        self.assertEqual(model_spec(self.user)[0], 'gemini-3.5-flash-lite')
        Preferences.objects.update_or_create(owner=self.user, defaults={'ai_mode': 'complex'})
        self.assertEqual(model_spec(self.user)[0], 'gemini-3.8-flash')
        # Unknown/legacy DB value falls back safely to an allowed model
        Preferences.objects.update_or_create(owner=self.user, defaults={'ai_mode': 'legacy-unknown-mode'})
        fallback = model_spec(self.user)
        self.assertIn(fallback[0], django_settings.ALLOWED_GEMINI_MODELS)

    @override_settings(AI_ENABLED=False, GEMINI_API_KEY='', PAPERLESS_URL='', PAPERLESS_TOKEN='', IMAP_HOST='')
    def test_integrations_status_hides_dead_buttons_when_unconfigured(self):
        page = self.client.get('/einstellungen/anbindungen/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Status der Anbindungen')
        self.assertContains(page, 'Deaktiviert')
        self.assertNotContains(page, 'Paperless jetzt abgleichen')
        self.assertNotContains(page, 'Postfach jetzt prüfen')

    def test_html_csp_header_present_without_unsafe_inline_scripts(self):
        page = self.client.get('/')
        csp = page.headers.get('Content-Security-Policy', '')
        self.assertIn("script-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertNotIn("'unsafe-inline'", csp.split("script-src")[1].split(";")[0])

    def test_setup_route_is_rate_limited(self):
        from akte.security import security_cache
        security_cache().clear()
        self.addCleanup(security_cache().clear)
        get_user_model().objects.all().delete()
        with override_settings(DEMO_MODE=False):
            anon = self.client.__class__()
            for _ in range(60):
                self.assertEqual(anon.get('/setup/').status_code, 200)
            self.assertEqual(anon.get('/setup/').status_code, 429)


class Pre100PiAndSoftNavRegressionTests(TestCase):
    databases = {'default', 'demo'}

    def test_healthcheck_accepts_setup_redirect_at_zero_users(self):
        import os
        from akte import healthcheck
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / 'db.sqlite3'
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute('CREATE TABLE t (id INTEGER)')
            fake_resp = mock.Mock(status=302)
            fake_resp.getheader.return_value = '/setup/'
            fake_conn = mock.Mock()
            fake_conn.getresponse.return_value = fake_resp
            with mock.patch.dict(os.environ, {'DATA_DIR': tmp}), \
                 mock.patch('akte.healthcheck.http.client.HTTPConnection', return_value=fake_conn):
                healthcheck.main()
            fake_conn.request.assert_called_once_with('HEAD', '/login/', headers={'Host': '127.0.0.1'})

    def test_dockerignore_keeps_rollback_db_management_command(self):
        from django.conf import settings as django_settings
        root = Path(django_settings.BASE_DIR)
        cmd = root / 'akte' / 'management' / 'commands' / 'rollback_db.py'
        self.assertTrue(cmd.is_file())
        dockerignore = (root / '.dockerignore').read_text(encoding='utf-8').splitlines()
        self.assertIn('!akte/management/commands/rollback_db.py', [line.strip() for line in dockerignore])

    def test_prepare_startup_migrates_demo_database_when_file_backed(self):
        with tempfile.TemporaryDirectory() as tmp:
            demo_db = Path(tmp) / 'demo.sqlite3'
            fake_default = mock.Mock()
            fake_default.settings_dict = {'NAME': ':memory:'}
            fake_demo = mock.Mock()
            fake_demo.settings_dict = {'NAME': str(demo_db)}
            fake_conns = {'default': fake_default, 'demo': fake_demo}
            with mock.patch('akte.updates.connections', fake_conns), \
                 mock.patch('akte.updates.check_downgrade'), \
                 mock.patch('akte.updates.pending_migrations', side_effect=[[], [('akte', '0001_initial')]]), \
                 mock.patch('akte.updates.call_command') as mock_cmd:
                prepare_startup(data_dir=tmp, app_version='0.9.0', auto_migrate=True)
                mock_cmd.assert_called_once_with('migrate', database='demo', interactive=False, verbosity=0)

    def test_demo_language_switch_immediately_after_start_is_not_rate_limited(self):
        from akte.security import security_cache
        security_cache().clear()
        self.addCleanup(security_cache().clear)
        client = self.client.__class__()
        start = client.post('/demo/start/', HTTP_ACCEPT_LANGUAGE='en-US,en;q=0.9')
        self.assertEqual(start.status_code, 302)
        switch = client.post('/demo/sprache/', {'language': 'de', 'next': '/demo/'})
        self.assertEqual(switch.status_code, 302)
        self.assertEqual(switch['Location'], '/demo/')

    def test_soft_nav_scripts_rebind_on_lg_navigate(self):
        from django.conf import settings as django_settings
        static_dir = Path(django_settings.BASE_DIR) / 'static'
        for name in ('theme.js', 'editors.js', 'app.js', 'handover.js', 'capture.js', 'voice.js'):
            text = (static_dir / name).read_text(encoding='utf-8')
            self.assertIn('lg:navigate', text, f'{name} must re-initialize on lg:navigate')


class StandaloneAndIntegrationMatrixTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('matrix-user', password='Sicheres-Passwort-2026!')
        self.client.force_login(self.user)
        from akte.models import Project
        self.project = Project.objects.create(owner=self.user, name='Matrix Car', kind='auto')

    @staticmethod
    def _tiny_png_upload(name='receipt.png', color=(20, 120, 90)):
        from io import BytesIO
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        buf = BytesIO()
        Image.new('RGB', (16, 16), color=color).save(buf, format='PNG')
        return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')

    def test_demo_mode_stays_active_even_when_ai_enabled_false(self):
        from akte.preferences import ai_is_active, paperless_is_active
        with override_settings(DEMO_MODE=True, AI_ENABLED=False, GEMINI_API_KEY='', PAPERLESS_URL='', PAPERLESS_TOKEN=''):
            self.assertTrue(ai_is_active())
            self.assertTrue(paperless_is_active())
        with override_settings(DEMO_MODE=False, AI_ENABLED=False, GEMINI_API_KEY='', PAPERLESS_URL='', PAPERLESS_TOKEN=''):
            self.assertFalse(ai_is_active())
            self.assertFalse(paperless_is_active())

    def test_quick_capture_standalone_without_ai_saves_directly_without_job(self):
        import uuid
        from akte.capture import receive_capture
        from akte.models import Job
        with tempfile.TemporaryDirectory() as media_dir, override_settings(
            DEMO_MODE=False, AI_ENABLED=False, GEMINI_API_KEY='', PAPERLESS_URL='', PAPERLESS_TOKEN='',
            MEDIA_ROOT=Path(media_dir),
        ):
            doc = receive_capture(
                self.user,
                text='Replaced cabin air filter\nLooks clean now.',
                uploads=[self._tiny_png_upload('filter.png')],
                project=self.project,
                token=uuid.uuid4(),
            )
            self.assertEqual(doc.status, 'accepted')
            self.assertFalse(doc.ai_enabled)
            self.assertEqual(doc.entry.title, 'Replaced cabin air filter')
            self.assertFalse(doc.entry.is_provisional)
            self.assertEqual(Job.objects.filter(document=doc, kind='capture').count(), 0)

    def test_receipt_upload_matrix_all_four_combinations_and_zero_price_ai(self):
        from decimal import Decimal
        from akte.files import receive_document
        from akte.models import Job
        from akte.worker import run_one

        # 1. AI OFF + Paperless OFF -> direct review, no document job
        with tempfile.TemporaryDirectory() as m1, override_settings(
            DEMO_MODE=False, AI_ENABLED=False, GEMINI_API_KEY='', PAPERLESS_URL='', PAPERLESS_TOKEN='',
            MEDIA_ROOT=Path(m1),
        ):
            doc, _ = receive_document(self.user, self._tiny_png_upload('r1.png'), project=self.project)
            self.assertEqual(doc.status, 'review')
            self.assertFalse(doc.ai_enabled)
            self.assertEqual(Job.objects.filter(document=doc).count(), 0)

        # 2. AI ON + Paperless OFF + default 0 token rates -> direct stage='ai', worker succeeds without Paperless!
        from akte.proposal_schema import empty_entry
        fake_entry = {**empty_entry(), 'title': 'Brake fluid', 'kind': 'notiz', 'description': 'Brake fluid purchase'}
        fake_proposal = {
            'vendor': 'Local Auto Parts', 'invoice_number': 'INV-101', 'invoice_date': '2026-11-20',
            'service_date': None, 'odometer_km': None, 'gross_total': '49.90', 'net_total': None,
            'tax_total': None, 'currency': 'EUR', 'positions': [],
            'entry': fake_entry, 'project_assignment': None, 'definition': None, 'inferred_fields': ['entry.title'],
        }
        fake_ai = mock.Mock()
        fake_ai.extract.return_value = (fake_proposal, {'promptTokenCount': 200, 'totalTokenCount': 320})
        with tempfile.TemporaryDirectory() as m2, override_settings(
            DEMO_MODE=False, AI_ENABLED=True, GEMINI_API_KEY='synthetic-key',
            AI_INPUT_USD_PER_M=Decimal('0'), AI_OUTPUT_USD_PER_M=Decimal('0'),
            PAPERLESS_URL='', PAPERLESS_TOKEN='', MEDIA_ROOT=Path(m2),
        ), mock.patch('akte.worker.ai', return_value=fake_ai), \
             mock.patch('akte.worker.paperless', side_effect=AssertionError('Paperless must not be called')):
            doc2, _ = receive_document(self.user, self._tiny_png_upload('r2.png', color=(60, 140, 90)), project=self.project)
            self.assertEqual(doc2.status, 'ai')
            self.assertTrue(doc2.ai_enabled)
            self.assertTrue(doc2.ai_with_original)
            job2 = Job.objects.get(document=doc2)
            self.assertEqual(job2.stage, 'ai')
            # Drain thumbnail + ai jobs
            while run_one():
                pass
            doc2.refresh_from_db()
            self.assertEqual(doc2.status, 'review')
            self.assertEqual(doc2.proposal.get('vendor'), 'Local Auto Parts')
            self.assertEqual(fake_ai.extract.call_count, 1)

        # 3. AI OFF + Paperless ON -> stage='upload', ai_enabled=False
        with tempfile.TemporaryDirectory() as m3, override_settings(
            DEMO_MODE=False, AI_ENABLED=False, GEMINI_API_KEY='',
            PAPERLESS_URL='https://paperless.invalid', PAPERLESS_TOKEN='tok', MEDIA_ROOT=Path(m3),
        ):
            doc3, _ = receive_document(self.user, self._tiny_png_upload('r3.png', color=(100, 160, 90)), project=self.project)
            self.assertEqual(doc3.status, 'paperless')
            self.assertFalse(doc3.ai_enabled)
            self.assertEqual(Job.objects.get(document=doc3).stage, 'upload')

        # 4. AI ON + Paperless ON -> stage='upload', ai_enabled=True
        with tempfile.TemporaryDirectory() as m4, override_settings(
            DEMO_MODE=False, AI_ENABLED=True, GEMINI_API_KEY='synthetic-key',
            PAPERLESS_URL='https://paperless.invalid', PAPERLESS_TOKEN='tok', MEDIA_ROOT=Path(m4),
        ):
            doc4, _ = receive_document(self.user, self._tiny_png_upload('r4.png', color=(140, 180, 90)), project=self.project)
            self.assertEqual(doc4.status, 'paperless')
            self.assertTrue(doc4.ai_enabled)
            self.assertEqual(Job.objects.get(document=doc4).stage, 'upload')

    def test_standalone_ui_hides_paperless_and_shows_calm_ai_hint(self):
        from django.urls import reverse
        with override_settings(DEMO_MODE=False, AI_ENABLED=False, GEMINI_API_KEY='', PAPERLESS_URL='', PAPERLESS_TOKEN=''):
            home = self.client.get('/')
            self.assertNotContains(home, 'id="voice-start"')
            self.assertContains(home, 'GEMINI_API_KEY')
            upload = self.client.get(reverse('document_upload'))
            self.assertNotContains(upload, 'Bereits in Paperless?')
            self.assertNotContains(upload, 'In Paperless suchen')
            self.assertContains(upload, 'GEMINI_API_KEY')
