"""Capture now, add work photos immediately, review invoice later."""
import copy
import json
from unittest.mock import patch
import httpx
from django.test import TestCase, override_settings
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from . import tests_invoice_chronicle
from .adapters import GeminiAI
from .demo import synthetic_photo
from .models import Document, Entry, CostSource
from .proposal_schema import empty_entry

class ScanEntryFlowTests(TestCase):
    setUp = tests_invoice_chronicle.InvoiceChronicleTests.setUp
    document = tests_invoice_chronicle.InvoiceChronicleTests.document
    payload = tests_invoice_chronicle.InvoiceChronicleTests.payload

    def upload(self):
        response = self.client.post(reverse('document_upload'), {
            'camera': SimpleUploadedFile('SYN-Beleg.jpg', synthetic_photo(63)),
            'auto_route': '1', 'scan_note': 'Für mein Auto, bereits montiert'})
        self.assertEqual(response.status_code, 302)
        return Document.objects.get(), response

    def test_upload_immediately_creates_usable_entry_without_waiting_for_network(self):
        doc, response = self.upload()
        self.assertIsNotNone(doc.entry_id)
        self.assertRedirects(response, reverse('entry', args=[doc.entry_id]))
        self.assertTrue(doc.entry.is_provisional)
        self.assertEqual(CostSource.objects.count(), 0)
        self.assertIsNone(doc.paperless_id)

    @override_settings(DEMO_MODE=False, GEMINI_API_KEY='synthetic-fixture')
    def test_accounting_mismatch_keeps_ai_fields_for_later_review(self):
        proposal = {**copy.deepcopy(self.proposal), 'entry': {**empty_entry(), **self.proposal['entry']},
                    'positions': [{'description': 'Synthetischer Artikel', 'amount': '60.00'}],
                    'project_assignment': {'project_id': None, 'confidence': 'none', 'reason': None}}
        response = httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': json.dumps(proposal)}]}}]})
        with patch('akte.adapters.httpx.post', return_value=response):
            actual, _ = GeminiAI().extract('Synthetischer Beleg')
        self.assertEqual(actual['gross_total'], '75.00')
        self.assertEqual(actual['positions'][0]['amount'], '60.00')
        self.assertEqual(actual['entry']['title'], 'Ölwechsel')

    def ai_ready(self):
        from django.utils import timezone
        from .models import Job
        doc, _ = self.upload()
        doc.status = 'ai'; doc.paperless_id = 863; doc.verified_at = timezone.now(); doc.save()
        Job.objects.filter(kind='thumbnail').update(status='done')
        Job.objects.filter(document=doc).update(stage='ai', status='pending')
        return doc

    def process(self, proposal):
        from .worker import run_one
        with patch('akte.worker.ai') as adapter:
            adapter.return_value.extract.return_value = (proposal, {})
            self.assertTrue(run_one())

    def routed_proposal(self):
        result = copy.deepcopy(self.proposal)
        result['project_assignment'] = {'project_id': str(self.project.pk), 'confidence': 'high', 'reason': 'Passendes Auto'}
        return result

    def test_repeated_identical_upload_keeps_exactly_one_entry(self):
        first, _ = self.upload(); second, _ = self.upload()
        self.assertEqual(first.entry_id, second.entry_id)
        self.assertEqual((Document.objects.count(), Entry.objects.count()), (1, 1))
        self.assertTrue(first.ai_with_original)
        self.assertContains(self.client.get(reverse('home')), reverse('entry', args=[first.entry_id]))

    def test_steps_and_photos_work_before_any_ai_or_project_assignment(self):
        from .models import Step, PhotoLink
        doc, _ = self.upload(); entry = doc.entry
        page = self.client.get(reverse('step_new', args=[entry.pk]))
        self.assertEqual(page.status_code, 200)
        self.client.post(reverse('step_new', args=[entry.pk]), {'version': entry.version, 'title': 'Montage', 'position': 0})
        step = Step.objects.get(entry=entry)
        self.client.post(reverse('photo_action', args=[entry.pk]), {
            'photos': SimpleUploadedFile('SYN-Arbeit.jpg', synthetic_photo(64)), 'step': str(step.pk), 'position': 0})
        photo = PhotoLink.objects.get(entry=entry)
        self.assertEqual(photo.step_id, step.pk)
        self.assertTrue(photo.asset.file.storage.exists(photo.asset.file.name))
        self.assertEqual(CostSource.objects.count(), 0)

    def test_ai_routes_same_entry_with_steps_and_photos_and_preserves_human_title(self):
        from .models import Step, PhotoLink, Asset
        from .files import ingest_asset
        doc = self.ai_ready(); original_entry = doc.entry_id
        step = Step.objects.create(entry=doc.entry, title='Eigener Montageschritt')
        asset = ingest_asset(self.user, SimpleUploadedFile('SYN-work.jpg', synthetic_photo(65)))
        photo = PhotoLink.objects.create(entry=doc.entry, step=step, asset=asset)
        doc.entry.title = 'Meine eigene Beschreibung'; doc.entry.save(update_fields=['title'])
        self.process(self.routed_proposal()); doc.refresh_from_db(); photo.refresh_from_db()
        self.assertEqual(doc.entry_id, original_entry)
        self.assertEqual(doc.entry.project_id, self.project.pk)
        self.assertEqual(doc.entry.title, 'Meine eigene Beschreibung')
        self.assertEqual(doc.entry.kind, 'wartung')
        self.assertEqual(photo.step_id, step.pk)
        self.assertEqual(photo.entry_id, original_entry)
        self.assertEqual(CostSource.objects.count(), 0)

    def test_failed_ai_keeps_usable_entry_and_original(self):
        from .worker import run_one
        from .adapters import IntegrationError
        doc = self.ai_ready(); entry_id = doc.entry_id
        with patch('akte.worker.ai') as adapter:
            adapter.return_value.extract.side_effect = IntegrationError('Synthetischer Ausfall')
            run_one()
        doc.refresh_from_db()
        self.assertEqual(doc.entry_id, entry_id)
        self.assertTrue(doc.asset.file.storage.exists(doc.asset.file.name))
        self.assertEqual(self.client.get(reverse('entry', args=[entry_id])).status_code, 200)
        self.assertEqual(CostSource.objects.count(), 0)

    def test_warning_is_stored_without_discarding_proposal_or_retrying_ai(self):
        from .models import Job
        from .billing import accept_document
        from django.core.exceptions import ValidationError
        doc = self.ai_ready(); proposal = self.routed_proposal()
        proposal['positions'] = [{'description': 'SYN-Teil', 'amount': '60.00'}]
        self.process(proposal); doc.refresh_from_db()
        self.assertEqual(doc.proposal['gross_total'], '75.00')
        self.assertEqual(doc.entry.title, 'Ölwechsel')
        self.assertIn('Positionssumme', doc.error)
        self.assertEqual(Job.objects.get(document=doc).status, 'done')
        with self.assertRaises(ValidationError):
            accept_document(self.user, doc, doc.proposal, [], expected_version=doc.version)
        self.assertEqual(CostSource.objects.count(), 0)
        self.assertContains(self.client.get(reverse('review', args=[doc.pk])), '75,00')

    def test_later_confirmation_reuses_entry_and_books_once(self):
        from .models import PhotoLink
        from .files import ingest_asset
        doc = self.ai_ready(); entry_id = doc.entry_id
        asset = ingest_asset(self.user, SimpleUploadedFile('SYN-photo.jpg', synthetic_photo(66)))
        photo = PhotoLink.objects.create(entry=doc.entry, asset=asset)
        self.process(self.routed_proposal()); doc.refresh_from_db()
        payload = self.payload(doc, chronicle_mode='existing', chronicle_entry=str(entry_id), entry_title='Später geprüft')
        self.assertEqual(self.client.post(reverse('review', args=[doc.pk]), payload).status_code, 302)
        doc.refresh_from_db()
        self.assertEqual(doc.status, 'accepted')
        self.assertEqual(doc.entry_id, entry_id)
        self.assertEqual(doc.entry.title, 'Später geprüft')
        self.assertFalse(doc.entry.is_provisional)
        self.assertEqual(doc.entry.cost, 75)
        self.assertTrue(PhotoLink.objects.filter(pk=photo.pk, entry_id=entry_id).exists())
        self.client.post(reverse('review', args=[doc.pk]), payload)
        self.assertEqual((Entry.objects.count(), CostSource.objects.count()), (1, 1))

    def test_inbox_is_not_an_ai_candidate_or_a_financial_target(self):
        from .project_routing import ai_context
        from .forms import ProjectRoutingForm
        from .billing import allocate
        from django.core.exceptions import ValidationError
        doc, _ = self.upload()
        self.assertTrue(doc.entry.project.is_scan_inbox)
        self.assertNotIn(str(doc.entry.project_id), [item['id'] for item in ai_context(doc)['routing_projects']])
        self.assertNotIn(doc.entry.project, ProjectRoutingForm(owner=self.user).fields['project'].queryset)
        source = CostSource.objects.create(owner=self.user, label='SYN', amount=10)
        with self.assertRaises(ValidationError):
            allocate(self.user, source, [{'project': doc.entry.project, 'amount': '10'}])

    def test_manual_route_keeps_entry_id_and_blocks_late_ai_reroute(self):
        from .project_routing import assign_project
        from .models import Project
        from .worker import run_one
        doc = self.ai_ready(); target = Project.objects.create(owner=self.user, name='Mein manuelles Ziel')
        def extract(*args, **kwargs):
            assign_project(self.user, doc, target, doc.version)
            return self.routed_proposal(), {}
        with patch('akte.worker.ai') as adapter:
            adapter.return_value.extract.side_effect = extract
            run_one()
        doc.refresh_from_db()
        self.assertEqual(doc.project_id, target.pk)
        self.assertEqual(doc.entry.project_id, target.pk)
        self.assertEqual(doc.routing['status'], 'manual')

    def test_worker_does_not_resurrect_a_deleted_provisional_entry(self):
        from django.utils import timezone
        doc = self.ai_ready(); doc.entry.deleted_at = timezone.now(); doc.entry.save(update_fields=['deleted_at'])
        self.process(self.routed_proposal()); doc.refresh_from_db()
        self.assertIsNotNone(doc.entry.deleted_at)
        self.assertIsNone(doc.project_id)
        self.assertEqual(CostSource.objects.count(), 0)

    def test_entry_creation_failure_rolls_back_document_and_new_original(self):
        from django.db import DatabaseError
        from .files import receive_document
        from .models import Asset
        with patch('akte.scan_entries.Entry.objects.get_or_create', side_effect=DatabaseError('SYN failure')):
            with self.assertRaises(DatabaseError):
                receive_document(self.user, SimpleUploadedFile('SYN-fail.jpg', synthetic_photo(67)), create_entry=True)
        self.assertEqual((Document.objects.count(), Entry.objects.count(), Asset.objects.count()), (0, 0, 0))
        self.assertEqual(list((self.data/'media'/'originals').glob('*')), [])

    def test_numeric_round_trip_still_allows_reanalysis_of_untouched_meter(self):
        from .scan_entries import apply_scan_proposal
        doc = self.ai_ready(); self.process(self.routed_proposal()); doc.refresh_from_db()
        proposal = self.routed_proposal(); proposal['entry']['meter'] = '165201'
        apply_scan_proposal(doc, proposal); doc.entry.refresh_from_db()
        self.assertEqual(doc.entry.meter, 165201)

    def test_gross_unit_price_mismatch_is_reviewable_but_not_bookable(self):
        from .billing import validate_proposal, proposal_warnings
        from django.core.exceptions import ValidationError
        proposal = copy.deepcopy(self.proposal)
        proposal['positions'] = [{'description': 'SYN', 'quantity': '2', 'unit_price': '40', 'price_basis': 'gross', 'amount': '75.00'}]
        validate_proposal(proposal, check_totals=False)
        self.assertIn('Einzelpreis', ' '.join(proposal_warnings(proposal)))
        with self.assertRaises(ValidationError): validate_proposal(proposal, final=True)

    def test_new_step_can_be_added_when_ai_updated_entry_metadata_during_typing(self):
        from .models import Step
        doc = self.ai_ready(); typed_at_version = doc.entry.version
        self.process(self.routed_proposal())
        response = self.client.post(reverse('step_new', args=[doc.entry_id]), {
            'version': typed_at_version, 'title': 'Während KI ergänzt', 'position': 0})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Step.objects.filter(entry_id=doc.entry_id, title='Während KI ergänzt').exists())

    def test_provisional_entry_fields_are_visible_in_later_review(self):
        doc = self.ai_ready(); self.process(self.routed_proposal()); doc.refresh_from_db()
        page = self.client.get(reverse('review', args=[doc.pk]))
        self.assertContains(page, 'name="entry_title"')
        self.assertContains(page, 'Diesen Eintrag prüfen und behalten')
        self.assertNotContains(page, 'Ausdrücklich nur als Dokument ablegen')

    def test_trashing_invoice_keeps_work_entry_reachable_and_does_not_remove_photos(self):
        from .document_trash import change_document_deleted
        doc, _ = self.upload()
        change_document_deleted(self.user, doc, doc.version)
        home = self.client.get(reverse('home'))
        self.assertContains(home, reverse('entry', args=[doc.entry_id]))
        page = self.client.get(reverse('entry', args=[doc.entry_id]))
        self.assertContains(page, 'Beleg im Papierkorb')
        self.assertContains(page, 'name="photos"')
