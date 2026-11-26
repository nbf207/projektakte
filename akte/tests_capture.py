"""One targeted Snap & Talk test; no external APIs or user data."""
import copy
import json
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
import httpx
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase,override_settings
from django.urls import reverse
from django.utils import timezone
from .capture import receive_capture,process_capture
from .demo import synthetic_photo,synthetic_pdf
from .models import AIUsage,Allocation,CostSource,Document,Entry,Job,PhotoLink,Project,Task
from .proposal_schema import empty_entry,INVOICE_FIELDS,POSITION_FIELDS
from .worker import run_one


class CaptureFlowTest(TestCase):
    def test_snap_and_talk_flow(self):
        with tempfile.TemporaryDirectory() as folder,override_settings(DATA_DIR=Path(folder),MEDIA_ROOT=Path(folder)/'media',
            DEMO_MODE=False,ALLOWED_HOSTS=['testserver'],GEMINI_API_KEY='synthetic',AI_ENABLED=True,
            AI_INPUT_USD_PER_M=Decimal('.30'),AI_OUTPUT_USD_PER_M=Decimal('2.50'),AI_MONTHLY_LIMIT=Decimal('1.50')):
            user=get_user_model().objects.create_user('snap-test');self.client.force_login(user)
            project=Project.objects.create(owner=user,name='BMW-Test',kind='auto',fields={'model':{'value':'SYN-BMW'}})
            other=get_user_model().objects.create_user('other-owner')
            Project.objects.create(owner=other,name='Privates Fremdprojekt',kind='auto')
            parent=Entry.objects.create(project=project,title='Offener Testumbau',kind='umbau',status='arbeit')
            page=self.client.get(reverse('home'))
            self.assertContains(page,'snap-form');self.assertNotContains(page,'Privates Fremdprojekt')
            key=uuid.uuid4();audio=b'RIFF'+bytes(4)+b'WAVEfmt '+bytes(80)
            photo=synthetic_photo(90);pdf=synthetic_pdf('Synthetische Arbeitsnotiz, keine Rechnung')
            response=self.client.post(reverse('capture_upload'),{'capture_token':str(key),'text':'BMW-Test: Ölfilter für offenen Umbau wechseln.',
                'files':[SimpleUploadedFile('Arbeit.jpg',photo),SimpleUploadedFile('Notiz.pdf',pdf)],
                'audio':SimpleUploadedFile('SYN.wav',audio)},HTTP_X_REQUESTED_WITH='XMLHttpRequest')
            self.assertEqual(response.status_code,200,response.content)
            doc=Document.objects.get(submission_key=key);entry_id=doc.entry_id
            self.assertEqual(response.json()['url'],reverse('entry',args=[entry_id]))
            self.assertEqual(PhotoLink.objects.filter(entry_id=entry_id).count(),1)
            self.assertTrue(doc.entry.is_provisional)
            original_photo=PhotoLink.objects.get(entry_id=entry_id).asset.file.path
            Job.objects.filter(kind='thumbnail').update(status='done')
            proposal=dict.fromkeys(INVOICE_FIELDS)
            proposal.update(entry={**empty_entry(),'title':'Ölfilter wechseln','kind':'notiz','status':'geplant','meter':'120000','meter_unit':'km'},
                positions=[{**dict.fromkeys(POSITION_FIELDS),'description':'Ölfilter','part_number':'SYN-123','price_basis':'unknown'}],
                inferred_fields=['proposal.entry.title','proposal.entry.kind'],
                project_assignment={'project_id':str(project.pk),'confidence':'high','reason':'BMW-Test ausdrücklich genannt'})
            result={'transcript':'Ölfilter wechseln, 120000 Kilometer.','category':'aufgabe','is_receipt':False,'receipt_files':[],
                'open_entry':{'id':str(parent.pk),'confidence':'high','reason':'Offener Testumbau genannt'},'proposal':proposal}
            def api_result(value):
                return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':json.dumps(value)}]}}],
                    'usageMetadata':{'promptTokenCount':100,'totalTokenCount':200}})
            with patch('akte.adapters.httpx.post',return_value=api_result(result)) as api,self.captureOnCommitCallbacks(execute=True):
                self.assertTrue(run_one())
                sent=api.call_args.kwargs['json'];parts=sent['contents'][0]['parts']
                self.assertEqual(len([item for item in parts if 'inlineData' in item]),3)
                self.assertIn('SYN-BMW',sent['systemInstruction']['parts'][0]['text'])
                self.assertNotIn('Privates Fremdprojekt',sent['systemInstruction']['parts'][0]['text'])
            doc.refresh_from_db();self.assertEqual(doc.status,'accepted',doc.error)
            self.assertEqual(doc.entry_id,entry_id);self.assertEqual(doc.entry.project_id,project.pk)
            self.assertFalse(doc.entry.is_provisional);self.assertIsNone(doc.entry.date)
            self.assertEqual(doc.entry.meter,Decimal('120000'))
            self.assertEqual(doc.entry.snapshot['capture_related_entry']['id'],str(parent.pk))
            self.assertEqual(doc.pages.count(),2);self.assertTrue(Path(original_photo).exists())
            self.assertEqual(Task.objects.filter(entry_id=entry_id).count(),1)
            self.assertEqual(CostSource.objects.count(),0)
            process_capture(Job.objects.get(document=doc))
            self.assertEqual(Task.objects.filter(entry_id=entry_id).count(),1)
            self.assertEqual(self.client.get(reverse('entry',args=[entry_id])).status_code,200)
            self.assertEqual(self.client.get(reverse('capture_detail',args=[doc.pk])).status_code,200)
            repeated=receive_capture(user,text='different retry',token=key)
            self.assertEqual(repeated.pk,doc.pk)
            # A real receipt becomes the existing review workflow, never a direct cost.
            bill=receive_capture(user,text='Rechnung für BMW-Test',project=project,token=uuid.uuid4(),
                uploads=[SimpleUploadedFile('SYN-Rechnung.pdf',synthetic_pdf('SYN-Rechnung 45.00 EUR'))])
            receipt_proposal=copy.deepcopy(proposal)
            receipt_proposal.update(invoice_number='SYN-SNAP-1',vendor='Synthetische Werkstatt',invoice_date='2026-11-13',
                gross_total='45.00',currency='EUR',positions=[{'description':'Ölfilter','amount':'45.00'}])
            bill_result={**result,'category':'wartung','is_receipt':True,'receipt_files':[0],'transcript':'',
                'open_entry':{'id':None,'confidence':'none','reason':None},'proposal':receipt_proposal}
            with patch('akte.adapters.httpx.post',return_value=api_result(bill_result)):
                self.assertTrue(run_one())
            bill.refresh_from_db();self.assertEqual(bill.status,'accepted',bill.error)
            receipt=Document.objects.get(pk=bill.voice_data['receipt_id'])
            self.assertEqual(receipt.entry_id,bill.entry_id);self.assertFalse(receipt.ai_enabled)
            self.assertEqual(receipt.status,'paperless');self.assertEqual(CostSource.objects.count(),0)
            self.assertEqual(self.client.get(reverse('review',args=[receipt.pk])).status_code,200)
            receipt.paperless_id=987;receipt.verified_at=timezone.now();receipt.remote_state='current';receipt.save()
            from .billing import accept_document
            rows=[{'project':project,'entry':receipt.entry,'amount':Decimal('45.00')}]
            for _ in range(2):
                accept_document(user,receipt,receipt.proposal,rows,expected_version=receipt.version,
                    chronicle={'mode':'create','project':project})
            self.assertEqual(CostSource.objects.count(),1)
            self.assertEqual(Allocation.objects.get().amount,Decimal('45.00'))
            Job.objects.filter(document=receipt).update(status='done')
            # Timeout retains inputs and budget reservation; no duplicate cost/entry.
            failed=receive_capture(user,text='Test mit Ausfall',token=uuid.uuid4(),audio=SimpleUploadedFile('SYN-fail.wav',audio))
            with patch('akte.adapters.httpx.post',side_effect=httpx.ReadTimeout('synthetic timeout')):
                self.assertTrue(run_one())
            failed.refresh_from_db();self.assertEqual(failed.job.status,'pending')
            self.assertTrue(failed.pages.get().asset.file.storage.exists(failed.pages.get().asset.file.name))
            self.assertEqual(AIUsage.objects.filter(document=failed,state='reserved').count(),1)
            self.assertEqual(CostSource.objects.count(),1)
