"""One focused voice-capture flow, with synthetic audio and mocked HTTP."""
import json
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import httpx
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import AIUsage, Allocation, CostSource, Document, Job, Project, Task
from .proposal_schema import empty_entry, INVOICE_FIELDS, POSITION_FIELDS
from .voice import receive_voice
from .worker import run_one


class VoiceFlowTest(TestCase):
    def test_capture_process_review_and_retry(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(
            DATA_DIR=Path(folder), MEDIA_ROOT=Path(folder)/'media', DEMO_MODE=False,
            ALLOWED_HOSTS=['testserver'], GEMINI_API_KEY='synthetic-test-key',
            AI_INPUT_USD_PER_M=Decimal('.30'), AI_OUTPUT_USD_PER_M=Decimal('2.50'),
            AI_MONTHLY_LIMIT=Decimal('1.50'), AI_ENABLED=True):
            user=get_user_model().objects.create_user('synthetic-voice')
            project=Project.objects.create(owner=user,name='Synthetisches Auto',kind='auto')
            self.client.force_login(user)
            self.assertContains(self.client.get(reverse('home')), 'voice-start')
            audio=b'RIFF'+bytes(4)+b'WAVEfmt '+bytes(80)
            token=uuid.uuid4()
            doc=receive_voice(user,SimpleUploadedFile('synthetic.wav',audio),token)
            original_entry=doc.entry_id
            original_file=doc.asset.file.path
            self.assertTrue(doc.entry.is_provisional)
            self.assertIsNone(doc.paperless_id)
            self.assertEqual(receive_voice(user,SimpleUploadedFile('again.wav',audio),token).pk,doc.pk)
            proposal=dict.fromkeys(INVOICE_FIELDS)
            proposal.update(entry={**empty_entry(),'title':'Ölfilter wechseln','kind':'notiz','meter':'120000','meter_unit':'km'},
                inferred_fields=['proposal.entry.title','proposal.entry.kind'],
                project_assignment={'project_id':str(project.pk),'confidence':'high','reason':'Auto ausdrücklich genannt'},
                positions=[{**dict.fromkeys(POSITION_FIELDS),'description':'Ölfilter','part_number':'SYN123','price_basis':'unknown'}])
            result={'transcript':'Beim synthetischen Auto Ölfilter wechseln, 120000 Kilometer.','voice_type':'aufgabe','proposal':proposal}
            response=httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':json.dumps(result)}]}}],
                'usageMetadata':{'promptTokenCount':100,'totalTokenCount':200}})
            with patch('akte.adapters.bounded_request',return_value=response) as api, self.captureOnCommitCallbacks(execute=True):
                self.assertTrue(run_one())
                self.assertEqual(api.call_args.kwargs['json']['contents'][0]['parts'][1]['inlineData']['mimeType'],'audio/wav')
            doc.refresh_from_db()
            self.assertEqual(doc.status,'review',doc.error)
            self.assertEqual(doc.entry_id,original_entry)
            self.assertEqual(doc.entry.project_id,project.pk)
            self.assertEqual(doc.entry.date,timezone.localdate())
            self.assertIsNone(doc.asset_id)
            self.assertFalse(Path(original_file).exists())
            self.assertEqual(CostSource.objects.count(),0)
            failed=receive_voice(user,SimpleUploadedFile('timeout.wav',audio+b'failure'),uuid.uuid4())
            with patch('akte.adapters.bounded_request',side_effect=httpx.ReadTimeout('synthetic timeout')):
                self.assertTrue(run_one())
            failed.refresh_from_db()
            self.assertTrue(failed.asset.file.storage.exists(failed.asset.file.name))
            self.assertEqual(Job.objects.get(document=failed).status,'pending')
            self.assertEqual(AIUsage.objects.filter(document=failed,state='reserved').count(),1)
            self.assertEqual(CostSource.objects.count(),0)
