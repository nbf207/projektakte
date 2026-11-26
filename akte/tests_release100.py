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
