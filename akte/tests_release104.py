import tempfile
import uuid
import zipfile
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .capture import receive_capture
from .demo import synthetic_pdf
from .exports import portable_export
from .files import receive_document
from .models import Allocation, Document, Project
from .updates import prepare_startup


class Release104FixesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='pi4v104', password='test-password-123')
        self.client.force_login(self.user)
        self.project_a = Project.objects.create(owner=self.user, name='Auto A', kind='vehicle')
        self.project_b = Project.objects.create(owner=self.user, name='Haus B', kind='house')

    @override_settings(DEMO_MODE=False, PAPERLESS_URL='', PAPERLESS_TOKEN='', AI_ENABLED=False, GEMINI_API_KEY='')
    def test_manual_receipt_default_currency_and_split_across_two_projects(self):
        upload = SimpleUploadedFile('baumarkt.pdf', synthetic_pdf('Baumarkt 100 EUR'), content_type='application/pdf')
        doc, _ = receive_document(self.user, upload, project=self.project_a, create_entry=True)

        # GET review pre-fills default currency EUR
        get_resp = self.client.get(reverse('review', args=[doc.pk]))
        self.assertEqual(get_resp.status_code, 200)
        self.assertIn('value="EUR"', get_resp.content.decode('utf-8'))

        # Missing alloc_project yields clean German message instead of raw UUID error
        bad_resp = self.client.post(
            reverse('review', args=[doc.pk]),
            {
                'action': 'accept',
                'simple_review': '1',
                'version': str(doc.version),
                'draft_version': str(doc.review_version),
                'chronicle_project': str(self.project_a.pk),
                'chronicle_mode': 'existing',
                'chronicle_entry': str(doc.entry_id),
                'vendor': 'Baumarkt',
                'gross_total': '100,00',
                'currency': '',
                'positions_editor': 'expert',
                'positions': '[]',
                'alloc_project': [str(self.project_a.pk), ''],
                'alloc_entry': ['', ''],
                'alloc_step': ['', ''],
                'alloc_amount': ['60,00', '40,00'],
                'alloc_ref': ['', ''],
            },
        )
        self.assertEqual(bad_resp.status_code, 200)
        bad_html = bad_resp.content.decode('utf-8')
        self.assertIn('für jede Kostenzuordnung ein Projekt auswählen', bad_html)
        self.assertNotIn('keine gültige UUID', bad_html)

        # Split across project_a and project_b (which has no entries) with empty currency -> falls back to EUR
        ok_resp = self.client.post(
            reverse('review', args=[doc.pk]),
            {
                'action': 'accept',
                'simple_review': '1',
                'version': str(doc.version),
                'draft_version': str(doc.review_version),
                'chronicle_project': str(self.project_a.pk),
                'chronicle_mode': 'existing',
                'chronicle_entry': str(doc.entry_id),
                'vendor': 'Baumarkt',
                'gross_total': '100,00',
                'currency': '',
                'positions_editor': 'expert',
                'positions': '[]',
                'alloc_project': [str(self.project_a.pk), str(self.project_b.pk)],
                'alloc_entry': ['', ''],
                'alloc_step': ['', ''],
                'alloc_amount': ['60,00', '40,00'],
                'alloc_ref': ['', ''],
            },
            follow=True,
        )
        self.assertEqual(ok_resp.status_code, 200)
        doc.refresh_from_db()
        self.assertEqual(doc.status, 'accepted')
        self.assertEqual(doc.source.currency, 'EUR')
        self.assertEqual(doc.source.amount, Decimal('100.00'))
        allocs = list(Allocation.objects.filter(source=doc.source).order_by('amount'))
        self.assertEqual(len(allocs), 2)
        self.assertEqual({a.project_id: a.amount for a in allocs}, {self.project_a.pk: Decimal('60.00'), self.project_b.pk: Decimal('40.00')})

    @override_settings(DEMO_MODE=False, PAPERLESS_URL='', PAPERLESS_TOKEN='', AI_ENABLED=False, GEMINI_API_KEY='')
    def test_pure_text_note_not_shown_as_receipt_in_inbox_search_or_zip(self):
        snap = receive_capture(
            self.user,
            text='Einzigartige Textnotiz Dachrinne gesaeubert',
            project=self.project_a,
            token=uuid.uuid4(),
        )
        # Not listed in Belegeingang
        inbox_html = self.client.get(reverse('inbox')).content.decode('utf-8')
        self.assertNotIn('Einzigartige Textnotiz Dachrinne gesaeubert', inbox_html)

        # Single search hit (Entry only, no duplicate Document hit)
        search_resp = self.client.get(reverse('search') + '?q=Dachrinne')
        self.assertEqual(search_resp.status_code, 200)
        self.assertEqual(len(search_resp.context['entries']), 1)
        self.assertEqual(len(search_resp.context['documents']), 0)

        # Not written as belege/<id>.txt in ZIP export
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / 'export.zip'
            portable_export(dest, self.user, project=self.project_a)
            with zipfile.ZipFile(dest) as zf:
                self.assertNotIn(f'belege/{snap.pk}.txt', set(zf.namelist()))
                self.assertIn('Dachrinne gesaeubert', zf.read('index.html').decode('utf-8'))

    def test_fresh_empty_db_does_not_create_pre_migrate_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db_path = data_dir / 'db.sqlite3'
            fake_conn = mock.MagicMock()
            fake_conn.settings_dict = {'NAME': str(db_path)}

            def fake_pending(_conn):
                # Simulates SQLite creating an empty 0-byte file when Django opens connection
                db_path.touch()
                return [('akte', False)]

            with override_settings(DATA_DIR=data_dir), \
                 mock.patch('akte.updates.connections', {'default': fake_conn}), \
                 mock.patch('akte.updates.unknown_applied_migrations', return_value=[]), \
                 mock.patch('akte.updates.pending_migrations', side_effect=fake_pending), \
                 mock.patch('akte.updates.call_command') as mock_cmd:
                snap = prepare_startup(data_dir=data_dir, app_version='1.0.4', auto_migrate=True)
                self.assertIsNone(snap)
                mock_cmd.assert_called_once_with('migrate', database='default', interactive=False, verbosity=1)

    def test_static_assets_v104_fixes(self):
        root = Path(settings.BASE_DIR)
        review_css = (root / 'static' / 'review-simple.css').read_text(encoding='utf-8')
        self.assertNotIn('#review-allocation .allocation-row>label:first-child{display:none}', review_css)

        style_css = (root / 'static' / 'style.css').read_text(encoding='utf-8')
        self.assertNotIn('@view-transition{navigation:auto}', style_css)

        shell_js = (root / 'static' / 'instant-shell.js').read_text(encoding='utf-8')
        self.assertNotIn('REVALIDATE_MS', shell_js)

        self.assertTrue(settings.APP_VERSION >= '1.0.4')
