"""Completion refresh must follow jobs and keep the reviewer's own input."""
from django.urls import reverse
from django.utils import timezone
from django.test import TestCase
from . import tests_invoice_chronicle
from .models import CostSource, Entry, Job


class ReviewRefreshTests(TestCase):
    setUp = tests_invoice_chronicle.InvoiceChronicleTests.setUp
    document = tests_invoice_chronicle.InvoiceChronicleTests.document
    payload = tests_invoice_chronicle.InvoiceChronicleTests.payload

    def test_pending_scan_shows_processing_instead_of_legacy_warning(self):
        doc = self.document()
        doc.proposal = {}
        doc.status = 'ocr'
        doc.save()
        Job.objects.filter(document=doc).update(status='pending', stage='ocr')
        page = self.client.get(reverse('review', args=[doc.pk]))
        self.assertContains(page, 'data-processing="1"')
        self.assertContains(page, 'review-refresh.js')
        self.assertContains(page, 'aktualisiert sich automatisch')
        self.assertNotContains(page, 'Dieser ältere Vorschlag')

    def test_retrying_legacy_proposal_is_processing_even_with_review_status(self):
        doc = self.document()
        doc.proposal.pop('entry')
        doc.error = 'Synthetischer vorübergehender Ausfall'
        doc.save()
        Job.objects.filter(document=doc).update(status='pending', stage='ai')
        page = self.client.get(reverse('review', args=[doc.pk]))
        self.assertContains(page, 'data-processing="1"')
        self.assertNotContains(page, 'Dieser ältere Vorschlag')
        state = self.client.get(reverse('document_status', args=[doc.pk])).json()
        self.assertTrue(state['processing'])
        self.assertEqual(state['label'], 'Wird verarbeitet')

    def test_status_follows_job_completion_and_blocked_errors(self):
        doc = self.document()
        url = reverse('document_status', args=[doc.pk])
        for status, expected in [('running', True), ('pending', True), ('done', False), ('blocked', False)]:
            with self.subTest(job_status=status):
                Job.objects.filter(document=doc).update(status=status)
                self.assertEqual(self.client.get(url).json()['processing'], expected)

    def test_completed_current_proposal_does_not_start_repeated_refresh(self):
        doc = self.document()
        page = self.client.get(reverse('review', args=[doc.pk]))
        self.assertContains(page, 'data-processing="0"')
        self.assertNotContains(page, 'Dieser ältere Vorschlag')
        self.assertEqual(Job.objects.get(document=doc).status, 'done')

    def test_empty_manual_review_does_not_claim_to_be_an_old_proposal(self):
        doc = self.document()
        doc.proposal = {}
        doc.ai_enabled = False
        doc.save()
        page = self.client.get(reverse('review', args=[doc.pk]))
        self.assertNotContains(page, 'Dieser ältere Vorschlag')
        self.assertContains(page, 'data-processing="0"')

    def test_actual_completed_legacy_proposal_remains_explicit_without_an_api_call_on_get(self):
        doc = self.document()
        doc.proposal.pop('entry')
        doc.save()
        page = self.client.get(reverse('review', args=[doc.pk]))
        self.assertContains(page, 'Dieser ältere Vorschlag')
        self.assertEqual(Job.objects.get(document=doc).status, 'done')

    def test_invalid_post_preserves_typed_values_while_worker_finishes(self):
        doc = self.document()
        Job.objects.filter(document=doc).update(status='pending', stage='ai')
        page = self.client.post(reverse('review', args=[doc.pk]), self.payload(
            doc, entry_title='Meine noch ungespeicherte Korrektur', gross_total='kein Betrag'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Meine noch ungespeicherte Korrektur')
        self.assertContains(page, 'data-preserve-input="1"')
        self.assertContains(page, 'data-processing="1"')
        self.assertEqual((CostSource.objects.count(), Entry.objects.count()), (0, 0))

    def test_accepted_or_deleted_document_is_never_reported_as_processing(self):
        doc = self.document()
        Job.objects.filter(document=doc).update(status='pending')
        doc.status = 'accepted'
        doc.save()
        url = reverse('document_status', args=[doc.pk])
        self.assertFalse(self.client.get(url).json()['processing'])
        doc.deleted_at = timezone.now()
        doc.save()
        self.assertEqual(self.client.get(url).json()['status'], 'trash')
        self.assertFalse(self.client.get(url).json()['processing'])

    def test_status_is_owner_scoped(self):
        doc = self.document()
        from django.contrib.auth import get_user_model
        other = get_user_model().objects.create_user('refresh-foreign')
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse('document_status', args=[doc.pk])).status_code, 404)
