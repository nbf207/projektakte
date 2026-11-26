import base64,json,tempfile,uuid,zipfile
from pathlib import Path
from datetime import date
from decimal import Decimal
from unittest.mock import patch
import httpx
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase,override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Project,Entry,Document,Job,CostSource,Allocation,Part,PartEvent,Task,Draft,PhotoLink
from .services import receive_document,accept_document,ingest_asset,allocate
from .demo import synthetic_pdf,synthetic_photo
from .exports import portable_export,verify_archive
from .adapters import GeminiAI,IntegrationError,original_for_ai,demo_proposal
from .worker import run_one,reserve_ai

class ReviewRegressionTests(TestCase):
    def setUp(self):
        root=settings.DATA_DIR/'test-temp';root.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=root);self.addCleanup(self.temp.cleanup)
        self.override=override_settings(DATA_DIR=Path(self.temp.name),MEDIA_ROOT=Path(self.temp.name)/'media',DEMO_MODE=True,ALLOWED_HOSTS=['testserver'])
        self.override.enable();self.addCleanup(self.override.disable);settings.MEDIA_ROOT.mkdir()
        self.user=get_user_model().objects.create_user('review',password='synthetic-test-password')
        self.client.force_login(self.user)
        self.auto=Project.objects.create(owner=self.user,name='Auto',kind='auto',fields={'Öl':{'type':'text','value':'Demo 5W-40'},'Alt':{'type':'text','value':'Historisch'}})
        self.aqua=Project.objects.create(owner=self.user,name='Aquarium',kind='aquarium')

    def entry(self,title,project=None):
        return Entry.objects.create(project=project or self.auto,title=title,kind='wartung',date=date(2026,11,12),snapshot={'oil':'Demo 5W-40'},cost_state='unknown')

    def doc(self,entry,number,amount='75.00'):
        d,_=receive_document(self.user,SimpleUploadedFile(number+'.pdf',synthetic_pdf(number)),entry.project,entry)
        d.paperless_id=int(d.id.int%2_000_000_000)+1;d.verified_at=timezone.now();d.status='review'
        d.ocr=number;d.proposal={'invoice_number':number,'invoice_date':'2026-11-10','currency':'EUR','gross_total':amount,'positions':[{'description':number,'part_number':'DEMO-01','amount':amount}]};d.save()
        Job.objects.filter(document=d).update(status='done')
        return d

    def confirm(self,doc,rows):
        return accept_document(self.user,doc,doc.proposal,rows,expected_version=doc.version)

    def export(self,**kwargs):
        file=settings.DATA_DIR/(str(uuid.uuid4())+'.zip');portable_export(file,self.user,**kwargs);verify_archive(file)
        with zipfile.ZipFile(file) as z:
            return json.loads(z.read('daten.json')),z.namelist(),z.read('index.html').decode()

    def records(self,data,model): return [r for r in data['objects'] if r['model']=='akte.'+model]

    def test_single_entry_export_excludes_unrelated_project_context_and_files(self):
        a=self.entry('Exportierter Ölwechsel');b=self.entry('Fremder Umbau')
        da=self.doc(a,'Beleg-A');db=self.doc(b,'UNBETEILIGTER-BELEG')
        self.confirm(da,[{'project':self.auto,'entry':a,'amount':'75.00'}]);self.confirm(db,[{'project':self.auto,'entry':b,'amount':'75.00'}])
        part=Part.objects.create(project=self.auto,name='Historisch benötigtes Teil');part.documents.add(db)
        PartEvent.objects.create(part=part,entry=a,action='eingebaut',date=date(2026,11,12),snapshot={'documents':[str(da.pk)]})
        other_part=Part.objects.create(project=self.auto,name='Unbeteiligtes Teil');other_part.documents.add(db)
        for n,e in enumerate([a,b],1):
            asset=ingest_asset(self.user,SimpleUploadedFile(f'{n}.jpg',synthetic_photo(n)))
            PhotoLink.objects.create(entry=e,asset=asset,caption=e.title)
            Task.objects.create(project=self.auto,entry=e,title=e.title)
            Draft.objects.create(owner=self.user,project=self.auto,entry=e,payload={'title':e.title})
        Draft.objects.create(owner=self.user,project=self.auto,payload={'title':'Ungebundener Projektentwurf'})
        data,names,index=self.export(project=self.auto,entry=a)
        for model in ['entry','document','costsource','allocation','part','partevent','task','draft','photolink']:
            self.assertEqual(len(self.records(data,model)),1,model)
        self.assertEqual(self.records(data,'entry')[0]['pk'],str(a.pk));self.assertEqual(self.records(data,'document')[0]['pk'],str(da.pk))
        self.assertNotIn('medien/'+db.asset.file.name,names)
        self.assertFalse(any(name.startswith(f'belege/{db.pk}') for name in names))
        self.assertNotIn('UNBETEILIGTER-BELEG',index);self.assertNotIn('Fremder Umbau',json.dumps(data))
        self.assertEqual(self.records(data,'part')[0]['fields']['documents'],[])

    def test_shared_invoice_keeps_original_and_explicit_outside_context(self):
        a=self.entry('Ölwechsel');b=self.entry('Aquarium-Arbeit',self.aqua)
        shared=self.doc(b,'GETEILT','105.00')
        source=self.confirm(shared,[{'project':self.auto,'entry':a,'amount':'63.00'},{'project':self.aqua,'entry':b,'amount':'42.00'}])
        unrelated=self.doc(b,'NICHT-GETEILT','20.00');self.confirm(unrelated,[{'project':self.aqua,'entry':b,'amount':'20.00'}])
        data,names,index=self.export(entry=a)
        self.assertEqual(len(self.records(data,'entry')),1);self.assertEqual(len(self.records(data,'document')),1)
        self.assertEqual(len(self.records(data,'allocation')),1);self.assertEqual(self.records(data,'document')[0]['fields']['entry'],None)
        self.assertEqual(data['source_context'],[{'source_id':str(source.pk),'currency':'EUR','amount':'105.00','allocated_in_scope':'63.00','allocated_outside_scope':'42.00','unallocated_total':'0.00'}])
        self.assertTrue(any(name.startswith(f'belege/{shared.pk}') for name in names))
        self.assertFalse(any(name.startswith(f'belege/{unrelated.pk}') for name in names))
        self.assertIn('42.00',index);self.assertEqual(data['scope']['kind'],'entry')

    def test_project_and_full_exports_have_different_boundaries(self):
        a=self.entry('Auto-Arbeit');b=self.entry('Aquarium-Arbeit',self.aqua)
        da=self.doc(a,'AUTO');db=self.doc(b,'AQUARIUM')
        self.confirm(da,[{'project':self.auto,'entry':a,'amount':'75.00'}]);self.confirm(db,[{'project':self.aqua,'entry':b,'amount':'75.00'}])
        project,_,_=self.export(project=self.auto);full,_,_=self.export()
        self.assertEqual(len(self.records(project,'document')),1);self.assertEqual(len(self.records(full,'document')),2)
        self.assertEqual(project['scope']['kind'],'project');self.assertEqual(full['scope']['kind'],'all')
        with self.assertRaises(ValidationError): self.export(project=self.aqua,entry=a)

    def position_payload(self,doc,**extra):
        return {'version':doc.version,'invoice_number':doc.proposal['invoice_number'],'gross_total':doc.proposal['gross_total'].replace('.',','),'currency':'EUR','positions_editor':'rows','positions-TOTAL_FORMS':'1','positions-INITIAL_FORMS':'1','positions-MIN_NUM_FORMS':'0','positions-MAX_NUM_FORMS':'300','positions-0-description':'Korrigiertes Öl','positions-0-part_number':'FILTER-NEU','positions-0-amount':'75,00','action':'accept',**extra}

    def test_regular_position_editor_accepts_and_allocates_to_suggested_entry(self):
        e=self.entry('Ölwechsel');doc=self.doc(e,'FORMULAR')
        response=self.client.get(reverse('review',args=[doc.pk]))
        self.assertContains(response,'name="positions-0-description"');self.assertContains(response,'Position hinzufügen')
        suggested=response.context['allocations'][0]
        self.assertEqual(suggested['entry_id'],str(e.pk));self.assertEqual(suggested['amount'],'75,00')
        data=self.position_payload(doc,alloc_project=str(self.auto.pk),alloc_entry=str(e.pk),alloc_amount='75,00',alloc_ref='1')
        self.assertEqual(self.client.post(reverse('review',args=[doc.pk]),data).status_code,302)
        self.assertEqual(e.cost,Decimal('75.00'));source=CostSource.objects.get();self.assertEqual(source.positions[0]['part_number'],'FILTER-NEU')

    def test_manual_expense_is_suggested_and_is_not_booked_twice(self):
        e=self.entry('Ölwechsel');source=CostSource.objects.create(owner=self.user,label='Ölwechsel',amount=Decimal('75'))
        allocate(self.user,source,[{'project':self.auto,'entry':e,'amount':'75.00'}]);doc=self.doc(e,'VERBINDEN')
        response=self.client.get(reverse('review',args=[doc.pk]));self.assertEqual(response.context['saved_connect'],str(source.pk));self.assertEqual(response.context['allocations'],[])
        self.assertEqual(self.client.post(reverse('review',args=[doc.pk]),self.position_payload(doc,connect=str(source.pk))).status_code,302)
        self.assertEqual(CostSource.objects.count(),1);self.assertEqual(e.cost,75)

    def test_fully_unallocated_needs_explicit_confirmation(self):
        e=self.entry('Ölwechsel');doc=self.doc(e,'OFFEN');data=self.position_payload(doc)
        response=self.client.post(reverse('review',args=[doc.pk]),data)
        self.assertContains(response,'ausdrücklich bestätigen');self.assertEqual(CostSource.objects.count(),0)
        data['keep_unallocated']='on';self.assertEqual(self.client.post(reverse('review',args=[doc.pk]),data).status_code,302)
        self.assertEqual(CostSource.objects.get().remainder,75);self.assertEqual(e.cost,0)

    def test_position_draft_and_explicit_save_preserve_allocations(self):
        e=self.entry('Ölwechsel');doc=self.doc(e,'ENTWURF')
        data=self.position_payload(doc,alloc_project=str(self.auto.pk),alloc_entry=str(e.pk),alloc_amount='60,00',alloc_ref='Teilzuordnung',action='save')
        data['positions-0-amount']='75,'
        response=self.client.post(reverse('review_draft',args=[doc.pk]),data);self.assertEqual(response.status_code,200)
        page=self.client.get(reverse('review',args=[doc.pk]));self.assertContains(page,'value="75,"');self.assertEqual(page.context['allocations'][0]['amount'],'60,00')
        data['version']=response.json()['version'];data['positions-0-amount']='75,00'
        self.assertEqual(self.client.post(reverse('review',args=[doc.pk]),data).status_code,302)
        page=self.client.get(reverse('review',args=[doc.pk]));self.assertEqual(page.context['allocations'][0]['amount'],'60,00')
        self.assertContains(page,'Korrigiertes Öl');self.assertEqual(CostSource.objects.count(),0)

    def test_deleted_position_and_invalid_amount_have_clear_validation(self):
        e=self.entry('Ölwechsel');doc=self.doc(e,'ZEILEN')
        data=self.position_payload(doc,keep_unallocated='on');data.update({'positions-TOTAL_FORMS':'2','positions-1-description':'Löschen','positions-1-amount':'99,00','positions-1-DELETE':'on'})
        self.assertEqual(self.client.post(reverse('review',args=[doc.pk]),data).status_code,302)
        self.assertEqual(len(CostSource.objects.get().positions),1)
        doc2=self.doc(e,'UNGUELTIG');bad=self.position_payload(doc2,keep_unallocated='on');bad['positions-0-amount']='1,234.56'
        page=self.client.post(reverse('review',args=[doc2.pk]),bad);self.assertContains(page,'Widersprüchliche Zahlentrenner');self.assertContains(page,'1,234.56')

    def test_custom_field_form_preserves_history_and_retires_removed_fields(self):
        e=self.entry('Alte Wartung')
        data={'name':'Auto','kind':'auto','position':'0','color':'#087f80','icon':'car','version':self.auto.version,'field_editor':'fields','field_json':'ABSICHTLICH KEIN JSON','fields-TOTAL_FORMS':'4','fields-INITIAL_FORMS':'2','fields-MAX_NUM_FORMS':'100','fields-MIN_NUM_FORMS':'0',
            'fields-0-original_name':'Öl','fields-0-name':'Öl','fields-0-kind':'text','fields-0-value':'Demo 0W-30','fields-0-active':'on',
            'fields-1-original_name':'Alt','fields-1-name':'Alt','fields-1-kind':'text','fields-1-value':'','fields-1-DELETE':'on',
            'fields-2-name':'Volumen','fields-2-kind':'number','fields-2-value':'180,5','fields-2-unit':'l','fields-2-active':'on',
            'fields-3-name':'Beleuchtung','fields-3-kind':'boolean','fields-3-active':'on'}
        response=self.client.post(reverse('project_edit',args=[self.auto.pk]),data);self.assertEqual(response.status_code,302)
        self.auto.refresh_from_db();e.refresh_from_db()
        self.assertEqual(self.auto.fields['Volumen']['value'],'180.5');self.assertEqual(self.auto.fields['Volumen']['unit'],'l')
        self.assertEqual(self.auto.fields['Beleuchtung']['value'],False);self.assertEqual(self.auto.fields['Alt']['active'],False)
        self.assertEqual(self.auto.fields['Alt']['value'],'Historisch');self.assertEqual(e.snapshot['oil'],'Demo 5W-40')
        self.assertContains(self.client.get(reverse('project_edit',args=[self.auto.pk])),'name="fields-0-value"')

    def test_original_image_and_pdf_are_attached_to_Gemini_request(self):
        expected=demo_proposal('DEMO-OEL-001')
        response=httpx.Response(200,request=httpx.Request('POST','https://example.invalid'),json={'candidates':[{'content':{'parts':[{'text':json.dumps(expected)}]}}]})
        for raw,mime in [(synthetic_pdf('DEMO-OEL-001'),'application/pdf'),(synthetic_photo(1),'image/jpeg')]:
            with override_settings(GEMINI_API_KEY='synthetic'):
                with patch('akte.adapters.bounded_request',return_value=response) as post:
                    GeminiAI().extract('Schlecht erkannter OCR-Text',original=raw,mime_type=mime)
            body=post.call_args.kwargs['json'];parts=body['contents'][0]['parts']
            self.assertEqual(parts[0]['text'],'Schlecht erkannter OCR-Text');self.assertEqual(parts[1]['inlineData']['mimeType'],mime)
            self.assertEqual(base64.b64decode(parts[1]['inlineData']['data']),raw);self.assertNotIn('tools',body)

    def test_original_mode_is_opt_in_bounded_and_uses_worker(self):
        e=self.entry('Ölwechsel');doc=self.doc(e,'DEMO-OEL-001')
        self.assertFalse(doc.ai_with_original)
        self.client.post(reverse('document_action',args=[doc.pk]),{'action':'ai','ai_with_original':'1'})
        doc.refresh_from_db();self.assertTrue(doc.ai_with_original)
        with patch('akte.worker.ai') as adapter:
            adapter.return_value.extract.return_value=(demo_proposal('DEMO-OEL-001'),{})
            run_one();self.assertEqual(adapter.return_value.extract.call_args.kwargs['mime_type'],'application/pdf')
        doc.refresh_from_db();self.assertEqual(doc.status,'review')
        with override_settings(GEMINI_ORIGINAL_MAX_BYTES=5):
            with self.assertRaises(IntegrationError): original_for_ai(doc)
        self.client.post(reverse('document_action',args=[doc.pk]),{'action':'ai'})
        doc.refresh_from_db();self.assertFalse(doc.ai_with_original)

    def test_vision_reservation_can_block_before_paid_request(self):
        e=self.entry('Ölwechsel');doc=self.doc(e,'BUDGET');doc.ai_with_original=True
        with override_settings(DEMO_MODE=False,AI_MONTHLY_LIMIT=Decimal('.10'),AI_INPUT_USD_PER_M=Decimal('1'),AI_OUTPUT_USD_PER_M=Decimal('1')):
            with self.assertRaises(IntegrationError): reserve_ai(doc)

    def test_choice_date_and_link_fields_and_duplicate_names(self):
        data={'name':'Auto','kind':'auto','position':'0','color':'#087f80','icon':'car','version':self.auto.version,'field_editor':'fields','fields-TOTAL_FORMS':'3','fields-INITIAL_FORMS':'0','fields-MAX_NUM_FORMS':'100','fields-MIN_NUM_FORMS':'0',
            'fields-0-name':'Modus','fields-0-kind':'choice','fields-0-options':'Normal\nSparsam','fields-0-value':'Sparsam','fields-0-active':'on',
            'fields-1-name':'Bauzeit','fields-1-kind':'date','fields-1-value':'11/2026','fields-1-active':'on',
            'fields-2-name':'Anleitung','fields-2-kind':'link','fields-2-value':'https://example.com/manual','fields-2-active':'on'}
        self.assertEqual(self.client.post(reverse('project_edit',args=[self.auto.pk]),data).status_code,302)
        self.auto.refresh_from_db();self.assertEqual(self.auto.fields['Modus']['value'],'Sparsam');self.assertEqual(self.auto.fields['Bauzeit']['value'],'2026-11')
        data['version']=self.auto.version;data['fields-2-name']='Modus'
        self.assertContains(self.client.post(reverse('project_edit',args=[self.auto.pk]),data),'Jeder Feldname darf nur einmal vorkommen')

    def test_300_position_rows_fit_request_limits_and_remain_cent_exact(self):
        e=self.entry('Viele Positionen');doc=self.doc(e,'300-ZEILEN','3.00')
        data={'version':doc.version,'invoice_number':'300-ZEILEN','gross_total':'3,00','currency':'EUR','positions_editor':'rows','keep_unallocated':'on','positions-TOTAL_FORMS':'300','positions-INITIAL_FORMS':'0','positions-MAX_NUM_FORMS':'300','positions-MIN_NUM_FORMS':'0','action':'accept'}
        for i in range(300):
            data.update({f'positions-{i}-description':f'Testposition {i}',f'positions-{i}-part_number':'',f'positions-{i}-amount':'0,01',f'positions-{i}-DELETE':''})
        self.assertEqual(self.client.post(reverse('review',args=[doc.pk]),data).status_code,302)
        source=CostSource.objects.get();self.assertEqual(len(source.positions),300);self.assertEqual(source.amount,Decimal('3.00'))

    def test_old_german_number_and_new_precision_survive_reopening(self):
        from .editors import project_field_initial,ProjectFields,project_fields_data
        old={'Kilometer':{'type':'number','value':'165.200'}}
        self.assertEqual(project_field_initial(old)[0]['value'],'165200')
        raw={'fields-TOTAL_FORMS':'1','fields-INITIAL_FORMS':'0','fields-MAX_NUM_FORMS':'100','fields-MIN_NUM_FORMS':'0','fields-0-name':'Dosierung','fields-0-kind':'number','fields-0-value':'1,234','fields-0-active':'on'}
        formset=ProjectFields(raw,prefix='fields');self.assertTrue(formset.is_valid())
        result=project_fields_data(formset,{})
        self.assertEqual(result['Dosierung']['value'],'1.234');self.assertEqual(project_field_initial(result)[0]['value'],'1,234')
