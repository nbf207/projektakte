"""One focused PWA/share test, including real CSRF middleware and synthetic JS events."""
import json
import re
import subprocess
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
import httpx
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client,TestCase,override_settings
from django.urls import reverse
from .demo import synthetic_pdf,synthetic_photo
from .models import CostSource,Document,Entry,Job,Part,Project
from .proposal_schema import empty_entry,INVOICE_FIELDS
from .worker import run_one


class ShareFlowTest(TestCase):
    def test_installed_share_to_csrf_form_and_capture(self):
        javascript=subprocess.run(['node',str(Path(__file__).with_name('tests_share_worker.mjs')),str(settings.BASE_DIR/'static')],capture_output=True,text=True,timeout=30)
        self.assertEqual(javascript.returncode,0,javascript.stdout+javascript.stderr)
        with tempfile.TemporaryDirectory() as folder,override_settings(DATA_DIR=Path(folder),MEDIA_ROOT=Path(folder)/'media',DEMO_MODE=False,
            ALLOWED_HOSTS=['testserver'],GEMINI_API_KEY='synthetic',AI_ENABLED=True,AI_INPUT_USD_PER_M=Decimal('.30'),
            AI_OUTPUT_USD_PER_M=Decimal('2.50'),AI_MONTHLY_LIMIT=Decimal('1.50')):
            client=Client(enforce_csrf_checks=True)
            manifest=client.get(reverse('manifest'));self.assertEqual(manifest.status_code,200)
            target=manifest.json()['share_target'];self.assertEqual(target['method'],'POST');self.assertEqual(target['action'],'/teilen/')
            self.assertIn('image/*',target['params']['files'][0]['accept']);self.assertIn('application/pdf',target['params']['files'][0]['accept'])
            worker=client.get(reverse('service_worker'));self.assertEqual(worker.status_code,200);self.assertEqual(worker['Service-Worker-Allowed'],'/')
            packet=uuid.uuid4();page=client.get(reverse('share')+'?paket='+str(packet));self.assertEqual(page.status_code,302);self.assertIn('paket',page.url)
            self.assertEqual(client.post(reverse('share_save'),{}).status_code,403);self.assertEqual(Document.objects.count(),0)
            user=get_user_model().objects.create_user('share-synthetic');foreign=get_user_model().objects.create_user('other-share-owner')
            project=Project.objects.create(owner=user,name='SYN Projekt',kind='auto',fields={'long':{'value':'A'*4500+'MASTER-END'}})
            other=Project.objects.create(owner=foreign,name='PRIVATE-OTHER',kind='auto')
            Part.objects.create(project=project,name='SYN bekannter Filter',number='SYN-PART-777',manufacturer='SYN Hersteller',state='vorhanden')
            Part.objects.create(project=other,name='SECRET-FOREIGN-PART',number='NO')
            parent=Entry.objects.create(project=project,title='SYN offener Umbau',kind='umbau',status='arbeit')
            client.force_login(user);page=client.get(reverse('share')+'?paket='+str(packet))
            self.assertContains(page,'Mit KI auswerten &amp; speichern');self.assertNotContains(page,'PRIVATE-OTHER')
            csrf=re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"',page.content.decode()).group(1)
            photo=synthetic_photo(120);pdf=synthetic_pdf('SYN share invoice 23.45 EUR')
            def payload(token=packet,project_id=None):
                return {'csrfmiddlewaretoken':csrf,'capture_token':str(token),'project':str(project.pk) if project_id is None else project_id,
                    'text':'SYN-Teilen-Test','files':[SimpleUploadedFile('SYN-work.jpg',photo),SimpleUploadedFile('SYN-bill.pdf',pdf)]}
            self.assertEqual(client.post(reverse('share_save'),payload(project_id=''),HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code,400)
            self.assertEqual(client.post(reverse('share_save'),payload(project_id=str(other.pk)),HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code,404)
            response=client.post(reverse('share_save'),payload(),HTTP_X_REQUESTED_WITH='XMLHttpRequest')
            self.assertEqual(response.status_code,200,response.content);url=response.json()['url'];self.assertIn('weiter=1',url)
            doc=Document.objects.get(submission_key=packet);self.assertEqual(doc.project_id,project.pk);self.assertEqual(doc.pages.count(),2)
            self.assertEqual(client.get(url).status_code,200)
            self.assertEqual(client.post(reverse('share_save'),payload(),HTTP_X_REQUESTED_WITH='XMLHttpRequest').json()['url'],url)
            self.assertEqual(Document.objects.count(),1);Job.objects.filter(kind='thumbnail').update(status='done')
            proposal=dict.fromkeys(INVOICE_FIELDS)
            proposal.update(gross_total='23.45',currency='EUR',positions=[{'description':'Filter','amount':'23.45'}],
                entry={**empty_entry(),'title':'SYN Einkauf','kind':'notiz'},inferred_fields=['entry.title','entry.kind'],
                project_assignment={'project_id':str(project.pk),'confidence':'high','reason':'Ausgewählt'})
            result={'transcript':'','category':'notiz','is_receipt':True,'receipt_files':[1],
                'open_entry':{'id':str(parent.pk),'confidence':'high','reason':'SYN Umbau'},'proposal':proposal}
            def response_for(value):
                return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':json.dumps(value)}]}}],
                    'usageMetadata':{'promptTokenCount':200,'totalTokenCount':400}})
            with patch('akte.adapters.httpx.post',return_value=response_for(result)) as api:
                self.assertTrue(run_one())
                instruction=api.call_args.kwargs['json']['systemInstruction']['parts'][0]['text']
                for fact in ['MASTER-END','SYN-PART-777','SYN offener Umbau']:self.assertIn(fact,instruction)
                self.assertNotIn('PRIVATE-OTHER',instruction);self.assertNotIn('SECRET-FOREIGN-PART',instruction)
            doc.refresh_from_db();receipt=Document.objects.get(pk=doc.voice_data['receipt_id'])
            self.assertRedirects(client.get(url),reverse('review',args=[receipt.pk]),fetch_redirect_response=False)
            self.assertEqual(CostSource.objects.count(),0);Job.objects.filter(document=receipt).update(status='done')
            # A normal photo share forwards to its saved chronology entry.
            next_token=uuid.uuid4();next_payload=payload(token=next_token);next_payload['text']='SYN normale Foto-Notiz'
            saved=client.post(reverse('share_save'),next_payload,HTTP_X_REQUESTED_WITH='XMLHttpRequest')
            normal=Document.objects.get(submission_key=next_token)
            normal_result={**result,'is_receipt':False,'receipt_files':[],'proposal':{**proposal,'gross_total':None,'currency':None,'positions':[]}}
            with patch('akte.adapters.httpx.post',return_value=response_for(normal_result)):
                self.assertTrue(run_one())
            normal.refresh_from_db();self.assertEqual(normal.status,'accepted',normal.error)
            self.assertRedirects(client.get(saved.json()['url']),reverse('entry',args=[normal.entry_id]),fetch_redirect_response=False)
            self.assertEqual(CostSource.objects.count(),0)
