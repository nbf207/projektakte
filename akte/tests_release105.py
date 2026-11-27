import tempfile
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from pypdf import PdfReader

from .capture import receive_capture
from .demo import synthetic_pdf
from .exports import report_pdf
from .files import receive_document
from .forms import PartForm
from .maintenance_pdf import maintenance_pdf
from .models import Allocation, CostSource, Document, Entry, Project


class Release105FixesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='pi4v105', password='test-password-123')
        self.client.force_login(self.user)
        self.project_a = Project.objects.create(owner=self.user, name='Auto A', kind='vehicle')
        self.project_b = Project.objects.create(owner=self.user, name='Haus B', kind='house')

    @override_settings(DEMO_MODE=False, PAPERLESS_URL='', PAPERLESS_TOKEN='', AI_ENABLED=False, GEMINI_API_KEY='')
    def test_unassigned_text_note_can_be_assigned_and_has_non_receipt_wording(self):
        snap = receive_capture(
            self.user,
            text='Scheibenwischer quietschen leicht',
            project=None,
            token=uuid.uuid4(),
        )
        self.assertEqual(snap.status, 'review')
        self.assertTrue(snap.entry.project.is_scan_inbox)

        # Entry page notice should not call a text note "Der Beleg" / "Rechnung direkt prüfen"
        entry_html = self.client.get(reverse('entry', args=[snap.entry_id])).content.decode('utf-8')
        self.assertIn('Schnellerfassung ist dauerhaft lokal gespeichert', entry_html)
        self.assertNotIn('Rechnung direkt prüfen', entry_html)

        # Assign project via document_route
        route_resp = self.client.post(
            reverse('document_route', args=[snap.pk]),
            {'project': str(self.project_a.pk), 'version': str(snap.version)},
            follow=True,
        )
        self.assertEqual(route_resp.status_code, 200)
        self.assertNotIn('Nur offene Belege', route_resp.content.decode('utf-8'))
        snap.refresh_from_db()
        snap.entry.refresh_from_db()
        self.assertEqual(snap.project_id, self.project_a.pk)
        self.assertEqual(snap.entry.project_id, self.project_a.pk)
        self.assertEqual(snap.status, 'accepted')
        self.assertFalse(snap.entry.is_provisional)

        # Parts form must not list pure text notes under Verknüpfte Belege
        part_form = PartForm(owner=self.user)
        self.assertNotIn(snap.pk, list(part_form.fields['documents'].queryset.values_list('pk', flat=True)))

    @override_settings(DEMO_MODE=False, PAPERLESS_URL='', PAPERLESS_TOKEN='', AI_ENABLED=False, GEMINI_API_KEY='')
    def test_inline_pdf_preview_uses_viewer_compatible_csp_without_sandbox(self):
        upload = SimpleUploadedFile('beleg.pdf', synthetic_pdf('Rechnung 14,50 EUR'), content_type='application/pdf')
        doc, _ = receive_document(self.user, upload, project=self.project_a, create_entry=False)

        inline_resp = self.client.get(reverse('media', args=[doc.asset_id]))
        self.assertEqual(inline_resp.status_code, 200)
        csp = inline_resp.headers.get('Content-Security-Policy', '')
        self.assertNotIn('sandbox', csp)
        self.assertIn("frame-ancestors 'self'", csp)
        self.assertIn("object-src 'self'", csp)
        inline_resp.close()

        dl_resp = self.client.get(reverse('media', args=[doc.asset_id]) + '?download=1')
        self.assertEqual(dl_resp.status_code, 200)
        self.assertIn('sandbox', dl_resp.headers.get('Content-Security-Policy', ''))
        dl_resp.close()

    def test_pdf_reports_consistent_currency_and_no_duplicate_quick_note_text(self):
        entry = Entry.objects.create(
            project=self.project_a,
            title='Wischerblatt gewechselt',
            description='Wischerblatt gewechselt',
            status='erledigt',
            kind='wartung',
            date=date(2026, 11, 25),
            precision='day',
            cost_state='known',
        )
        source = CostSource.objects.create(
            owner=self.user,
            label='Autoteile',
            amount=Decimal('14.50'),
            currency='EUR',
            invoice_number='RE-1450',
        )
        Allocation.objects.create(source=source, project=self.project_a, entry=entry, amount=Decimal('14.50'))

        with tempfile.TemporaryDirectory() as td:
            full_pdf = Path(td) / 'full.pdf'
            report_pdf(full_pdf, self.project_a, Entry.objects.filter(pk=entry.pk))
            full_text = '\n'.join(page.extract_text() or '' for page in PdfReader(str(full_pdf)).pages)
            self.assertIn('14,50 EUR', full_text)
            self.assertNotIn('14.50 EUR', full_text)
            self.assertEqual(full_text.count('Wischerblatt gewechselt'), 1)

            compact_pdf = Path(td) / 'compact.pdf'
            maintenance_pdf(compact_pdf, self.project_a, entry)
            compact_text = '\n'.join(page.extract_text() or '' for page in PdfReader(str(compact_pdf)).pages)
            self.assertEqual(compact_text.count('Wischerblatt gewechselt'), 1)

    def test_allocation_filtering_hooks_no_view_transition_and_readme_notes(self):
        root = Path(settings.BASE_DIR)
        self.assertEqual(settings.APP_VERSION, '1.0.5')

        alloc_tpl = (root / 'templates' / 'allocation.html').read_text(encoding='utf-8')
        self.assertIn('data-project="{{ e.project_id }}"', alloc_tpl)

        app_js = (root / 'static' / 'app.js').read_text(encoding='utf-8')
        self.assertIn('syncAllocationRow', app_js)

        shell_js = (root / 'static' / 'instant-shell.js').read_text(encoding='utf-8')
        self.assertNotIn('startViewTransition', shell_js)

        ws_css = (root / 'static' / 'workspace.css').read_text(encoding='utf-8')
        self.assertNotIn('view-transition-name', ws_css)

        readme = (root / 'README.md').read_text(encoding='utf-8')
        self.assertIn('about 1 minute', readme)
        self.assertIn('COMPOSE_PROJECT_NAME', readme)
