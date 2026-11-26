"""Permanent trash removal and safe repeat of the same receipt."""
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, Client, override_settings
from .purging import source_fingerprint
from django.urls import reverse
from django.utils import timezone
from django.core.exceptions import ValidationError
from . import tests_invoice_chronicle
from .models import AIUsage, Document, Entry, PhotoLink, CostSource, Job
from .document_trash import change_document_deleted

class PurgeTests(TestCase):
    setUp = tests_invoice_chronicle.InvoiceChronicleTests.setUp
    document = tests_invoice_chronicle.InvoiceChronicleTests.document

    def trashed_doc(self):
        doc=self.document()
        return change_document_deleted(self.user,doc,doc.version)

    def test_permanent_action_is_available_in_trash(self):
        self.trashed_doc()
        self.assertContains(self.client.get(reverse('trash')),'Endgültig löschen')

    def test_removing_document_must_not_erase_ai_spending(self):
        doc=self.trashed_doc()
        usage=AIUsage.objects.create(owner=self.user,document=doc,estimated_usd=Decimal('.05'),model='synthetic')
        doc.delete()
        self.assertTrue(AIUsage.objects.filter(pk=usage.pk).exists())

    def test_get_confirmation_never_deletes_and_post_needs_explicit_confirmation(self):
        doc=self.trashed_doc();url=reverse('purge',args=['document',doc.pk])
        self.assertEqual(self.client.get(url).status_code,200)
        self.assertEqual(self.client.post(url,{'version':doc.version}).status_code,400)
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())

    def test_csrf_and_owner_are_required(self):
        from django.contrib.auth import get_user_model
        doc=self.trashed_doc();url=reverse('purge',args=['document',doc.pk])
        secure=Client(enforce_csrf_checks=True);secure.force_login(self.user)
        self.assertEqual(secure.post(url,{'confirmed':'1','version':doc.version}).status_code,403)
        other=get_user_model().objects.create_user('purge-outsider');self.client.force_login(other)
        self.assertEqual(self.client.get(url).status_code,404)
        self.assertEqual(self.client.post(url,{'confirmed':'1','version':doc.version}).status_code,404)

    def test_active_or_restored_records_cannot_be_purged(self):
        from .purging import purge_item
        doc=self.document()
        with self.assertRaises(ValidationError):purge_item(self.user,doc,doc.version)
        doc=change_document_deleted(self.user,doc,doc.version);old=doc.version
        change_document_deleted(self.user,doc,doc.version,restore=True)
        with self.assertRaises(ValidationError):purge_item(self.user,doc,old)

    def test_unreferenced_original_removed_only_after_successful_commit(self):
        from .purging import purge_item
        from .models import Asset,ArchiveReference
        doc=self.trashed_doc();file=doc.asset.file.path;asset_id=doc.asset_id
        from pathlib import Path
        with self.captureOnCommitCallbacks(execute=True):purge_item(self.user,doc,doc.version)
        self.assertFalse(Document.objects.filter(pk=doc.pk).exists())
        self.assertFalse(Asset.objects.filter(pk=asset_id).exists())
        self.assertFalse(Path(file).exists())
        self.assertEqual(ArchiveReference.objects.get().paperless_id,701)

    def test_database_error_retains_document_files_and_costs(self):
        from .purging import purge_item
        from django.db import DatabaseError
        from pathlib import Path
        doc=self.trashed_doc();file=doc.asset.file.path
        with patch('akte.purging._remove_unused_files',side_effect=DatabaseError('SYN database failure')):
            with self.assertRaises(DatabaseError):purge_item(self.user,doc,doc.version)
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())
        self.assertTrue(Path(file).exists())

    def test_same_file_can_be_uploaded_again_and_reuses_archived_identity(self):
        from .purging import purge_item
        from .services import receive_document
        from django.core.files.uploadedfile import SimpleUploadedFile
        doc=self.trashed_doc();old_id=doc.pk
        with doc.asset.file.open('rb') as stream:raw=stream.read()
        with self.captureOnCommitCallbacks(execute=True):purge_item(self.user,doc,doc.version)
        repeat,new=receive_document(self.user,SimpleUploadedFile('SYN-repeat.pdf',raw),self.project)
        self.assertTrue(new);self.assertNotEqual(repeat.pk,old_id)
        self.assertEqual(repeat.paperless_id,701)
        self.assertEqual(repeat.job.stage,'reuse')
        self.assertEqual(Document.objects.count(),1)

    def test_reuse_reads_archive_without_another_upload(self):
        from .purging import purge_item
        from .services import receive_document
        from .worker import run_one
        from django.core.files.uploadedfile import SimpleUploadedFile
        doc=self.trashed_doc()
        with doc.asset.file.open('rb') as stream:raw=stream.read()
        purge_item(self.user,doc,doc.version)
        repeat,_=receive_document(self.user,SimpleUploadedFile('repeat.pdf',raw))
        with patch('akte.worker.paperless') as adapter:
            adapter.return_value.read.return_value={'id':701,'content':'SYN OCR','modified':'same'}
            run_one();adapter.return_value.upload.assert_not_called()
        repeat.refresh_from_db()
        self.assertEqual(repeat.ocr,'SYN OCR');self.assertIsNotNone(repeat.verified_at)
        self.assertEqual(repeat.job.stage,'ai')

    def test_changed_archive_original_is_blocked_without_reupload(self):
        from .purging import purge_item
        from .services import receive_document
        from .worker import run_one
        from django.test import override_settings
        from django.core.files.uploadedfile import SimpleUploadedFile
        doc=self.trashed_doc()
        with doc.asset.file.open('rb') as stream:raw=stream.read()
        purge_item(self.user,doc,doc.version)
        repeat,_=receive_document(self.user,SimpleUploadedFile('repeat.pdf',raw))
        with override_settings(DEMO_MODE=False),patch('akte.worker.paperless') as adapter:
            adapter.return_value.read.return_value={'id':701,'content':'SYN OCR'}
            adapter.return_value.original_sha256.return_value='0'*64
            run_one();adapter.return_value.upload.assert_not_called()
        repeat.refresh_from_db()
        self.assertEqual(repeat.status,'uncertain')
        self.assertEqual(repeat.job.status,'blocked')
        self.assertIsNone(repeat.verified_at)

    def test_archive_change_during_reuse_is_not_silently_accepted(self):
        from .purging import purge_item
        from .services import receive_document
        from .worker import run_one
        from django.core.files.uploadedfile import SimpleUploadedFile
        doc=self.trashed_doc()
        with doc.asset.file.open('rb') as stream:raw=stream.read()
        purge_item(self.user,doc,doc.version)
        repeat,_=receive_document(self.user,SimpleUploadedFile('repeat.pdf',raw))
        with patch('akte.worker.paperless') as adapter:
            adapter.return_value.read.side_effect=[{'id':701,'content':'before'},{'id':701,'content':'after'}]
            run_one()
        repeat.refresh_from_db();self.assertEqual(repeat.status,'uncertain')

    def test_pending_previous_upload_waits_without_submitting_twice(self):
        from .purging import purge_item
        from .services import receive_document
        from .worker import run_one
        from .adapters import Pending
        from django.core.files.uploadedfile import SimpleUploadedFile
        doc=self.trashed_doc();doc.paperless_id=None;doc.submitted_at=timezone.now();doc.save()
        with doc.asset.file.open('rb') as stream:raw=stream.read()
        purge_item(self.user,doc,doc.version)
        repeat,_=receive_document(self.user,SimpleUploadedFile('repeat.pdf',raw))
        with patch('akte.worker.paperless') as adapter:
            adapter.return_value.reconcile.side_effect=Pending('SYN previous upload processing')
            run_one();adapter.return_value.upload.assert_not_called()
        self.assertEqual(Job.objects.get(document=repeat).status,'pending')

    @override_settings(AI_INPUT_USD_PER_M=Decimal('0.3'),AI_OUTPUT_USD_PER_M=Decimal('2.5'))
    def test_late_ai_usage_and_failure_after_purge_do_not_crash_or_reset_budget(self):
        from .purging import purge_item
        from .worker import record_usage,fail,commit_job
        from .adapters import IntegrationError
        doc=self.trashed_doc();job=doc.job
        usage=AIUsage.objects.create(owner=self.user,document=doc,estimated_usd='.05',model='SYN')
        purge_item(self.user,doc,doc.version)
        record_usage(usage,{'promptTokenCount':100,'totalTokenCount':150})
        usage.refresh_from_db();self.assertIsNone(usage.document_id);self.assertEqual(usage.state,'complete')
        self.assertGreater(usage.estimated_usd,0)
        fail(job,IntegrationError('SYN late failure'))
        self.assertFalse(commit_job(job,{'status':'review'}))
        self.assertEqual(Document.objects.count(),0)

    def booked_doc(self):
        from .billing import accept_document
        doc=self.document()
        source=accept_document(self.user,doc,doc.proposal,[{'project':self.project,'amount':'75'}],expected_version=doc.version)
        doc.refresh_from_db();doc=change_document_deleted(self.user,doc,doc.version)
        return doc,source

    def test_booked_costs_require_separate_confirmation_and_current_version(self):
        from .purging import purge_item
        doc,source=self.booked_doc()
        with self.assertRaises(ValidationError):purge_item(self.user,doc,doc.version)
        with self.assertRaises(ValidationError):purge_item(self.user,doc,doc.version,remove_costs=True,cost_version=source.version-1)
        self.assertEqual(self.project.cost,75)
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())
        page=self.client.get(reverse('purge',args=['document',doc.pk]))
        self.assertContains(page,'name="remove_costs"');self.assertContains(page,'75')

    def test_explicitly_removing_test_invoice_also_removes_its_cost_source_once(self):
        from .purging import purge_item
        doc,source=self.booked_doc()
        purge_item(self.user,doc,doc.version,remove_costs=True,cost_version=source.version,cost_fingerprint=source_fingerprint(source))
        self.assertEqual(self.project.cost,0)
        self.assertFalse(CostSource.objects.filter(pk=source.pk).exists())

    def test_shared_file_survives_until_last_reference_is_removed(self):
        from .purging import purge_item
        from .models import Asset
        from pathlib import Path
        doc=self.trashed_doc();asset_id=doc.asset_id;filename=doc.asset.file.path
        entry=Entry.objects.create(project=self.project,title='SYN shared')
        photo=PhotoLink.objects.create(entry=entry,asset=doc.asset,deleted_at=timezone.now())
        with self.captureOnCommitCallbacks(execute=True):purge_item(self.user,doc,doc.version)
        self.assertTrue(Asset.objects.filter(pk=asset_id).exists());self.assertTrue(Path(filename).exists())
        with self.captureOnCommitCallbacks(execute=True):purge_item(self.user,photo,photo.version)
        self.assertFalse(Asset.objects.filter(pk=asset_id).exists());self.assertFalse(Path(filename).exists())

    def test_purging_work_entry_retains_costs_on_project_and_detaches_document(self):
        from .purging import purge_item
        from .billing import accept_document
        entry=Entry.objects.create(project=self.project,title='SYN',deleted_at=timezone.now())
        doc=self.document();doc.entry=entry;doc.save()
        source=accept_document(self.user,doc,doc.proposal,[{'project':self.project,'amount':'75'}],expected_version=doc.version)
        # Attach an allocation before trashing the work entry; amounts survive deletion.
        source.allocations.update(entry=entry)
        purge_item(self.user,entry,entry.version)
        doc.refresh_from_db();self.assertIsNone(doc.entry_id)
        self.assertEqual(self.project.cost,75);self.assertEqual(source.allocations.count(),1)
        self.assertIsNone(source.allocations.get().entry_id)

    def test_work_photos_and_inbox_remain_reachable_after_invoice_is_purged(self):
        from .purging import purge_item
        from .services import receive_document
        from .demo import synthetic_pdf
        from django.core.files.uploadedfile import SimpleUploadedFile
        doc,_=receive_document(self.user,SimpleUploadedFile('SYN.pdf',synthetic_pdf('SYN inbox')),create_entry=True)
        entry=doc.entry;doc=change_document_deleted(self.user,doc,doc.version)
        purge_item(self.user,doc,doc.version)
        entry.refresh_from_db();self.assertFalse(entry.is_provisional)
        self.assertContains(self.client.get(reverse('entry',args=[entry.pk])),'Beleg wurde endgültig entfernt')
        self.assertContains(self.client.get(reverse('home')),reverse('project',args=[entry.project_id]))

    def test_changed_allocations_require_a_new_confirmation_even_without_a_version_bump(self):
        from .purging import purge_item
        doc,source=self.booked_doc();before=source_fingerprint(source)
        source.allocations.update(amount='74')
        with self.assertRaises(ValidationError):
            purge_item(self.user,doc,doc.version,remove_costs=True,cost_version=source.version,cost_fingerprint=before)
        self.assertTrue(CostSource.objects.filter(pk=source.pk).exists())

    @override_settings(DEMO_MODE=False)
    def test_live_reuse_verifies_original_checksum_before_accepting_ocr(self):
        from .purging import purge_item
        from .services import receive_document
        from .worker import run_one
        from django.core.files.uploadedfile import SimpleUploadedFile
        doc=self.trashed_doc()
        with doc.asset.file.open('rb') as stream:raw=stream.read()
        purge_item(self.user,doc,doc.version)
        repeat,_=receive_document(self.user,SimpleUploadedFile('repeat.pdf',raw))
        with patch('akte.worker.paperless') as adapter:
            adapter.return_value.read.return_value={'id':701,'content':'SYN OCR'}
            adapter.return_value.original_sha256.return_value=repeat.asset.sha256
            run_one();adapter.return_value.original_sha256.assert_called_once_with(701)
            adapter.return_value.upload.assert_not_called()
        repeat.refresh_from_db();self.assertIsNotNone(repeat.verified_at)

    def test_unconfirmed_upload_can_be_removed_and_started_fresh(self):
        from .purging import purge_item
        from .services import receive_document
        from .models import ArchiveReference
        from django.core.files.uploadedfile import SimpleUploadedFile
        doc=self.trashed_doc();doc.paperless_id=None;doc.verified_at=None;doc.save()
        with doc.asset.file.open('rb') as stream:raw=stream.read()
        purge_item(self.user,doc,doc.version)
        repeat,_=receive_document(self.user,SimpleUploadedFile('repeat.pdf',raw))
        self.assertEqual(ArchiveReference.objects.count(),0)
        self.assertEqual(repeat.job.stage,'upload')

    def test_original_checksum_is_streamed_and_bounded(self):
        import hashlib,httpx
        from .adapters import LivePaperless,IntegrationError
        with override_settings(PAPERLESS_URL='https://paperless.invalid',PAPERLESS_TOKEN='SYN'):
            adapter=LivePaperless()
            def handle(request):
                self.assertEqual(request.url.params['original'],'true')
                return httpx.Response(200,content=b'synthetic original')
            adapter._client=lambda: httpx.Client(base_url='https://paperless.invalid/',transport=httpx.MockTransport(handle))
            self.assertEqual(adapter.original_sha256(701),hashlib.sha256(b'synthetic original').hexdigest())
            with override_settings(MAX_DOCUMENT_BYTES=3),self.assertRaises(IntegrationError):
                adapter.original_sha256(701)

    def test_preflight_file_error_does_not_reserve_a_gemini_call_that_never_started(self):
        from .worker import run_one
        doc=self.document();doc.ai_with_original=True;doc.save()
        Job.objects.filter(document=doc).update(stage='ai',status='pending')
        with patch('akte.worker.original_for_ai',side_effect=OSError('SYN file unavailable')),patch('akte.worker.reserve_ai') as reserve:
            run_one();reserve.assert_not_called()
        self.assertEqual(AIUsage.objects.count(),0)

    def test_finishing_export_does_not_crash_worker_after_its_entry_is_purged(self):
        from .models import ExportRequest
        from .purging import purge_item
        from .worker import with_export_error
        entry=Entry.objects.create(project=self.project,title='SYN export',deleted_at=timezone.now())
        export=ExportRequest.objects.create(owner=self.user,project=self.project,entry=entry)
        job=Job.objects.create(export=export,kind='export',stage='export',status='running')
        purge_item(self.user,entry,entry.version)
        self.assertFalse(Job.objects.filter(pk=job.pk).exists())
        with_export_error(job)
