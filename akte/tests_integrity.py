"""Regression tests for races and integrity failures reported during the core audit."""
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .adapters import IntegrationError
from .demo import synthetic_pdf
from .models import Asset, CostSource, Document, Job, Project
from .services import accept_document, ingest_asset, receive_document
from .worker import reserve, run_one


class IntegrityRegressionTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.media = self.data / 'media'
        self.media.mkdir()
        override = override_settings(DATA_DIR=self.data, MEDIA_ROOT=self.media, DEMO_MODE=True)
        override.enable()
        self.addCleanup(override.disable)
        self.owner = get_user_model().objects.create_user('audit-owner')
        self.project = Project.objects.create(owner=self.owner, name='Synthetic audit')
        self.proposal = {'gross_total': '75.00', 'currency': 'EUR', 'positions': []}

    def prepared(self):
        doc, _ = receive_document(self.owner, SimpleUploadedFile('audit.pdf', synthetic_pdf('audit')), self.project)
        doc.paperless_id = 42
        doc.verified_at = timezone.now()
        doc.status = 'review'
        doc.save()
        Job.objects.filter(document=doc).update(stage='ocr')
        return doc

    def accept(self, doc):
        return accept_document(self.owner, doc, self.proposal,
                               [{'project': self.project, 'amount': '75.00'}],
                               expected_version=doc.version)

    def test_accepted_invoice_survives_late_worker_error(self):
        doc = self.prepared()
        def read(*args):
            self.accept(doc)
            raise IntegrationError('Timeout after explicit acceptance')
        adapter = type('Adapter', (), {'read': staticmethod(read)})()
        with patch('akte.worker.paperless', return_value=adapter):
            run_one()
        doc.refresh_from_db()
        self.assertEqual(doc.status, 'accepted')
        self.assertEqual(CostSource.objects.count(), 1)

    def test_accepted_invoice_survives_late_worker_result(self):
        doc = self.prepared()
        def read(*args):
            self.accept(doc)
            return {'id': 42, 'content': 'Late OCR result'}
        adapter = type('Adapter', (), {'read': staticmethod(read)})()
        with patch('akte.worker.paperless', return_value=adapter):
            run_one()
        doc.refresh_from_db()
        self.assertEqual(doc.status, 'accepted')

    def test_worker_with_replaced_lease_cannot_apply_result(self):
        doc = self.prepared()
        def read(*args):
            Job.objects.filter(document=doc).update(lease_token=uuid.uuid4())
            return {'id': 42, 'content': 'Obsolete result'}
        adapter = type('Adapter', (), {'read': staticmethod(read)})()
        with patch('akte.worker.paperless', return_value=adapter):
            run_one()
        doc.refresh_from_db()
        self.assertNotEqual(doc.ocr, 'Obsolete result')

    def test_reservations_are_globally_serial(self):
        self.prepared()
        second = Document.objects.create(owner=self.owner, instance='demo', paperless_id=43)
        Job.objects.create(document=second)
        self.assertIsNotNone(reserve())
        self.assertIsNone(reserve())

    def test_database_failure_after_file_rename_removes_original(self):
        with patch('akte.files.Asset.objects.create', side_effect=DatabaseError('injected')):
            with self.assertRaises(DatabaseError):
                ingest_asset(self.owner, SimpleUploadedFile('failed.pdf', synthetic_pdf('failed')), document=True)
        self.assertEqual(list((self.media / 'originals').glob('*')), [])
        self.assertEqual(list((self.media / 'incoming').glob('*')), [])

    def test_allocation_rows_must_have_matching_lengths(self):
        self.client.force_login(self.owner)
        payload = {'token': uuid.uuid4(), 'label': 'Malformed allocation', 'amount': '75,00',
                   'currency': 'EUR', 'alloc_project': [str(self.project.pk)],
                   'alloc_amount': ['75,00', '10,00']}
        response = self.client.post(reverse('cost_new', args=[self.project.pk]), payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CostSource.objects.exists())

    def test_receive_failure_cleans_unacknowledged_file(self):
        with patch('akte.files.Document.objects.get_or_create',side_effect=DatabaseError('injected')):
            with self.assertRaises(DatabaseError):
                receive_document(self.owner,SimpleUploadedFile('failure.pdf',synthetic_pdf('failure')),self.project)
        self.assertFalse(Asset.objects.exists())
        self.assertFalse(list((self.media/'originals').glob('*')))

    def test_interrupted_upload_cleans_temporary_file(self):
        class Interrupted:
            name='interrupted.pdf'
            def chunks(self):
                yield b'%PDF-'
                raise OSError('connection interrupted')
        with self.assertRaises(OSError):
            ingest_asset(self.owner,Interrupted(),document=True)
        self.assertFalse(Asset.objects.exists())
        self.assertFalse(list((self.media/'incoming').glob('*')))

    def test_position_references_signs_and_limits(self):
        from .billing import allocate
        from django.core.exceptions import ValidationError
        source=CostSource.objects.create(owner=self.owner,amount=Decimal('105'),positions=[
            {'description':'Part A','amount':'60'}, {'description':'Part B','amount':'40'},
            {'description':'Shipping','amount':'10'}, {'description':'Discount','amount':'-5'}])
        rows=[{'project':self.project,'amount':amount,'position_ref':str(i)}
              for i,amount in enumerate(['60','40','10','-5'],1)]
        self.assertEqual(allocate(self.owner,source,rows),0)
        for invalid in ('5','unknown'):
            with self.assertRaises(ValidationError):
                allocate(self.owner,source,[{'project':self.project,'amount':'1','position_ref':invalid}])
        with self.assertRaises(ValidationError):
            allocate(self.owner,source,[{'project':self.project,'amount':'61','position_ref':'1'}])
        self.assertEqual(source.allocations.count(),4)

    def test_changed_and_missing_remote_do_not_rebook_or_discard_edits(self):
        from .documents import remote_signature,sync_document
        from .adapters import DocumentMissing
        doc=self.prepared();self.accept(doc)
        original={'id':42,'content':'old','modified':'2026-11-12T00:00:00Z'}
        doc.refresh_from_db();doc.remote_signature=remote_signature(original);doc.edits={'vendor':'checked'};doc.save()
        fake=type('Adapter',(),{})()
        fake.read=lambda *args:{**original,'content':'changed'}
        with patch('akte.documents.paperless',return_value=fake):
            changed=sync_document(doc)
        self.assertEqual(changed.remote_state,'changed');self.assertEqual(changed.status,'accepted')
        def missing(*args): raise DocumentMissing('gone')
        fake.read=missing
        with patch('akte.documents.paperless',return_value=fake):
            missing_doc=sync_document(doc)
        self.assertEqual(missing_doc.remote_state,'missing');self.assertEqual(missing_doc.edits,{'vendor':'checked'})
        self.assertEqual(CostSource.objects.count(),1)

    def test_invalid_schema_values_are_validation_errors(self):
        from .billing import validate_proposal
        from django.core.exceptions import ValidationError
        for proposal in ({'currency':12},{'positions':[{'description':'x','amount':'1','part_number':{}}]},
                         {'positions':[{'description':'x','amount':'1','run_shell':'bad'}]}):
            with self.assertRaises(ValidationError): validate_proposal(proposal)

    def test_missing_usage_metadata_keeps_budget_reservation(self):
        from .models import AIUsage
        from .worker import record_usage
        usage=AIUsage.objects.create(owner=self.owner,document=self.prepared(),estimated_usd=Decimal('.05'),model='synthetic')
        record_usage(usage,{'promptTokenCount':1})
        usage.refresh_from_db()
        self.assertEqual(usage.estimated_usd,Decimal('.05'));self.assertEqual(usage.state,'reserved')

    def test_multi_page_bill_is_one_document_and_keeps_sources(self):
        from .files import receive_bundle
        from .demo import synthetic_photo
        from .exports import portable_export
        from pypdf import PdfReader
        import zipfile,io
        image=synthetic_photo(2);pdf=synthetic_pdf('second page')
        def uploads(): return [SimpleUploadedFile('first.jpg',image),SimpleUploadedFile('second.pdf',pdf)]
        first,new=receive_bundle(self.owner,uploads(),self.project)
        second,new_again=receive_bundle(self.owner,uploads(),self.project)
        self.assertTrue(new);self.assertFalse(new_again);self.assertEqual(first.pk,second.pk)
        self.assertEqual(first.pages.count(),2);self.assertEqual(len(PdfReader(first.asset.file.path).pages),2)
        archive=self.data/'bundle.zip';portable_export(archive,self.owner)
        with zipfile.ZipFile(archive) as z:
            for page in first.pages.all(): self.assertIn('medien/'+page.asset.file.name,z.namelist())

    def test_pdf_import_never_invents_dates_costs_and_retains_source(self):
        from .imports import parse_source
        from .models import ImportBatch
        raw=synthetic_pdf('BMW Synthetic service, unclear date and costs')
        rows=parse_source('synthetic.pdf',raw)
        self.assertEqual(rows[0]['date'],'');self.assertEqual(rows[0]['cost'],'')
        self.client.force_login(self.owner)
        response=self.client.post(reverse('import',args=[self.project.pk]),{'source':SimpleUploadedFile('synthetic.pdf',raw)})
        self.assertEqual(response.status_code,302)
        batch=ImportBatch.objects.get()
        self.assertEqual(Path(batch.source_asset.file.path).read_bytes(),raw)

    def test_shared_document_has_both_entry_links(self):
        from .models import Entry
        a=Entry.objects.create(project=self.project,title='A',kind='notiz')
        other=Project.objects.create(owner=self.owner,name='Other linked project')
        b=Entry.objects.create(project=other,title='B',kind='notiz')
        raw=synthetic_pdf('shared')
        first,_=receive_document(self.owner,SimpleUploadedFile('same.pdf',raw),self.project,a)
        second,new=receive_document(self.owner,SimpleUploadedFile('same.pdf',raw),other,b)
        self.assertFalse(new);self.assertEqual(first.pk,second.pk)
        self.assertEqual(a.linked_documents.get().pk,b.linked_documents.get().pk)
        self.client.force_login(self.owner)
        response=self.client.get(reverse('project',args=[other.pk]))
        self.assertEqual(response.context['pending'],1)

    def test_selected_parts_are_once_only_and_do_not_confirm_install(self):
        from .documents import create_parts
        from .models import Part,PartEvent
        doc=self.prepared();self.proposal['positions']=[{'description':'Filter','part_number':'DEMO-01','amount':'75'}]
        self.accept(doc)
        create_parts(doc,self.project,['0']);create_parts(doc,self.project,['0'])
        self.assertEqual(Part.objects.count(),1);self.assertEqual(Part.objects.get().state,'vorhanden')
        self.assertFalse(PartEvent.objects.exists())

    def test_zero_counter_survives_edit_form(self):
        from .models import Entry
        e=Entry.objects.create(project=self.project,title='Zero',kind='wartung',meter=0)
        self.client.force_login(self.owner)
        response=self.client.get(reverse('entry_edit',args=[e.pk]))
        self.assertEqual(response.context['form'].initial['meter_text'],'0,000')

    def test_repeated_photo_submit_does_not_duplicate_links(self):
        from .demo import synthetic_photo
        from .models import Entry,PhotoLink
        self.client.force_login(self.owner)
        e=Entry.objects.create(project=self.project,title='Photo',kind='umbau')
        raw=synthetic_photo(4);token=uuid.uuid4()
        for unused in range(2):
            response=self.client.post(reverse('photo_action',args=[e.pk]),{'token':token,'photos':SimpleUploadedFile('same.jpg',raw)})
            self.assertEqual(response.status_code,302)
        self.assertEqual(PhotoLink.objects.count(),1)

    def test_cost_nonce_replay_does_not_edit_existing_source(self):
        self.client.force_login(self.owner)
        token=uuid.uuid4();url=reverse('cost_new',args=[self.project.pk])
        base={'token':token,'label':'First','amount':'75,00','currency':'EUR'}
        self.client.post(url,base)
        self.client.post(url,{**base,'label':'Replay','amount':'150,00'})
        source=CostSource.objects.get();self.assertEqual(source.amount,75);self.assertEqual(source.label,'First')

    def test_late_entry_draft_cannot_recreate_confirmed_entry(self):
        from .models import Draft,Entry
        self.client.force_login(self.owner)
        token=uuid.uuid4()
        e=Entry.objects.create(project=self.project,title='Confirmed',kind='notiz',request_key=token)
        response=self.client.post(reverse('draft_save',args=[self.project.pk]),{'token':token,'version':0,'title':'Late'})
        self.assertEqual(response.status_code,409);self.assertFalse(Draft.objects.exists())

    def test_review_draft_does_not_invalidate_explicit_confirmation(self):
        self.client.force_login(self.owner)
        doc=self.prepared();version=doc.version
        response=self.client.post(reverse('review_draft',args=[doc.pk]),{'version':version,'draft_version':0,'vendor':'Draft'})
        self.assertEqual(response.status_code,200)
        doc.refresh_from_db();self.assertEqual(doc.version,version);self.assertEqual(doc.review_version,1)
        self.accept(doc);doc.refresh_from_db()
        self.assertEqual(doc.status,'accepted')
        response=self.client.post(reverse('review_draft',args=[doc.pk]),{'version':version,'draft_version':1,'vendor':'Late'})
        self.assertEqual(response.status_code,409)

    def test_remote_ack_requires_explicit_comparison_and_keeps_money(self):
        from .documents import acknowledge_remote
        from django.core.exceptions import ValidationError
        doc=self.prepared();self.accept(doc);doc.refresh_from_db()
        fake=type('Adapter',(),{'read':lambda *args:{'id':42,'content':'changed metadata'}})()
        with patch('akte.documents.paperless',return_value=fake):
            with self.assertRaises(ValidationError): acknowledge_remote(doc,False)
            result=acknowledge_remote(doc,True)
        self.assertEqual(result.status,'accepted');self.assertEqual(result.source.amount,75)
        self.assertEqual(result.remote_state,'current')

    def test_uncertain_upload_can_link_checked_existing_id_without_post(self):
        from .documents import schedule_document
        from django.core.exceptions import ValidationError
        doc=self.prepared();doc.paperless_id=None;doc.status='uncertain';doc.save()
        Job.objects.filter(document=doc).update(status='blocked')
        fake=type('Adapter',(),{'read':lambda *args:{'id':99,'content':'checked'}})()
        with patch('akte.documents.paperless',return_value=fake):
            with self.assertRaises(ValidationError): schedule_document(doc,'link',existing_id=99)
            result=schedule_document(doc,'link',existing_id=99,identity_checked=True)
        self.assertEqual(result.paperless_id,99);self.assertEqual(result.job.stage,'ocr')

    def test_cleanup_retains_import_and_page_sources_but_removes_old_orphan(self):
        import os,io
        from django.core.management import call_command
        from .models import ImportBatch
        from .files import ingest_asset
        asset=ingest_asset(self.owner,SimpleUploadedFile('source.csv',b'title;date\nSynthetic;2026\n'),document=True,import_source=True)
        ImportBatch.objects.create(owner=self.owner,project=self.project,sha256=asset.sha256,source_name='source.csv',source_asset=asset)
        from datetime import timedelta
        Asset.objects.filter(pk=asset.pk).update(created=timezone.now()-timedelta(days=40))
        orphan=self.media/'originals'/'old-orphan.pdf';orphan.write_bytes(b'%PDF-old-unacknowledged')
        old=(timezone.now()-timedelta(days=40)).timestamp();os.utime(orphan,(old,old))
        call_command('cleanup',apply=True,stdout=io.StringIO())
        self.assertFalse(orphan.exists());self.assertTrue(Path(asset.file.path).exists())

    def test_search_index_tracks_bulk_updates_shared_ocr_and_unlinks(self):
        from .models import Entry,Step,DocumentLink
        from .views import search_entries
        from .files import link_document
        e=Entry.objects.create(project=self.project,title='Indexed',kind='umbau')
        step=Step.objects.create(entry=e,title='Hidden step',text='UNIQUE-SEARCH-TOKEN')
        self.assertTrue(search_entries(Entry.objects.all(),'UNIQUE-SEARCH-TOKEN').filter(pk=e.pk).exists())
        Step.objects.filter(pk=step.pk).update(text='OTHER-TOKEN')
        self.assertFalse(search_entries(Entry.objects.all(),'UNIQUE-SEARCH-TOKEN').filter(pk=e.pk).exists())
        doc=self.prepared();Document.objects.filter(pk=doc.pk).update(ocr='LINKED-OCR-TOKEN')
        link_document(doc,self.project,e)
        self.assertTrue(search_entries(Entry.objects.all(),'LINKED-OCR-TOKEN').filter(pk=e.pk).exists())
        DocumentLink.objects.filter(document=doc,entry=e).delete()
        self.assertFalse(search_entries(Entry.objects.all(),'LINKED-OCR-TOKEN').filter(pk=e.pk).exists())

    def test_stale_export_lease_cannot_publish_file_or_completion(self):
        from .models import ExportRequest
        from .exports import perform_export
        from datetime import timedelta
        export=ExportRequest.objects.create(owner=self.owner,project=self.project,kind='zip')
        job=Job.objects.create(export=export,kind='export',status='running',lease_token=uuid.uuid4(),leased_until=timezone.now()+timedelta(minutes=1))
        Job.objects.filter(pk=job.pk).update(lease_token=uuid.uuid4())
        self.assertFalse(perform_export(export,job=job))
        export.refresh_from_db();self.assertEqual(export.status,'pending')
        self.assertFalse(list((self.data/'exports').glob('*')))

    def test_bmw_layout_keeps_empty_values_and_normalizes_short_year(self):
        from .imports import parse_bmw_layout
        def line(a,b,c,d,e): return f'{a:<15}{b:<13}{c:<30}{d:<41}{e}'
        pages=['Fahrzeug: BMW SYNTHETISCH\nMotoröl: Testöl',
               line('Datum','km-','Arbeit / Maßnahme','Details & Teilenummern','Kosten / Ort')+'\n'+
               line('24.01.26','161.000','Thermostat','DEMO-01','(selbst)')+'\n'+line('','','','','35€')+'\n'+
               line('06.12.2025','160.000','Ölwechsel','Öl: Test 5W-40 Filter TEST-01','(selbst)')]
        result=parse_bmw_layout(pages)
        self.assertEqual(len(result['rows']),2)
        self.assertEqual(result['rows'][0]['date'],'2026-01-24');self.assertEqual(result['rows'][0]['cost'],'35')
        self.assertEqual(result['rows'][1]['cost'],'');self.assertEqual(result['rows'][1]['snapshot']['filter'],'TEST-01')
        self.assertNotIn('quantity_l',result['rows'][1]['snapshot'])

    def test_structured_import_fields_are_reviewed_and_confirmed_once(self):
        import json
        from .models import Entry,ImportBatch
        self.client.force_login(self.owner)
        data={'rows':[{'title':'Synthetic BMW maintenance','date':'2026-01-24','km':'161.000','cost':'35','source_page':2}],
              'project_fields':{'Motoröl':{'type':'text','value':'Synthetic oil'}},'warnings':['Review source']}
        response=self.client.post(reverse('import',args=[self.project.pk]),{'source':SimpleUploadedFile('review.json',json.dumps(data).encode())})
        self.assertEqual(response.status_code,302)
        batch=ImportBatch.objects.get()
        payload={'rows':json.dumps(batch.rows),'apply_fields':'1','project_version':self.project.version,
                 'importfields-TOTAL_FORMS':1,'importfields-INITIAL_FORMS':1,
                 'importfields-0-original_name':'','importfields-0-name':'Motoröl','importfields-0-kind':'text',
                 'importfields-0-value':'Reviewed oil','importfields-0-active':'on'}
        url=reverse('import_batch',args=[self.project.pk,batch.pk])
        self.assertEqual(self.client.post(url,payload).status_code,302)
        self.assertEqual(self.client.post(url,payload).status_code,302)
        self.project.refresh_from_db();self.assertEqual(self.project.fields['Motoröl']['value'],'Reviewed oil')
        self.assertEqual(Entry.objects.count(),1);self.assertEqual(self.project.cost,35)
        self.assertEqual(Entry.objects.get().snapshot['source_page'],2)

    def test_successful_export_finishes_job_and_unblocks_next_job(self):
        from .models import ExportRequest
        first=ExportRequest.objects.create(owner=self.owner,project=self.project,kind='zip')
        second=ExportRequest.objects.create(owner=self.owner,project=self.project,kind='zip')
        job1=Job.objects.create(kind='export',export=first)
        job2=Job.objects.create(kind='export',export=second)
        run_one()
        first.refresh_from_db();job1.refresh_from_db()
        self.assertEqual(first.status,'done')
        self.assertEqual(job1.status,'done')
        self.assertEqual(reserve().pk,job2.pk)

    def test_paperless_soft_deleted_document_is_missing_without_reversing_acceptance(self):
        from .documents import sync_document,acknowledge_remote
        from .adapters import DocumentMissing
        doc=self.prepared();self.accept(doc);doc.refresh_from_db()
        fake=type('Adapter',(),{'read':lambda *args:{'id':42,'content':'retained OCR','deleted_at':'2026-11-12T00:00:00Z'}})()
        with patch('akte.documents.paperless',return_value=fake):
            result=sync_document(doc)
            with self.assertRaises(DocumentMissing): acknowledge_remote(result,True)
        self.assertEqual(result.remote_state,'missing');self.assertEqual(result.status,'accepted')
        self.assertEqual(result.source.amount,75)

    def test_eur_entry_form_does_not_change_foreign_currency_expense(self):
        from .models import Entry,Allocation
        from .services import save_entry
        entry=Entry.objects.create(project=self.project,title='USD purchase',kind='wartung',cost_state='known',snapshot={'synthetic':True})
        source=CostSource.objects.create(owner=self.owner,label='USD source',amount=Decimal('100'),currency='USD')
        Allocation.objects.create(source=source,project=self.project,entry=entry,amount=Decimal('100'))
        save_entry(self.owner,self.project,{'title':'Text changed','manual_amount':Decimal('0')},uuid.uuid4(),entry,entry.version)
        source.refresh_from_db();self.assertEqual(source.amount,100);self.assertEqual(source.currency,'USD')
        self.assertEqual(source.allocations.get().amount,100)

    def test_connect_revalidates_preserved_allocations(self):
        from .models import Entry,Allocation
        from django.core.exceptions import ValidationError
        doc=self.prepared()
        unrelated=Project.objects.create(owner=self.owner,name='Other')
        entry=Entry.objects.create(project=unrelated,title='Wrong project',kind='wartung')
        source=CostSource.objects.create(owner=self.owner,label='Corrupted old allocation',amount=Decimal('75'))
        Allocation.objects.create(source=source,project=self.project,entry=entry,amount=Decimal('75'))
        with self.assertRaises(ValidationError):
            accept_document(self.owner,doc,self.proposal,[],expected_version=doc.version,connect=source)
        source.refresh_from_db();self.assertIsNone(source.document_id)
        doc.refresh_from_db();self.assertEqual(doc.status,'review')

    def test_large_pixel_header_is_rejected_without_scheduling_decode(self):
        import io,struct,zlib
        from PIL import Image
        from django.core.exceptions import ValidationError
        stream=io.BytesIO();Image.new('RGB',(1,1)).save(stream,format='PNG')
        raw=bytearray(stream.getvalue());raw[16:24]=struct.pack('>II',8000,5001)
        raw[29:33]=struct.pack('>I',zlib.crc32(raw[12:29]))
        with self.assertRaises(ValidationError):
            ingest_asset(self.owner,SimpleUploadedFile('oversized-header.png',bytes(raw)))
        self.assertFalse(Asset.objects.exists());self.assertFalse(Job.objects.exists())
        self.assertFalse(list((self.media/'incoming').glob('*')))

    def test_pdf_limits_abort_instead_of_silently_truncating(self):
        from .models import Entry,PhotoLink
        from .exports import report_pdf
        entry=Entry.objects.create(project=self.project,title='Bounded report',kind='umbau')
        asset=Asset.objects.create(owner=self.owner,file='originals/missing.jpg',sha256='f'*64,name='missing.jpg',mime='image/jpeg',size=1)
        PhotoLink.objects.bulk_create([PhotoLink(entry=entry,asset=asset) for unused in range(3)])
        with override_settings(PDF_MAX_PHOTOS=2):
            with self.assertRaisesMessage(IntegrationError,'PDF-Umfang'):
                report_pdf(self.data/'too-large.pdf',self.project,[entry])
        self.assertFalse((self.data/'too-large.pdf').exists())

    def test_pdf_rejects_entry_from_another_project(self):
        from .models import Entry
        from .exports import report_pdf
        from django.core.exceptions import ValidationError
        other=Project.objects.create(owner=self.owner,name='Other report scope')
        entry=Entry.objects.create(project=other,title='Wrong scope',kind='notiz')
        with self.assertRaises(ValidationError): report_pdf(self.data/'foreign.pdf',self.project,[entry])

    def test_pdf_rotates_actual_photo_and_releases_scratch_files(self):
        from .models import Entry,PhotoLink
        from .demo import synthetic_photo
        from .exports import report_pdf
        from pypdf import PdfReader
        entry=Entry.objects.create(project=self.project,title='Rotated photo',kind='umbau')
        asset=ingest_asset(self.owner,SimpleUploadedFile('real-image.jpg',synthetic_photo(1)))
        PhotoLink.objects.create(entry=entry,asset=asset,rotation=90)
        output=self.data/'rotated.pdf';report_pdf(output,self.project,[entry])
        images=[image for page in PdfReader(output).pages for image in page.images]
        self.assertTrue(images)
        from PIL import Image
        import io
        with Image.open(io.BytesIO(synthetic_photo(1))) as original:
            self.assertEqual(images[0].image.size,(original.height,original.width))
        self.assertFalse(list(self.data.glob('pdf-images-*')))

    def test_definite_auth_rejection_can_retry_but_uncertain_upload_cannot(self):
        import httpx
        doc=self.prepared();doc.paperless_id=None;doc.verified_at=None;doc.instance='https://paperless.invalid';doc.save()
        Job.objects.filter(document=doc).update(stage='upload')
        real_client=httpx.Client;calls=[];task=str(uuid.uuid4())
        def handler(request):
            calls.append(request.method)
            return httpx.Response(401) if len(calls)==1 else httpx.Response(200,json=task)
        with override_settings(DEMO_MODE=False,PAPERLESS_URL='https://paperless.invalid',PAPERLESS_TOKEN='synthetic-invalid-then-fixed'):
            with patch('akte.adapters.httpx.Client',side_effect=lambda **kw:real_client(**kw,transport=httpx.MockTransport(handler))):
                run_one();doc.refresh_from_db();self.assertIsNone(doc.submitted_at)
                Job.objects.filter(document=doc).update(available=timezone.now())
                run_one()
        doc.refresh_from_db();self.assertEqual(doc.task_id,task);self.assertEqual(calls,['POST','POST'])

    def test_interruption_after_db_commit_retains_referenced_original(self):
        from contextlib import contextmanager
        from .operations import operation_gate
        @contextmanager
        def interrupted_after_commit():
            with operation_gate(): yield
            raise OSError('interruption after completed database transaction')
        with patch('akte.files.operation_gate',interrupted_after_commit):
            with self.assertRaises(OSError):
                ingest_asset(self.owner,SimpleUploadedFile('committed.pdf',synthetic_pdf('committed')),document=True)
        asset=Asset.objects.get()
        self.assertTrue(Path(asset.file.path).is_file())

    def test_invalid_expert_positions_cannot_break_saved_review(self):
        self.client.force_login(self.owner)
        doc=self.prepared()
        response=self.client.post(reverse('review',args=[doc.pk]),{'version':doc.version,'gross_total':'75,00','currency':'EUR','positions_editor':'expert','positions':'[1]','action':'save'})
        self.assertEqual(response.status_code,200)
        doc.refresh_from_db();self.assertEqual(doc.edits,{})
        self.assertEqual(self.client.get(reverse('review',args=[doc.pk])).status_code,200)
        doc.edits={'positions':[1]};doc.save()
        response=self.client.get(reverse('review',args=[doc.pk]))
        self.assertEqual(response.status_code,200);self.assertContains(response,'Positionsdaten sind ungültig')

    def test_A03_invoice_100_split_60_40_is_not_added_twice(self):
        doc=self.prepared()
        other=Project.objects.create(owner=self.owner,name='Aquarium',kind='aquarium')
        proposal={'gross_total':'100.00','currency':'EUR','positions':[{'description':'Auto','amount':'60.00'},{'description':'Aquarium','amount':'40.00'}]}
        rows=[{'project':self.project,'amount':'60.00','position_ref':'1'},{'project':other,'amount':'40.00','position_ref':'2'}]
        first=accept_document(self.owner,doc,proposal,rows,expected_version=doc.version)
        again=accept_document(self.owner,doc,proposal,rows,expected_version=doc.version)
        self.assertEqual(first.pk,again.pk);self.assertEqual(CostSource.objects.count(),1)
        self.assertEqual(self.project.cost,Decimal('60'));self.assertEqual(other.cost,Decimal('40'))
        self.assertEqual(self.project.cost+other.cost,Decimal('100'))

    def test_explicit_entry_pdf_keeps_selected_trash_entry(self):
        from .models import Entry,ExportRequest
        from .exports import perform_export
        from pypdf import PdfReader
        entry=Entry.objects.create(project=self.project,title='Papierkorb-Export',kind='notiz',deleted_at=timezone.now())
        export=ExportRequest.objects.create(owner=self.owner,project=self.project,entry=entry,kind='pdf')
        self.assertTrue(perform_export(export))
        export.refresh_from_db()
        text=''.join(page.extract_text() for page in PdfReader(self.data/export.path).pages)
        self.assertIn(entry.title,text)
