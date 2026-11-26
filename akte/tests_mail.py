"""One targeted IMAP intake test; synthetic mail, mailbox and Gemini only."""
import imaplib
import json
import ssl
import tempfile
import uuid
from decimal import Decimal
from email.message import EmailMessage
from email import policy
from pathlib import Path
from unittest.mock import Mock,patch
import httpx
from django.contrib.auth import get_user_model
from django.test import TestCase,override_settings
from django.utils import timezone
from .demo import synthetic_photo,synthetic_pdf
from .mail_config import MailConfig
from .mail_ingest import MailPoller,import_message,subject_project
from .models import CostSource,Document,Job,Project
from .proposal_schema import empty_entry,INVOICE_FIELDS
from .worker import run_one


class MailFlowTest(TestCase):
    def test_imap_to_shared_ai_once_only(self):
        with tempfile.TemporaryDirectory() as folder,override_settings(DATA_DIR=Path(folder),MEDIA_ROOT=Path(folder)/'media',
            DEMO_MODE=False,AI_ENABLED=True,GEMINI_API_KEY='synthetic',AI_INPUT_USD_PER_M=Decimal('.30'),AI_OUTPUT_USD_PER_M=Decimal('2.50'),AI_MONTHLY_LIMIT=Decimal('1.50')):
            owner=get_user_model().objects.create_user('mail-synthetic')
            project=Project.objects.create(owner=owner,name='SYN Auto',kind='auto',fields={'kuerzel':{'value':'BMW'}})
            Project.objects.create(owner=owner,name='SYN Aquarium',kind='aquarium',fields={'kuerzel':{'value':'AQUA'}})
            config=MailConfig('imap.example.invalid','mailbox@example.invalid','fixture-secret','sender@example.invalid','mail-synthetic')
            self.assertNotIn('fixture-secret',repr(config))
            self.assertEqual(subject_project(owner,'[BMW] Bestellung').pk,project.pk)
            self.assertIsNone(subject_project(owner,'[BMW] und [AQUA]'))
            mail=EmailMessage();mail['From']='Example Sender <sender@example.invalid>';mail['To']=config.user
            mail['Subject']='[BMW] SYN Bestellung Ölfilter';mail['Message-ID']='<SYN-IMAP-1@example.invalid>'
            mail['Date']='Sat, 14 Nov 2026 00:15:00 +0100'
            mail.set_content('Synthetische Bestellung, kein Einbau. Gesamtbetrag 19,99 EUR. Kein Arbeitsdatum.')
            mail.add_attachment(synthetic_photo(151),maintype='image',subtype='jpeg',filename='SYN-work.jpg')
            mail.add_attachment(synthetic_pdf('SYN Lieferschein ohne Preis'),maintype='application',subtype='pdf',filename='SYN-note.pdf')
            raw=mail.as_bytes(policy=policy.SMTP)
            seen=set();fetched=[];store_calls=[];fail_seen=[True]
            client=Mock();client.__enter__=Mock(return_value=client);client.__exit__=Mock(return_value=False)
            client.select.return_value=('OK',[b'3'])
            def uid(command,*args):
                if command=='search':return 'OK',[b'1 2 3']
                number=int(args[0])
                if command=='store':
                    store_calls.append(number)
                    if fail_seen[0]:fail_seen[0]=False;return 'NO',[b'failed']
                    seen.add(number);return 'OK',[b'']
                if 'HEADER.FIELDS' in args[1]:
                    headers=(b'From: "sender@example.invalid" <attacker@example.invalid>\r\n' if number==1 else b'From: Example Sender <sender@example.invalid>\r\n')
                    size=99*1024*1024 if number==3 else len(raw)
                    return 'OK',[(f'{number} (UID {number} RFC822.SIZE {size})'.encode(),headers)]
                fetched.append(number);return 'OK',[(b'2 (BODY[] literal)',raw)]
            client.uid.side_effect=uid;poller=MailPoller()
            with patch('akte.mail_ingest.mail_config',return_value=config),patch('akte.mail_ingest.BoundedIMAP',return_value=client) as factory:
                with self.assertLogs('akte.mail_ingest',level='WARNING') as logs:self.assertEqual(poller.tick(),0)
                self.assertNotIn('fixture-secret',' '.join(logs.output))
                doc=Document.objects.get(capture_type='snap');entry_id=doc.entry_id
                self.assertEqual(doc.project_id,project.pk);self.assertEqual(doc.pages.count(),3)
                self.assertEqual(doc.entry.photos.count(),1);self.assertEqual(store_calls,[2]);self.assertFalse(seen)
                self.assertIn('Bestellung',doc.voice_data['mail']['full_text'])
                poller.next_check=0
                with self.assertLogs('akte.mail_ingest',level='WARNING'):self.assertEqual(poller.tick(),1)
                self.assertEqual(Document.objects.count(),1);self.assertEqual(Job.objects.filter(document=doc).count(),1)
                self.assertEqual(seen,{2});self.assertNotIn(1,fetched);self.assertNotIn(3,fetched)
                self.assertEqual(poller.tick(),0);self.assertEqual(factory.call_count,2)
                self.assertTrue(factory.call_args.kwargs['ssl_context'].check_hostname)
                self.assertEqual(factory.call_args.kwargs['ssl_context'].verify_mode,ssl.CERT_REQUIRED)
                client.sock.settimeout.assert_called_with(10)
            Job.objects.filter(kind='thumbnail').update(status='done')
            proposal=dict.fromkeys(INVOICE_FIELDS)
            proposal.update(vendor='SYN Teilehandel',gross_total='19.99',currency='EUR',positions=[{'description':'Ölfilter','amount':'19.99'}],
                entry={**empty_entry(),'title':'SYN Bestellung Ölfilter','kind':'notiz'},inferred_fields=['entry.title','entry.kind'],
                project_assignment={'project_id':str(project.pk),'confidence':'high','reason':'Betreffkürzel'})
            result={'knowledge':[],'transcript':'','category':'notiz','is_receipt':True,'receipt_files':[2],
                'open_entry':{'id':None,'confidence':'none','reason':None},'proposal':proposal}
            response=httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':json.dumps(result)}]}}],
                'usageMetadata':{'promptTokenCount':300,'totalTokenCount':600}})
            with patch('akte.adapters.bounded_request',return_value=response) as api:
                self.assertTrue(run_one());instruction=api.call_args.kwargs['json']['systemInstruction']['parts'][0]['text']
                self.assertIn('source_kind',instruction);self.assertIn('email_source_index',instruction)
            doc.refresh_from_db();self.assertEqual(doc.entry_id,entry_id)
            receipt=Document.objects.get(pk=doc.voice_data['receipt_id'])
            self.assertEqual(receipt.proposal['gross_total'],'19.99');self.assertFalse(receipt.ai_enabled)
            self.assertEqual(CostSource.objects.count(),0);self.assertEqual(doc.entry.date,timezone.localdate())
            # HTML-only/text-only mail is stored with an original-source PDF.
            html=EmailMessage();html['From']=config.sender;html['Subject']='Allgemeine Notiz'
            html.set_content('<style>DO-NOT-SHOW</style><p>Notiz aus HTML</p>',subtype='html')
            extra=import_message(owner,config,html.as_bytes());self.assertIsNone(extra.project_id)
            self.assertIn('Notiz aus HTML',extra.voice_data['text']);self.assertNotIn('DO-NOT-SHOW',extra.voice_data['text'])
            self.assertEqual(extra.pages.count(),1)
            # Original + eight supported attachments are retained, without silent omission.
            bundle=EmailMessage();bundle['From']=config.sender;bundle['Subject']='Acht Bilder';bundle.set_content('SYN Bilder')
            for index in range(8):bundle.add_attachment(synthetic_photo(160+index),maintype='image',subtype='jpeg',filename=f'SYN-{index}.jpg')
            grouped=import_message(owner,config,bundle.as_bytes());self.assertEqual(grouped.pages.count(),9)
            poller.next_check=0
            with patch('akte.mail_ingest.mail_config',return_value=config),patch('akte.mail_ingest.BoundedIMAP',side_effect=imaplib.IMAP4.error('fixture-secret')):
                with self.assertLogs('akte.mail_ingest',level='WARNING') as logs:self.assertEqual(poller.tick(),0)
                self.assertNotIn('fixture-secret',' '.join(logs.output))
