import hashlib,io,json,tempfile,uuid,zipfile
from pathlib import Path
from datetime import date,timedelta
from decimal import Decimal
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase,override_settings,Client
from django.urls import reverse
from django.utils import timezone
from .models import *
from .services import *
from .adapters import demo_proposal,IntegrationError,UploadUncertain,Pending,LivePaperless
from .worker import run_one
from .demo import synthetic_pdf,synthetic_photo
from .exports import portable_export,verify_archive,report_pdf
from .backup import backup,restore

class DomainTests(TestCase):
    def setUp(self):
        testroot=settings.DATA_DIR/'test-temp';testroot.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=testroot);self.addCleanup(self.temp.cleanup)
        self.override=override_settings(MEDIA_ROOT=Path(self.temp.name)/'media',DATA_DIR=Path(self.temp.name),DEMO_MODE=True,ALLOWED_HOSTS=['testserver'])
        self.override.enable();self.addCleanup(self.override.disable);settings.MEDIA_ROOT.mkdir()
        self.user=get_user_model().objects.create_user('owner',password='local-test-password')
        self.other=get_user_model().objects.create_user('other',password='other-test-password')
        self.auto=Project.objects.create(owner=self.user,name='BMW – Demonstration',kind='auto',fields={'Öl':{'type':'text','value':'Demo 5W-40'}})
        self.aqua=Project.objects.create(owner=self.user,name='Aquarium',kind='aquarium')
        self.client.force_login(self.user)
        self.cases=json.loads((settings.BASE_DIR/'spezifikation/TESTFAELLE.json').read_text(encoding='utf8'))['cases']
    def entry(self,**kw):
        data={'title':'Ölwechsel','kind':'wartung','status':'erledigt','date':date(2026,11,12),'precision':'day','snapshot':{'oil':'Demo 5W-40','quantity_l':'5.0','filter':'DEMO-FILTER-01'},'meter':Decimal(165200),'cost_state':'known','manual_amount':Decimal('75.00')}
        data.update(kw);return save_entry(self.user,self.auto,data,uuid.uuid4())
    def document(self,text='DEMO-OEL-001',entry=None):
        doc,_=receive_document(self.user,SimpleUploadedFile('receipt.pdf',synthetic_pdf(text),content_type='application/pdf'),self.auto,entry)
        doc.paperless_id=int(doc.id.int%2_000_000_000)+1;doc.verified_at=timezone.now();doc.ocr=text;doc.status='review';doc.proposal=demo_proposal(text);doc.save();doc.job.status='done';doc.job.save()
        return doc
    def source(self,amount='105.00'): return CostSource.objects.create(owner=self.user,label='Test',amount=Decimal(amount))
    def test_F01_oil_idempotency_snapshot(self):
        case=self.cases[0];token=uuid.uuid4();e=self.entry();old=e.request_key
        repeated=save_entry(self.user,self.auto,{'title':'IGNORED'},old)
        self.auto.fields['Öl']['value']='Demo 0W-30';self.auto.save();e.refresh_from_db()
        self.assertEqual(repeated.pk,e.pk);self.assertEqual(Entry.objects.count(),case['expected']['chronicle_entry_count'])
        self.assertEqual(str(e.cost),case['expected']['entry_cost']);self.assertEqual(e.snapshot['oil'],case['expected']['historical_oil_specification'])
    def test_F02_rebuild_single_row_images_and_costs(self):
        case=self.cases[1];e=self.entry(title='Soundsystem-Umbau',kind='umbau',date=date(2026,11,1),end_date=date(2026,11,12),budget=Decimal('1400'),manual_amount=None)
        for i in range(5): Step.objects.create(entry=e,title=f'Schritt {i}',position=i)
        for i in range(12):
            asset=ingest_asset(self.user,SimpleUploadedFile(f'{i}.jpg',synthetic_photo(i+1)));thumbnail(asset);PhotoLink.objects.create(asset=asset,entry=e,position=i,caption=f'Bild {i}')
        for i,amount in enumerate(case['invoice_amounts']):
            doc=self.document(f'Audio-{i}',e);p={'gross_total':amount,'currency':'EUR','positions':[{'description':'Audio','amount':amount}],'invoice_number':f'A-{i}'}
            accept_document(self.user,doc,p,[{'project':self.auto,'entry':e,'amount':amount}],expected_version=doc.version)
        photo=e.photo_links.first();photo.deleted_at=timezone.now();photo.save();photo.deleted_at=None;photo.save()
        self.assertEqual(Entry.objects.count(),1);self.assertEqual(e.steps.count(),5);self.assertEqual(e.photo_links.count(),12)
        self.assertEqual(e.documents.count(),3);self.assertEqual(e.date,date(2026,11,1));self.assertEqual(e.cost,Decimal('1250'));self.assertEqual(e.remaining,Decimal('150'))
    def test_F03_connect_invoice_does_not_double_book(self):
        case=self.cases[2];e=self.entry();text=(settings.BASE_DIR/case['source_file'].replace('beispiele','spezifikation/beispiele')).read_text(encoding='utf8')
        doc=self.document(text,e);p=demo_proposal(text)
        for key in ('invoice_number','invoice_date','service_date','odometer_km','gross_total','net_total','tax_total','currency'): self.assertEqual(p[key],case['expected_extraction'][key])
        self.assertEqual(e.cost,Decimal('75'));self.assertFalse(CostSource.objects.filter(document=doc).exists())
        manual=CostSource.objects.get(owner=self.user)
        source=accept_document(self.user,doc,p,[],expected_version=doc.version,connect=manual)
        second=accept_document(self.user,doc,p,[],expected_version=doc.version)
        with doc.asset.file.open('rb') as f: original=f.read()
        again,new=receive_document(self.user,SimpleUploadedFile('again.pdf',original),self.auto,e)
        self.assertFalse(new);self.assertEqual(again.pk,doc.pk);self.assertEqual(second.pk,source.pk)
        self.assertEqual(CostSource.objects.count(),1);self.assertEqual(e.cost,Decimal('75'));self.assertEqual(e.date,date(2026,11,12));self.assertEqual(source.invoice_date,'2026-11-10')
    def test_F04_split_shipping_discount_credit(self):
        source=self.source();allocate(self.user,source,[{'project':self.auto,'amount':'63.00'},{'project':self.aqua,'amount':'42.00'}])
        credit=self.source('-5.00');allocate(self.user,credit,[{'project':self.auto,'amount':'-5.00'}])
        self.assertEqual(self.auto.cost,Decimal('58'));self.assertEqual(self.aqua.cost,Decimal('42'));self.assertEqual(source.remainder,0)
        self.assertEqual(self.auto.cost+self.aqua.cost,Decimal('100'))
    def test_F05_partial_overallocation_is_atomic(self):
        source=self.source();self.assertEqual(allocate(self.user,source,[{'project':self.auto,'amount':'60.00'}]),Decimal('45'))
        with self.assertRaisesMessage(ValidationError,'1.00'): allocate(self.user,source,[{'project':self.auto,'amount':'106.00'}])
        self.assertEqual(source.allocations.get().amount,Decimal('60'))
    def test_F06_unknown_free_known(self):
        unknown=self.entry(cost_state='unknown',manual_amount=None);free=self.entry(cost_state='free',manual_amount=Decimal(0));known=self.entry()
        response=self.client.get(reverse('project',args=[self.auto.pk]));self.assertContains(response,'Unbekannt');self.assertContains(response,'Kostenlos');self.assertContains(response,'75,00')
        self.assertEqual(self.auto.cost,Decimal('75'));self.assertFalse(unknown.allocations.exists());self.assertEqual(free.cost,0)
    def test_F07_date_precision_and_order(self):
        expected=self.cases[6]['expected'];entries=[]
        for item in self.cases[6]['entries']:
            dt,precision=parse_date(item['date']);entries.append(self.entry(title=item['id'],date=dt,precision=precision,manual_amount=None))
        self.assertEqual(list(Entry.objects.values_list('title',flat=True)),expected['descending_order'])
        self.assertEqual(entries[1].display_date,expected['month_display']);self.assertEqual(entries[2].display_date,expected['year_display'])
        with timezone.override('Pacific/Honolulu'): self.assertEqual(entries[0].date.isoformat(),'2026-11-12')
    def test_F08_outage_restart_preserves_upload_and_manual_work(self):
        doc,new=receive_document(self.user,SimpleUploadedFile('in.pdf',synthetic_pdf('DEMO-OEL-001')),self.auto)
        with patch('akte.worker.paperless',side_effect=IntegrationError('Paperless aus')): self.assertTrue(run_one())
        self.assertTrue(Path(doc.asset.file.path).is_file());e=self.entry();self.assertEqual(e.cost,Decimal('75'))
        job=doc.job;job.status='running';job.leased_until=timezone.now()-timedelta(seconds=1);job.available=timezone.now();job.save()
        for _ in range(5):
            Job.objects.filter(pk=job.pk).update(available=timezone.now());run_one()
        doc.refresh_from_db();self.assertEqual(doc.status,'review');self.assertTrue(doc.verified_at)
        with patch('akte.worker.ai',side_effect=IntegrationError('KI aus')):
            job.refresh_from_db();job.stage='ai';job.status='pending';job.save();run_one()
        doc.refresh_from_db();source=accept_document(self.user,doc,doc.proposal,[],expected_version=doc.version,connect=e.allocations.get().source)
        self.assertEqual(CostSource.objects.filter(document=doc).count(),1);self.assertEqual(e.cost,Decimal('75'))
    def test_ambiguous_upload_never_posts_twice(self):
        doc,_=receive_document(self.user,SimpleUploadedFile('ambiguous.pdf',synthetic_pdf('ambiguous')),self.auto)
        fake=type('Fake',(),{})();fake.upload=lambda d: (_ for _ in ()).throw(UploadUncertain('Unklar'))
        with patch('akte.worker.paperless',return_value=fake): run_one()
        doc.refresh_from_db();self.assertEqual(doc.status,'uncertain');self.assertTrue(doc.submitted_at)
        # Simulate crash after POST but before persisting response. Reconcile is the only next action.
        calls=[];fake.upload=lambda d:calls.append('BAD POST');fake.reconcile=lambda d:(calls.append('reconcile') or 333)
        job=doc.job;job.status='running';job.leased_until=timezone.now()-timedelta(seconds=1);job.save()
        with patch('akte.worker.paperless',return_value=fake): run_one()
        self.assertEqual(calls,['reconcile']);doc.refresh_from_db();self.assertEqual(doc.paperless_id,333)
    def test_AI_budget_denies_before_external_call(self):
        doc=self.document();job=doc.job;job.stage='ai';job.status='pending';job.save()
        with override_settings(DEMO_MODE=False,AI_MONTHLY_LIMIT=Decimal('0'),AI_INPUT_USD_PER_M=Decimal('1'),AI_OUTPUT_USD_PER_M=Decimal('1')):
            with patch('akte.worker.paperless',return_value=object()),patch('akte.worker.ai') as network:
                run_one();network.assert_not_called()
        doc.refresh_from_db();self.assertEqual(doc.status,'review');self.assertIn('budget',doc.error.lower())
    def test_untrusted_schema_and_inconsistent_sum_rejected(self):
        for p in [{'gross_total':'NaN'}, {'gross_total':'75.00','currency':'EUR','positions':[{'description':'Oil','amount':'76.00'}]}, {'run_shell':'delete all'}]:
            with self.assertRaises(ValidationError): validate_proposal(p)
        self.assertEqual(CostSource.objects.count(),0)
    def test_shared_photo_unlink_preserves_original(self):
        first=self.entry();second=self.entry();asset=ingest_asset(self.user,SimpleUploadedFile('shared.jpg',synthetic_photo(1)))
        a=PhotoLink.objects.create(entry=first,asset=asset);b=PhotoLink.objects.create(entry=second,asset=asset)
        self.client.post(reverse('photo_action',args=[first.pk]),{'link_id':a.pk,'action':'trash'})
        self.assertEqual(second.photo_links.count(),1);self.assertTrue(Path(asset.file.path).exists());self.assertEqual(Asset.objects.count(),1)
    def test_parts_snapshot_and_historical_meter(self):
        e=self.entry(meter=Decimal('150000'),meter_replaced=True)
        part=Part.objects.create(project=self.auto,name='Filter alt',number='OLD')
        self.client.post(reverse('part_event',args=[e.pk]),{'part':part.pk,'date':'2026-11-12','action':'eingebaut'})
        part.number='NEW';part.save();event=PartEvent.objects.get();self.assertEqual(event.snapshot['number'],'OLD');self.assertEqual(event.snapshot['meter'],'150000.000')
        self.assertTrue(e.meter_replaced)
    def test_edit_conflict_keeps_first_update(self):
        e=self.entry();data={'title':'Erste Änderung','snapshot':e.snapshot,'manual_amount':Decimal('75')}
        save_entry(self.user,self.auto,data,uuid.uuid4(),e,1)
        with self.assertRaises(Conflict): save_entry(self.user,self.auto,{'title':'Zweite Änderung'},uuid.uuid4(),e,1)
        e.refresh_from_db();self.assertEqual(e.title,'Erste Änderung')
    def test_search_OCR_and_step_and_caption_owner_scoped(self):
        e=self.entry(title='Test');doc=self.document('DEMO-FILTER-01',e)
        response=self.client.get(reverse('search'),{'q':'DEMO-FILTER-01'});self.assertContains(response,'Test');self.assertContains(response,reverse('review',args=[doc.pk]))
        self.client.force_login(self.other);self.assertNotContains(self.client.get(reverse('search'),{'q':'DEMO-FILTER-01'}),'Test</a>')
    def test_private_files_documents_exports_require_login_and_owner(self):
        doc=self.document();url=reverse('media',args=[doc.asset_id]);self.client.logout();self.assertEqual(self.client.get(url).status_code,302)
        self.client.force_login(self.other);self.assertEqual(self.client.get(url).status_code,404);self.assertEqual(self.client.get(reverse('review',args=[doc.pk])).status_code,404)
    def test_csrf_required_and_markdown_sanitized(self):
        c=Client(enforce_csrf_checks=True);c.force_login(self.user)
        self.assertEqual(c.post(reverse('project_new'),{'name':'CSRF'}).status_code,403)
        from .templatetags.akte import rich
        self.assertNotIn('<script',rich('<script>alert(1)</script>'));self.assertNotIn('javascript:',rich('[x](javascript:alert(1))'))
    def test_portable_export_has_originals_and_hashes(self):
        e=self.entry();doc=self.document(entry=e);out=Path(self.temp.name)/'export.zip';portable_export(out,self.user)
        verify_archive(out)
        with zipfile.ZipFile(out) as z:
            self.assertIn('daten.json',z.namelist());self.assertIn('index.html',z.namelist());self.assertTrue(any(n.startswith('belege/') and n.endswith('.pdf') for n in z.namelist()))
            self.assertNotIn(b'password',z.read('daten.json'));self.assertNotIn(b'GEMINI_API_KEY',z.read('daten.json'))
    def test_backup_restore_database_media_and_document_identity(self):
        # Test DB is in-memory; build a real isolated SQLite baseline for restore mechanics.
        import sqlite3
        self.entry();doc=self.document();db=settings.DATA_DIR/'db.sqlite3'
        c=sqlite3.connect(db);c.execute('CREATE TABLE proof(project,entry,document)');c.execute('INSERT INTO proof VALUES(?,?,?)',(str(self.auto.pk),'Oil',str(doc.paperless_id)));c.commit();c.close()
        archive=settings.DATA_DIR/'backup.zip';backup(archive);target=settings.DATA_DIR/'restored';restore(archive,target)
        c=sqlite3.connect(target/'db.sqlite3');self.assertEqual(c.execute('SELECT entry FROM proof').fetchone()[0],'Oil');c.close()
        self.assertTrue((target/'media'/doc.asset.file.name).is_file())
        with self.assertRaises(ValueError): restore(archive,target)
    def test_corrupt_archive_fails_before_extraction(self):
        bad=settings.DATA_DIR/'bad.zip'
        with zipfile.ZipFile(bad,'w') as z:
            z.writestr('db.sqlite3',b'changed');z.writestr('manifest.json',json.dumps({'sha256':{'db.sqlite3':'0000'}}))
        target=settings.DATA_DIR/'unchanged'
        with self.assertRaises(ValueError): restore(bad,target)
        self.assertFalse(target.exists())
    def test_draft_resume_conflict_no_confirmation(self):
        token=uuid.uuid4();url=reverse('draft_save',args=[self.auto.pk])
        r=self.client.post(url,{'token':token,'title':'Unvollständig','draft_version':0,'version':0});self.assertEqual(r.status_code,200);self.assertEqual(Entry.objects.count(),0)
        r=self.client.get(reverse('entry_new',args=[self.auto.pk]),{'draft':token});self.assertContains(r,'Unvollständig')
        self.assertEqual(self.client.post(url,{'token':token,'title':'stale','draft_version':0}).status_code,409)
    def test_import_repeated_confirm_is_idempotent(self):
        batch=ImportBatch.objects.create(owner=self.user,project=self.auto,sha256='a'*64,source_name='synthetic.csv',rows=[{'title':'Imported','date':'11/2026','km':'165.200','cost':'75,00'}])
        confirm_import(self.user,batch);confirm_import(self.user,batch)
        e=Entry.objects.get();self.assertEqual(e.meter,165200);self.assertEqual(e.precision,'month');self.assertEqual(e.cost,75)
    def test_german_numbers_and_non_finite_values(self):
        self.assertEqual(decimal_input('165.200'),165200);self.assertEqual(decimal_input('1.400,00',money=True),1400)
        for val in ['1,400.00','NaN','Infinity','12.3456']:
            with self.assertRaises(ValidationError): decimal_input(val,money=True)
    def test_all_core_GET_pages_render(self):
        e=self.entry();doc=self.document(entry=e)
        urls=[reverse('home'),reverse('project',args=[self.auto.pk]),reverse('entry',args=[e.pk]),reverse('entry_edit',args=[e.pk]),reverse('review',args=[doc.pk]),reverse('cost',args=[e.allocations.get().source_id]),reverse('tasks'),reverse('inbox'),reverse('system'),reverse('project_new'),reverse('project_edit',args=[self.auto.pk]),reverse('trash'),reverse('import',args=[self.auto.pk]),reverse('project_export',args=[self.auto.pk])]
        urls.extend(reverse('project',args=[self.auto.pk])+'?tab='+x for x in ['teile','dokumente','kosten','aufgaben','stammdaten','umbauten'])
        for url in urls:
            with self.subTest(url=url): self.assertEqual(self.client.get(url).status_code,200)
    def test_pdf_has_expected_text_and_real_pages(self):
        from pypdf import PdfReader
        e=self.entry();out=settings.DATA_DIR/'report.pdf';report_pdf(out,self.auto,[e]);r=PdfReader(out)
        text=''.join(p.extract_text() for p in r.pages)
        self.assertIn('Demo 5W-40',text);self.assertIn('DEMO-FILTER-01',text);self.assertIn('75,00 EUR',text)
    def test_money_is_integer_in_SQLite_and_aggregate_is_exact(self):
        from django.db import connection
        source=self.source('10.00')
        Allocation.objects.bulk_create([Allocation(source=source,project=self.auto,amount=Decimal('.01')) for _ in range(1000)])
        self.assertEqual(self.auto.cost,Decimal('10.00'))
        with connection.cursor() as c:
            c.execute('SELECT amount, typeof(amount) FROM akte_costsource WHERE id=%s',[source.pk.hex])
            self.assertEqual(c.fetchone(),(1000,'integer'))
    def test_HTTP_oil_form_and_invoice_double_accept(self):
        from django.forms.models import model_to_dict
        token=uuid.uuid4();url=reverse('entry_new',args=[self.auto.pk])
        payload={'token':token,'version':0,'title':'Ölwechsel','kind':'wartung','status':'erledigt','date_text':'12.11.2026','meter_text':'165.200','meter_name':'Kilometerzähler','meter_unit':'km','oil':'Demo 5W-40','quantity':'5,0','filter':'DEMO-FILTER-01','cost_state':'known','amount':'75,00'}
        self.assertEqual(self.client.post(url,payload).status_code,302);self.assertEqual(self.client.post(url,payload).status_code,302)
        e=Entry.objects.get();doc=self.document(entry=e);p=demo_proposal('DEMO-OEL-001')
        data={**p,'positions':json.dumps(p['positions']),'version':doc.version,'connect':e.allocations.get().source_id,'action':'accept'}
        data={k:('' if v is None else v) for k,v in data.items()}
        self.assertEqual(self.client.post(reverse('review',args=[doc.pk]),data).status_code,302)
        self.assertEqual(self.client.post(reverse('review',args=[doc.pk]),data).status_code,302)
        self.assertEqual(CostSource.objects.count(),1);self.assertEqual(e.cost,Decimal('75'))
    def test_review_autosave_preserves_corrections_across_AI_retry(self):
        doc=self.document();url=reverse('review_draft',args=[doc.pk])
        r=self.client.post(url,{'version':doc.version,'invoice_number':'MANUAL-CORRECTION','gross_total':'75,00','positions':'[]'})
        self.assertEqual(r.status_code,200)
        job=doc.job;job.stage='ai';job.status='pending';job.save();run_one()
        doc.refresh_from_db();self.assertEqual(doc.review_draft['fields']['invoice_number'],'MANUAL-CORRECTION')
        self.assertContains(self.client.get(reverse('review',args=[doc.pk])),'MANUAL-CORRECTION')
    def test_cost_edit_conflict_is_detected(self):
        e=self.entry();source=e.allocations.get().source
        payload={'version':source.version,'label':'Updated','amount':'75,00','currency':'EUR','alloc_project':self.auto.pk,'alloc_entry':e.pk,'alloc_amount':'75,00','alloc_ref':''}
        self.assertEqual(self.client.post(reverse('cost',args=[source.pk]),payload).status_code,302)
        self.assertContains(self.client.post(reverse('cost',args=[source.pk]),payload),'zwischenzeitlich')
    def test_move_step_keeps_photo_inside_target_rebuild(self):
        first=self.entry(kind='umbau');second=self.entry(kind='umbau',manual_amount=None)
        step=Step.objects.create(entry=first,title='Move',position=0)
        asset=ingest_asset(self.user,SimpleUploadedFile('move.jpg',synthetic_photo(1)))
        photo=PhotoLink.objects.create(entry=first,step=step,asset=asset)
        response=self.client.post(reverse('step_edit',args=[first.pk,step.pk]),{'version':first.version,'title':'Moved','position':2,'text':'Dokumentation','target':second.pk})
        self.assertEqual(response.status_code,302);step.refresh_from_db();photo.refresh_from_db()
        self.assertEqual(step.entry_id,second.pk);self.assertEqual(photo.entry_id,second.pk);self.assertEqual(Entry.objects.count(),2)
    def test_Paperless_adapter_contract_and_timeout(self):
        import httpx
        doc=self.document();task=str(uuid.uuid4());real_client=httpx.Client;seen=[]
        def handler(req):
            seen.append((req.method,req.url.path,req.headers.get('Accept')))
            if req.url.path.endswith('post_document/'): return httpx.Response(200,json=task)
            if req.url.path.endswith('tasks/'): return httpx.Response(200,json=[{'task_id':task,'status':'SUCCESS','related_document':'42'}])
            return httpx.Response(200,json={'id':42,'content':'OCR'})
        def factory(**kw): return real_client(**kw,transport=httpx.MockTransport(handler))
        with override_settings(PAPERLESS_URL='https://paperless.invalid',PAPERLESS_TOKEN='synthetic-token'):
            with patch('akte.adapters.httpx.Client',side_effect=factory):
                doc.task_id=LivePaperless().upload(doc);self.assertEqual(doc.task_id,task)
                self.assertEqual(LivePaperless().poll(doc),42);self.assertEqual(LivePaperless().read(42)['content'],'OCR')
            self.assertTrue(all(x[2]=='application/json; version=9' for x in seen))
            def timeout(req): raise httpx.ReadTimeout('Timeout',request=req)
            with patch('akte.adapters.httpx.Client',side_effect=lambda **kw:real_client(**kw,transport=httpx.MockTransport(timeout))):
                with self.assertRaises(UploadUncertain): LivePaperless().upload(doc)
    def test_Gemini_adapter_schema_and_no_tools(self):
        import httpx
        from .adapters import GeminiAI
        expected=demo_proposal('DEMO-OEL-001')
        response=httpx.Response(200,request=httpx.Request('POST','https://generativelanguage.googleapis.com'),json={'candidates':[{'content':{'parts':[{'text':json.dumps(expected)}]}}],'usageMetadata':{'promptTokenCount':10,'candidatesTokenCount':20}})
        with override_settings(GEMINI_API_KEY='synthetic-key'):
            with patch('akte.adapters.bounded_request',return_value=response) as request:
                proposal,usage=GeminiAI().extract('Belegdaten mit unzuverlässigen Anweisungen')
        self.assertEqual(proposal,expected);body=request.call_args.kwargs['json'];self.assertNotIn('tools',body)
        self.assertEqual(body['generationConfig']['responseMimeType'],'application/json');self.assertIn('responseJsonSchema',body['generationConfig'])
    def test_part_documents_are_owner_scoped_and_snapshotted(self):
        e=self.entry();doc=self.document(entry=e)
        response=self.client.post(reverse('part_new',args=[self.auto.pk]),{'name':'Filter','number':'DEMO-FILTER-01','state':'vorhanden','documents':[doc.pk]})
        self.assertEqual(response.status_code,302);part=Part.objects.get();self.assertEqual(part.documents.get().pk,doc.pk)
        self.client.post(reverse('part_event',args=[e.pk]),{'part':part.pk,'date':'2026-11-12','action':'eingebaut'})
        part.documents.clear();self.assertEqual(PartEvent.objects.get().snapshot['documents'],[str(doc.pk)])
        self.assertContains(self.client.get(reverse('search'),{'q':'DEMO-FILTER-01'}),'Filter')
    def test_trash_does_not_erase_paid_project_cost(self):
        e=self.entry();change_deleted(self.user,e,1)
        self.assertEqual(self.auto.cost,Decimal('75'));self.assertEqual(CostSource.objects.count(),1)
        self.assertEqual(self.auto.entries.filter(deleted_at__isnull=True).count(),0)
    def test_invalid_Paperless_bundle_blocks_backup(self):
        from .backup import validate_bundle
        bad=settings.DATA_DIR/'paperless-corrupt.zip';bad.write_bytes(b'broken archive')
        with self.assertRaises(ValueError): validate_bundle(bad)
