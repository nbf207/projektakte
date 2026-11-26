"""Daily-use acceptance: exact positions, recurring maintenance and owner bootstrap."""
import io
import json
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from pypdf import PdfReader
from .billing import validate_proposal
from .editors import Positions, position_data, position_initial
from .exports import portable_export, perform_export
from .maintenance import complete_maintenance, MaintenanceForm, next_due, rule_state
from .maintenance_pdf import maintenance_pdf
from .models import Entry, ExportRequest, Job, MaintenanceCompletion, MaintenanceRule, Project
from .onboarding import create_owner_invitation

class DailyUseTests(TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.config=override_settings(DATA_DIR=Path(self.tmp.name),MEDIA_ROOT=Path(self.tmp.name)/'media',DEMO_MODE=True)
        self.config.enable();self.addCleanup(self.config.disable)
        (Path(self.tmp.name)/'media').mkdir()
        self.owner=get_user_model().objects.create_user('daily',password='synthetic-test-password')
        self.project=Project.objects.create(owner=self.owner,name='Prüfauto',kind='auto')
        self.entry=Entry.objects.create(project=self.project,title='Ölwechsel',kind='wartung',status='erledigt',date=date(2026,11,12),precision='day',meter=Decimal('165200'),meter_unit='km')
    def rule(self,**changes):
        values=dict(project=self.project,title='Ölwechsel',interval_months=12,interval_km=Decimal('15000'),base_date=date(2025,12,6),base_meter=Decimal('160000'))
        return MaintenanceRule.objects.create(**{**values,**changes})
    def proposal(self,item):
        return {'currency':'EUR','gross_total':item['amount'],'positions':[item]}
    def test_quantity_and_gross_unit_price_are_exact(self):
        item={'description':'Öl','amount':'10.21','quantity':'0.3','unit':'l','unit_price':'34.033333','price_basis':'gross'}
        self.assertEqual(validate_proposal(self.proposal(item))['positions'][0]['unit_price'],'34.033333')
    def test_net_unit_price_does_not_override_stated_gross_amount(self):
        item={'description':'Öl','amount':'11.90','quantity':'1','unit_price':'10','price_basis':'net'}
        self.assertEqual(validate_proposal(self.proposal(item))['gross_total'],'11.90')
    def test_inconsistent_gross_product_rejected(self):
        with self.assertRaises(ValidationError):
            validate_proposal(self.proposal({'description':'Öl','amount':'12.00','quantity':'2','unit_price':'5','price_basis':'gross'}))
    def test_nonfinite_and_excess_precision_rejected(self):
        for value in ['NaN','Infinity','0.1234567']:
            with self.subTest(value=value),self.assertRaises(ValidationError):
                validate_proposal(self.proposal({'description':'Öl','amount':'10.00','quantity':value}))
    def test_old_positions_remain_valid(self):
        validate_proposal(self.proposal({'description':'Altbestand','amount':'10.00'}))
    def test_position_form_round_trip_preserves_localized_details(self):
        rows=Positions({'positions-TOTAL_FORMS':'1','positions-INITIAL_FORMS':'0','positions-0-description':'Öl','positions-0-amount':'10,21','positions-0-quantity':'0,3','positions-0-unit':'l','positions-0-unit_price':'34,033333','positions-0-price_basis':'gross'},prefix='positions')
        self.assertTrue(rows.is_valid(),rows.errors)
        data=position_data(rows)
        self.assertEqual(data[0]['quantity'],'0.3');self.assertEqual(position_initial(data)[0]['unit_price'],'34,033333')
    def test_month_end_and_leap_year(self):
        rule=self.rule(interval_months=1,base_date=date(2024,1,31))
        self.assertEqual(next_due(rule)[0],date(2024,2,29))
    def test_date_or_distance_first_threshold(self):
        rule=self.rule(base_date=date(2026,11,1),interval_months=12,interval_km=Decimal('1000'))
        self.assertTrue(rule_state(rule,today=date(2026,11,12))['due'])
        rule.interval_km=Decimal('50000');rule.base_date=date(2025,1,1)
        self.assertTrue(rule_state(rule,today=date(2026,11,12))['due'])
    def test_zero_base_and_missing_reading_are_distinct(self):
        rule=self.rule(base_meter=Decimal('0'),interval_km=Decimal('200000'))
        self.assertFalse(rule_state(rule,today=date(2026,1,1))['meter_uncertain'])
        self.entry.meter=None;self.entry.save()
        self.assertTrue(rule_state(rule,today=date(2026,1,1))['meter_uncertain'])
    def test_completion_once_advances_from_actual_work(self):
        rule=self.rule();result=complete_maintenance(self.owner,rule,self.entry,1)
        rule.refresh_from_db();self.assertEqual(next_due(rule),(date(2027,11,12),Decimal('180200')))
        self.assertEqual(complete_maintenance(self.owner,rule,self.entry,1).pk,result.pk)
        self.assertEqual(MaintenanceCompletion.objects.count(),1)
        self.assertFalse(self.entry.allocations.exists())
    def test_stale_completion_and_backdated_work_rejected(self):
        rule=self.rule(version=2)
        with self.assertRaises(ValidationError):complete_maintenance(self.owner,rule,self.entry,1)
        self.entry.date=date(2025,1,1);self.entry.save()
        with self.assertRaises(ValidationError):complete_maintenance(self.owner,rule,self.entry,2)
    def test_partial_date_and_foreign_project_rejected(self):
        rule=self.rule();self.entry.precision='month';self.entry.save()
        with self.assertRaises(ValidationError):complete_maintenance(self.owner,rule,self.entry,1)
        other=Project.objects.create(owner=self.owner,name='Anderes Auto')
        self.entry.project=other;self.entry.save()
        with self.assertRaises(ValidationError):complete_maintenance(self.owner,rule,self.entry,1)
    def test_meter_reset_requires_explicit_review(self):
        rule=self.rule();self.entry.meter_replaced=True;self.entry.save()
        self.assertTrue(rule_state(rule,today=date(2026,1,1))['meter_uncertain'])
        with self.assertRaises(ValidationError):complete_maintenance(self.owner,rule,self.entry,1)
    def test_rule_form_requires_intervals_and_bases(self):
        for data in [{'title':'Öl'},{'title':'Öl','interval_months':'0'},{'title':'Öl','interval_months':'12'}]:
            self.assertFalse(MaintenanceForm(data).is_valid())
    def test_compact_pdf_many_entries_on_few_pages(self):
        for index in range(14):
            Entry.objects.create(project=self.project,title='Wartung '+str(index),kind='wartung',status='erledigt',date=date(2025,1,index+1),cost_state='unknown')
        destination=Path(self.tmp.name)/'wartung.pdf';maintenance_pdf(destination,self.project)
        pdf=PdfReader(destination);self.assertLessEqual(len(pdf.pages),3)
        text=' '.join(page.extract_text() for page in pdf.pages)
        self.assertIn('Wartung 13',text);self.assertIn('Unbekannt',text);self.assertIn('15 Vorgänge',text);self.assertIn('165.200 km',text)
    def test_compact_pdf_job_completes_and_downloads_as_pdf(self):
        export=ExportRequest.objects.create(owner=self.owner,project=self.project,kind='wartung')
        self.assertTrue(perform_export(export));export.refresh_from_db()
        self.assertTrue(export.path.endswith('.pdf'))
        self.client.force_login(self.owner)
        response=self.client.get('/export/'+str(export.pk)+'/download/')
        self.assertEqual(response['Content-Type'],'application/pdf');response.close()
    def test_compact_pdf_owner_scope(self):
        foreign=Project.objects.create(owner=self.owner,name='Fremdes Projekt')
        with self.assertRaises(ValidationError):maintenance_pdf(Path(self.tmp.name)/'bad.pdf',foreign,self.entry)
    def test_portable_export_contains_rules_and_completion(self):
        rule=self.rule();complete_maintenance(self.owner,rule,self.entry,1)
        destination=Path(self.tmp.name)/'export.zip';portable_export(destination,self.owner,self.project)
        import zipfile
        with zipfile.ZipFile(destination) as z:
            data=json.loads(z.read('daten.json'))
        names={item['model'] for item in data['objects']}
        self.assertIn('akte.maintenancerule',names);self.assertIn('akte.maintenancecompletion',names)
    def test_cleanup_retains_linked_maintenance_history(self):
        rule=self.rule();complete_maintenance(self.owner,rule,self.entry,1)
        self.entry.deleted_at=timezone.now()-timedelta(days=40);self.entry.save()
        call_command('cleanup',apply=True,stdout=io.StringIO())
        self.assertTrue(Entry.objects.filter(pk=self.entry.pk).exists())
    def test_existing_owner_is_never_replaced(self):
        with self.assertRaises(ValidationError):create_owner_invitation('daily')
    def test_maintenance_page_owner_protection(self):
        rule=self.rule();other=get_user_model().objects.create_user('other')
        self.client.force_login(other)
        self.assertEqual(self.client.get('/wartungsplan/'+str(rule.pk)+'/').status_code,404)

    @override_settings(DEMO_MODE=False,AI_ENABLED=True,AI_MONTHLY_LIMIT=Decimal('5'),AI_INPUT_USD_PER_M=Decimal('.30'),AI_OUTPUT_USD_PER_M=Decimal('2.50'))
    def test_api_budget_is_shared_across_owners(self):
        from .models import AIUsage, Document
        from .worker import reserve_ai
        from .adapters import IntegrationError
        first=Document.objects.create(owner=self.owner,project=self.project,ai_enabled=True)
        AIUsage.objects.create(owner=self.owner,document=first,estimated_usd=Decimal('4.99'),model='test')
        other=get_user_model().objects.create_user('budget-other')
        second=Document.objects.create(owner=other,ai_enabled=True)
        with self.assertRaises(IntegrationError):reserve_ai(second)

    def test_waitress_delegates_proxy_trust_to_wsgi_boundary(self):
        from serve import proxy_options
        self.assertEqual(proxy_options(),{'clear_untrusted_proxy_headers':False})
    def test_login_rate_limit_and_other_clients(self):
        from .security import security_cache
        security_cache().clear();self.addCleanup(security_cache().clear)
        self.assertEqual(self.client.post('/login/',{'username':'missing','password':'invalid'}).status_code,200)
        self.assertEqual(self.client.post('/login/',{'username':'missing','password':'invalid'}).status_code,429)
        self.assertEqual(self.client.post('/login/',{'username':'missing','password':'invalid'},REMOTE_ADDR='127.0.0.2').status_code,200)

    def test_pending_import_visible_only_to_owner(self):
        from .models import ImportBatch
        batch=ImportBatch.objects.create(owner=self.owner,project=self.project,sha256='a'*64,source_name='BMW-Prüfquelle.pdf',rows=[{'title':'Ölwechsel'}])
        self.client.force_login(self.owner)
        self.assertContains(self.client.get('/'),'BMW-Prüfquelle.pdf')
        batch.confirmed=True;batch.save()
        self.assertNotContains(self.client.get('/'),'BMW-Prüfquelle.pdf')

    @override_settings(PAPERLESS_URL='http://paperless-test',PAPERLESS_TOKEN='synthetic-token')
    def test_restricted_task_api_resolves_only_own_submission(self):
        import uuid
        from types import SimpleNamespace
        from unittest.mock import Mock,patch
        from akte.adapters import LivePaperless,TaskAccessDenied
        doc=SimpleNamespace(task_id=str(uuid.uuid4()),submission_key=uuid.uuid4())
        response=Mock();response.json.return_value={'results':[{'id':19,'title':'Projektakte '+str(doc.submission_key)}]}
        adapter=LivePaperless()
        with patch.object(LivePaperless,'request',side_effect=[TaskAccessDenied(),response]):self.assertEqual(adapter.poll(doc),19)
    @override_settings(PAPERLESS_URL='http://paperless-test',PAPERLESS_TOKEN='synthetic-token')
    def test_missing_submission_after_task_denial_is_pending_without_upload(self):
        import uuid
        from types import SimpleNamespace
        from unittest.mock import Mock,patch
        from akte.adapters import LivePaperless,TaskAccessDenied,Pending
        doc=SimpleNamespace(task_id=str(uuid.uuid4()),submission_key=uuid.uuid4())
        response=Mock();response.json.return_value={'results':[{'id':20,'title':'Andere Rechnung'}]}
        adapter=LivePaperless()
        with patch.object(LivePaperless,'request',side_effect=[TaskAccessDenied(),response]),self.assertRaises(Pending):adapter.poll(doc)

    def test_accepted_position_details_remain_visible(self):
        from .models import CostSource
        source=CostSource.objects.create(owner=self.owner,label='Öl',amount=Decimal('10.21'),currency='EUR',positions=[{'description':'Motoröl','amount':'10.21','quantity':'0.3','unit':'l','unit_price':'34.033333','price_basis':'gross'}])
        self.client.force_login(self.owner)
        page=self.client.get('/kosten/'+str(source.pk)+'/')
        self.assertContains(page,'34,033333');self.assertContains(page,'brutto');self.assertContains(page,'0,3')
    def test_stale_completion_in_browser_returns_a_clear_message(self):
        rule=self.rule(version=2);self.client.force_login(self.owner)
        response=self.client.post('/wartungsplan/',{'action':'complete','rule':str(rule.pk),'entry':str(self.entry.pk),'version':'1'},follow=True)
        self.assertEqual(response.status_code,200);self.assertContains(response,'Wartungsplan wurde geändert')
        self.assertEqual(MaintenanceCompletion.objects.count(),0)
