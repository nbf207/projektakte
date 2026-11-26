"""A working model lookup must not hide generateContent billing failures."""
import json
from decimal import Decimal
from unittest.mock import patch

import httpx
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from .adapters import AIRequestRejected, AIResponseInvalid, GeminiAI, IntegrationError, demo_proposal
from .models import AIUsage, CostSource, Document, Entry, Job
from . import tests_invoice_chronicle
from .worker import run_one


@override_settings(GEMINI_API_KEY='synthetic-secret')
class GeminiErrorMessagesTests(SimpleTestCase):
    def response(self, status, content=None):
        return httpx.Response(status, request=httpx.Request('POST','https://example.invalid'),
                              json=content or {'error':{'message':'never echo synthetic-secret'}})

    def test_billing_auth_model_and_quota_errors_are_distinct_and_redacted(self):
        for status, expected in [(400,'Schema'),(401,'Schlüssel'),(402,'Vorauszahlungsguthaben'),
                                 (403,'Schlüsselberechtigung'),(404,'Modell'),(429,'Kontingent')]:
            with self.subTest(status=status),patch('akte.adapters.httpx.post',return_value=self.response(status)):
                with self.assertRaises(AIRequestRejected) as caught:GeminiAI().extract('synthetic')
                self.assertIn(expected,str(caught.exception))
                self.assertIn(str(status),str(caught.exception))
                self.assertNotIn('synthetic-secret',str(caught.exception))
                self.assertEqual(caught.exception.retryable,status==429)

    def test_timeout_and_transient_service_error_remain_retryable(self):
        for failure in [httpx.ReadTimeout('secret in URL'),None]:
            options={'side_effect':failure} if failure else {'return_value':self.response(503)}
            with self.subTest(failure=failure),patch('akte.adapters.httpx.post',**options):
                with self.assertRaises(IntegrationError) as caught:GeminiAI().extract('synthetic')
                self.assertNotIsInstance(caught.exception,AIRequestRejected)
                self.assertNotIn('secret in URL',str(caught.exception))

    def test_invalid_success_retains_usage_and_reports_output_failure(self):
        body={'candidates':[{'content':{'parts':[{'text':'not JSON'}]}}],
              'usageMetadata':{'promptTokenCount':10,'totalTokenCount':20}}
        with patch('akte.adapters.httpx.post',return_value=self.response(200,body)):
            with self.assertRaises(AIResponseInvalid) as caught:GeminiAI().extract('synthetic')
        self.assertEqual(caught.exception.usage['totalTokenCount'],20)
        self.assertIn('strukturierten',str(caught.exception))

    def test_thought_parts_do_not_replace_the_structured_answer(self):
        proposal=demo_proposal('DEMO-OEL-001')
        body={'candidates':[{'content':{'parts':[{'thought':True,'text':'internal'},
              {'text':json.dumps(proposal)}]},'finishReason':'STOP'}]}
        with patch('akte.adapters.httpx.post',return_value=self.response(200,body)):
            actual,_=GeminiAI().extract('synthetic')
        self.assertEqual(actual,proposal)

    def test_model_footnotes_are_metadata_instead_of_literal_title_and_tag_stars(self):
        from .proposal_schema import normalize_interpretations
        proposal=demo_proposal('DEMO-OEL-001')
        proposal['entry'].update(title='Öl und Filter gekauft*',tags='Öl*, Filter*')
        normalized=normalize_interpretations(proposal)
        self.assertEqual(normalized['entry']['title'],'Öl und Filter gekauft')
        self.assertEqual(normalized['entry']['tags'],'Öl, Filter')
        self.assertIn('entry.tags',normalized['inferred_fields'])

    def test_truncated_structured_output_is_never_accepted_as_complete(self):
        body={'candidates':[{'finishReason':'MAX_TOKENS','content':{'parts':[{'text':'{}'}]}}]}
        with patch('akte.adapters.httpx.post',return_value=self.response(200,body)):
            with self.assertRaisesMessage(AIResponseInvalid,'Ausgabelimit'):GeminiAI().extract('synthetic')


# Reuse the synthetic fixture, without inheriting its test methods a second time.
class GeminiWorkerFailureTests(TestCase):
    setUp=tests_invoice_chronicle.InvoiceChronicleTests.setUp
    document=tests_invoice_chronicle.InvoiceChronicleTests.document

    def prepared(self):
        doc=self.document()
        doc.instance='http://paperless.invalid';doc.status='ai';doc.save()
        Job.objects.filter(document=doc).update(status='pending',stage='ai',available=timezone.now())
        return doc

    def settings(self):
        return override_settings(DEMO_MODE=False,PAPERLESS_URL='http://paperless.invalid',
            PAPERLESS_TOKEN='synthetic',GEMINI_API_KEY='synthetic',AI_ENABLED=True,
            AI_INPUT_USD_PER_M=Decimal('.30'),AI_OUTPUT_USD_PER_M=Decimal('2.50'),
            AI_MONTHLY_LIMIT=Decimal('4.50'),AI_REQUEST_RESERVE=Decimal('.05'))

    def test_402_blocks_after_one_attempt_and_releases_rejected_reservation(self):
        doc=self.prepared()
        response=httpx.Response(402,request=httpx.Request('POST','https://example.invalid'))
        with self.settings(),patch('akte.adapters.httpx.post',return_value=response) as post:
            self.assertTrue(run_one())
            self.assertFalse(run_one())
            self.assertEqual(post.call_count,1)
        doc.refresh_from_db()
        self.assertEqual(doc.status,'review')
        self.assertIn('Vorauszahlungsguthaben',doc.error)
        self.assertEqual(doc.job.status,'blocked')
        usage=AIUsage.objects.get(document=doc)
        self.assertEqual((usage.state,usage.estimated_usd),('rejected',Decimal(0)))
        self.assertEqual((Entry.objects.count(),CostSource.objects.count()),(0,0))

    def test_invalid_json_accounts_known_tokens_instead_of_full_reservation(self):
        doc=self.prepared()
        response=httpx.Response(200,request=httpx.Request('POST','https://example.invalid'),
            json={'candidates':[{'content':{'parts':[{'text':'invalid'}]}}],
                  'usageMetadata':{'promptTokenCount':10,'totalTokenCount':20}})
        with self.settings(),patch('akte.adapters.httpx.post',return_value=response):run_one()
        usage=AIUsage.objects.get(document=doc)
        self.assertEqual((usage.state,usage.estimated_usd),('complete',Decimal('.000028')))

    def test_late_billing_rejection_does_not_reset_accepted_invoice(self):
        from .services import accept_document
        doc=self.prepared()
        def late(*args,**kwargs):
            accept_document(self.user,doc,doc.proposal,[{'project':self.project,'amount':'75'}],expected_version=doc.version)
            raise AIRequestRejected('Vorauszahlungsguthaben fehlt (HTTP 402).',402)
        with self.settings(),patch('akte.worker.ai') as adapter:
            adapter.return_value.extract.side_effect=late
            run_one()
        doc.refresh_from_db()
        self.assertEqual(doc.status,'accepted')
        self.assertEqual(doc.job.status,'done')
        self.assertEqual(CostSource.objects.count(),1)
