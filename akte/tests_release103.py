import io
import tempfile
import uuid
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .adapters import IntegrationError
from .capture import receive_capture
from .demo import synthetic_pdf
from .exports import portable_export
from .files import receive_document
from .forms import PartForm, ProjectForm
from .knowledge import KnowledgeForm
from .maintenance import MaintenanceForm, complete_maintenance
from .models import Document, Entry, MaintenanceRule, Project


def _minimal_pdf():
    return synthetic_pdf('Werkstattrechnung RE-2026-103 89,50 EUR')


class Release103FixesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='pi4tester', password='test-password-123')
        self.client.force_login(self.user)
        self.project = Project.objects.create(owner=self.user, name='Testauto', kind='vehicle')

    @override_settings(DEMO_MODE=False, PAPERLESS_URL='', PAPERLESS_TOKEN='', AI_ENABLED=False, GEMINI_API_KEY='')
    def test_local_receipt_accept_without_paperless_and_no_ai_notice(self):
        upload = SimpleUploadedFile('werkstatt.pdf', _minimal_pdf(), content_type='application/pdf')
        resp = self.client.post(reverse('document_upload'), {'document': upload}, follow=True)
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode('utf-8')
        self.assertNotIn('Die KI versucht die Zuordnung im Hintergrund', body)
        self.assertNotIn('Verarbeitung läuft im Hintergrund', body)

        doc = Document.objects.get(owner=self.user)
        self.assertEqual(doc.instance, 'local')
        self.assertIsNone(doc.paperless_id)

        # Accept the locally uploaded receipt without Paperless
        accept_resp = self.client.post(
            reverse('review', args=[doc.pk]),
            {
                'action': 'accept',
                'simple_review': '1',
                'version': str(doc.version),
                'draft_version': str(doc.review_version),
                'chronicle_project': str(self.project.pk),
                'chronicle_mode': 'existing',
                'chronicle_entry': str(doc.entry_id),
                'vendor': 'Werkstatt Nord',
                'invoice_number': 'RE-2026-103',
                'gross_total': '89,50',
                'currency': 'EUR',
                'positions_editor': 'expert',
                'positions': '[]',
                'alloc_project': [str(self.project.pk)],
                'alloc_entry': [''],
                'alloc_step': [''],
                'alloc_amount': ['89,50'],
                'alloc_ref': [''],
            },
            follow=True,
        )
        self.assertEqual(accept_resp.status_code, 200)
        self.assertNotIn('Paperless-ID fehlt', accept_resp.content.decode('utf-8'))
        doc.refresh_from_db()
        self.assertEqual(doc.status, 'accepted')
        self.assertEqual(doc.source.amount, Decimal('89.50'))

    @override_settings(DEMO_MODE=False, PAPERLESS_URL='', PAPERLESS_TOKEN='', AI_ENABLED=False, GEMINI_API_KEY='')
    def test_project_zip_export_with_text_capture_and_local_receipt(self):
        snap = receive_capture(
            self.user,
            text='Bremsbeläge vorne geprüft, noch 6 mm Restbelag.',
            project=self.project,
            token=uuid.uuid4(),
        )
        upload = SimpleUploadedFile('teile.pdf', _minimal_pdf(), content_type='application/pdf')
        receipt, _ = receive_document(self.user, upload, project=self.project, create_entry=False)

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / 'export.zip'
            portable_export(dest, self.user, project=self.project)
            with zipfile.ZipFile(dest) as zf:
                names = set(zf.namelist())
                self.assertNotIn(f'belege/{snap.pk}.txt', names)
                self.assertIn(f'belege/{receipt.pk}.pdf', names)
                self.assertIn('Bremsbeläge vorne geprüft', zf.read('index.html').decode('utf-8'))

        # Missing original on an invoice document reports which document is missing
        broken = Document.objects.create(
            owner=self.user,
            project=self.project,
            instance='local',
            capture_type='invoice',
            proposal={'invoice_number': 'RE-FEHLT-99'},
        )
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(IntegrationError) as ctx:
                portable_export(Path(td) / 'broken.zip', self.user, project=self.project)
            self.assertIn('RE-FEHLT-99', str(ctx.exception))
            self.assertIn(str(broken.pk), str(ctx.exception))

    @override_settings(DEMO_MODE=False, PAPERLESS_URL='', PAPERLESS_TOKEN='', AI_ENABLED=False, GEMINI_API_KEY='')
    def test_labels_document_str_and_german_forms(self):
        snap = receive_capture(
            self.user,
            text='Reifendruck kontrolliert 2,4 bar',
            project=self.project,
            token=uuid.uuid4(),
        )
        self.assertEqual(str(snap), 'Reifendruck kontrolliert 2,4 bar')
        self.assertNotIn('Document object', str(snap))

        inbox_html = self.client.get(reverse('inbox')).content.decode('utf-8')
        self.assertNotIn('Paperless-Dokument', inbox_html)

        search_html = self.client.get(reverse('search') + '?q=Reifendruck').content.decode('utf-8')
        self.assertIn('Reifendruck kontrolliert 2,4 bar', search_html)
        self.assertNotIn('Paperless-Beleg', search_html)

        part_form = PartForm(owner=self.user)
        rendered_parts = str(part_form['documents'])
        self.assertNotIn('Document object', rendered_parts)
        self.assertNotIn('Reifendruck kontrolliert 2,4 bar', rendered_parts)

        project_form = ProjectForm()
        self.assertEqual(str(project_form.fields['color'].label), 'Farbe')
        self.assertEqual(str(project_form.fields['ai_profile'].label), 'KI-Profil')

        knowledge_form = KnowledgeForm(initial={'version': 0})
        self.assertEqual(str(knowledge_form.fields['source_url'].label), 'Quell-URL (optional)')

    def test_maintenance_filters_incompatible_meter_unit_and_explains_requirements(self):
        form = MaintenanceForm()
        self.assertTrue(form.fields['base_meter'].help_text)
        self.assertTrue(form.fields['base_date'].help_text)

        rule = MaintenanceRule.objects.create(
            project=self.project,
            title='Ölwechsel',
            interval_km=Decimal('15000'),
            meter_unit='km',
            base_meter=Decimal('100000'),
            active=True,
        )
        entry_no_unit = Entry.objects.create(
            project=self.project,
            title='Öl ohne Einheit',
            status='erledigt',
            date=date(2026, 11, 20),
            precision='day',
            meter=Decimal('115000'),
            meter_unit='',
        )
        entry_ok = Entry.objects.create(
            project=self.project,
            title='Öl mit km',
            status='erledigt',
            date=date(2026, 11, 21),
            precision='day',
            meter=Decimal('116000'),
            meter_unit='km',
        )
        html = self.client.get(reverse('project_maintenance', args=[self.project.pk])).content.decode('utf-8')
        self.assertNotIn('Öl ohne Einheit', html)
        self.assertIn('Öl mit km', html)
        self.assertIn('116000', html)
        self.assertIn('data-maintenance-form', html)

        with self.assertRaises(ValidationError) as ctx:
            complete_maintenance(self.user, rule, entry_no_unit, rule.version)
        self.assertIn('Zählereinheit', str(ctx.exception))

    def test_static_fixes_and_compose_healthcheck_present(self):
        root = Path(settings.BASE_DIR)
        compose = (root / 'compose.yaml').read_text(encoding='utf-8')
        self.assertIn('start_period: 120s', compose)
        self.assertIn(settings.APP_VERSION, compose)

        pwa_js = (root / 'static' / 'pwa.js').read_text(encoding='utf-8')
        self.assertIn('!window.isSecureContext', pwa_js)
        self.assertIn('erfordern HTTPS', pwa_js)

        shell_js = (root / 'static' / 'instant-shell.js').read_text(encoding='utf-8')
        self.assertNotIn('startViewTransition', shell_js)
