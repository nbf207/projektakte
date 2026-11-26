"""Focused tests for the 0.7.3 project builder and its save paths."""
import copy
import uuid
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from . import tests_release072 as prior_tests
from .models import Project, Entry, TitleField, Category, MaintenanceRule, CostSource, Document
from .forms import EntryForm
from .definitions import catalog, field_name, validate_ai_definition
from .project_config import meter_warning
from .project_routing import ai_context
from .capture_ai import capture_context
from .services import save_entry, change_deleted
from .maintenance import next_due
from .categories import default_category


class Release073Tests(TestCase):
    setUp=prior_tests.Release072Tests.setUp
    selection=prior_tests.Release072Tests.selection
    proposal=prior_tests.Release072Tests.proposal
    entry_payload=prior_tests.Release072Tests.entry_payload

    def test_shared_fixed_form_and_project_fields_with_suggestions(self):
        field=TitleField.objects.create(project=self.project,label='Werkzeug')
        payload=self.entry_payload(template=self.oil)
        payload[field_name(field)]='SYN Ratsche';payload['extra_version_'+field.pk.hex]=field.version
        response=self.client.post(reverse('entry_new',args=[self.project.pk]),payload)
        self.assertEqual(response.status_code,302)
        entry=Entry.objects.get(request_key=payload['token'])
        self.assertEqual(next(r for r in entry.snapshot['extra_fields'] if r['id']==str(field.pk))['value'],'SYN Ratsche')
        definitions=catalog([self.project]);self.assertEqual(definitions[0]['fields'][0]['suggestions'],['SYN Ratsche'])
        page=self.client.get(reverse('entry_edit',args=[entry.pk]))
        self.assertContains(page,'entry-title-grid',count=1)
        self.assertContains(page,'id="entry-definitions"',count=1)
        payload=self.entry_payload(entry,title='Freier Titel')
        payload[field_name(field)]='SYN Ratsche';payload['extra_version_'+field.pk.hex]=field.version
        self.assertEqual(self.client.post(reverse('entry_edit',args=[entry.pk]),payload).status_code,302)
        entry.refresh_from_db();self.assertIsNone(entry.title_template_id)
        self.assertEqual(len(entry.snapshot['extra_fields']),1)
        self.assertEqual(entry.snapshot['oil'],'SYN 5W30')
        self.assertEqual(catalog([self.other_project])[0]['fields'],[])
        bad=self.entry_payload(entry)
        bad[field_name(self.fields[0])]='not allowed'
        self.assertFalse(EntryForm(bad,owner=self.user,project=self.project,entry=entry).is_valid())

    def test_settings_tree_crud_scope_and_ownership(self):
        url=reverse('settings');page=self.client.get(url,{'project':self.project.pk})
        self.assertEqual(page.status_code,200);self.assertContains(page,'id="projekt-stammdaten"')
        identity=uuid.uuid4()
        payload={'project':self.project.pk,'scope':'field','object_id':identity,'version':0,'action':'save',
                 'template':'','label':'SYN global','field_type':'text','unit':'','placeholder':''}
        response=self.client.post(url,payload);self.assertEqual(response.status_code,302, getattr(response,'context',None))
        field=TitleField.objects.get(pk=identity);self.assertEqual(field.project_id,self.project.pk)
        payload.update(project=self.other_project.pk,version=field.version,label='foreign')
        self.assertEqual(self.client.post(url,payload).status_code,404)
        payload.update(project=self.project.pk,action='delete')
        self.assertEqual(self.client.post(url,payload).status_code,302)
        self.assertFalse(TitleField.objects.filter(pk=identity).exists())
        response=self.client.post(url,{'project':self.project.pk,'scope':'template','object_id':self.oil.pk,
            'version':self.oil.version,'action':'save','category':self.oil.category_id,'name':self.oil.name,
            'interval_days':30,'interval_meter':1000,'meter_unit':'h'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(MaintenanceRule.objects.get(title_template=self.oil).meter_unit,'h')

    def test_master_fields_order_retirement_and_owner_guard(self):
        self.project.fields={'FIN':{'type':'text','value':'SYN VIN'},'Altfeld':{'type':'text','value':'KEEP'}};self.project.save()
        payload={'scope':'master','project':self.project.pk,'version':self.project.version,
            'fields-TOTAL_FORMS':3,'fields-INITIAL_FORMS':2,'fields-MIN_NUM_FORMS':0,'fields-MAX_NUM_FORMS':100,
            'fields-0-name':'FIN','fields-0-original_name':'FIN','fields-0-kind':'text','fields-0-value':'SYN VIN','fields-0-active':'on','fields-0-position':8,
            'fields-1-name':'Altfeld','fields-1-original_name':'Altfeld','fields-1-kind':'text','fields-1-value':'KEEP','fields-1-DELETE':'on',
            'fields-2-name':'Drehmoment','fields-2-kind':'number','fields-2-value':'120','fields-2-unit':'Nm','fields-2-active':'on','fields-2-position':1}
        self.assertEqual(self.client.post(reverse('settings'),payload).status_code,302)
        self.project.refresh_from_db();self.assertEqual(list(self.project.fields)[:2],['Drehmoment','FIN'])
        self.assertEqual(self.project.fields['Altfeld']['value'],'KEEP');self.assertFalse(self.project.fields['Altfeld']['active'])
        self.assertEqual(self.client.post(reverse('settings'),payload).status_code,200)

    def test_gemini_context_and_semantic_category_selection(self):
        self.project.ai_profile='SYN Bauteile BMW, Pflege nur Reinigung';self.project.save()
        field=TitleField.objects.create(project=self.project,label='SYN project field')
        context=capture_context(self.user,self.project)
        self.assertEqual(context['project_catalog'][0]['ai_profile'],self.project.ai_profile)
        doc=Document.objects.create(owner=self.user,project=self.project)
        self.assertEqual(ai_context(doc)['project']['ai_profile'],self.project.ai_profile)
        p=self.proposal();p['entry']['title']='Airbag-Tausch';p['entry']['kind']='reparatur'
        care=Category.objects.get(project=self.project,name='Pflege');care.default_kind='reparatur';care.save()
        repair=Category.objects.get(project=self.project,name='Reparatur')
        self.assertEqual(default_category(self.project,'reparatur'),repair)
        p['definition']={'category_id':str(care.pk),'category_version':care.version,'template_id':None,'template_version':None,'suffix':None,'values':[]}
        with self.assertRaises(ValidationError):validate_ai_definition(p,capture_context(self.user,self.project))
        p['definition'].update(category_id=str(repair.pk),category_version=repair.version,
            values=[{'field_id':str(field.pk),'field_version':field.version,'value':'SYN','inferred':False}])
        validate_ai_definition(p,capture_context(self.user,self.project))
        p['definition']['values'][0]['field_version']+=1
        with self.assertRaises(ValidationError):validate_ai_definition(p,capture_context(self.user,self.project))

    def test_title_maintenance_create_edit_retry_trash_and_unknown(self):
        self.oil.interval_months=12;self.oil.interval_days=2;self.oil.interval_meter=Decimal('10000');self.oil.save()
        payload=self.entry_payload(template=self.oil);payload.update(date_text='2026-01-31',meter_text='90000',meter_unit='km')
        self.client.post(reverse('entry_new',args=[self.project.pk]),payload)
        entry=Entry.objects.get(request_key=payload['token'])
        rule=MaintenanceRule.objects.get(title_template=self.oil)
        self.assertEqual(next_due(rule),(date(2027,2,2),Decimal('100000')))
        self.client.post(reverse('entry_new',args=[self.project.pk]),payload)
        self.assertEqual(MaintenanceRule.objects.filter(title_template=self.oil).count(),1)
        payload=self.entry_payload(entry,template=self.oil);payload.update(date_text='2026-02-03',meter_text='91000',meter_unit='km')
        self.client.post(reverse('entry_edit',args=[entry.pk]),payload);entry.refresh_from_db();rule.refresh_from_db()
        self.assertEqual(rule.base_date,date(2026,2,3));self.assertEqual(rule.base_meter,Decimal('91000'))
        change_deleted(self.user,entry,entry.version);rule.refresh_from_db();self.assertIsNone(rule.base_date)
        self.assertEqual(CostSource.objects.count(),0)

    def test_meter_warning_is_nonblocking_and_chronological(self):
        Entry.objects.create(project=self.project,title='older',date=date(2026,1,1),meter=90000)
        Entry.objects.create(project=self.project,title='future',date=date(2028,1,1),meter=99999)
        payload=self.entry_payload(template=self.oil);payload.update(date_text='2027-01-01',meter_text='89000',meter_unit='km')
        result=self.client.post(reverse('entry_new',args=[self.project.pk]),payload,follow=True)
        self.assertContains(result,'liegt unter dem vorherigen Eintrag')
        entry=Entry.objects.get(request_key=payload['token']);entry.meter_replaced=True
        self.assertEqual(meter_warning(entry),'')

    def test_project_management_tabs_archive_and_auto_template(self):
        url=reverse('settings')
        payload={'scope':'project','project':self.project.pk,'version':self.project.version,'name':'SYN renamed',
            'position':-4,'color':'#123456','icon':'B','ai_profile':'SYN profile','hidden_tabs':['teile','umbauten'],'archived':'on'}
        response=self.client.post(url,payload);self.assertEqual(response.status_code,302)
        self.project.refresh_from_db();self.assertEqual(self.project.name,'SYN renamed');self.assertTrue(self.project.archived)
        page=self.client.get(reverse('project',args=[self.project.pk]));self.assertNotContains(page,'?tab=teile');self.assertNotContains(page,'?tab=umbauten')
        self.assertNotContains(self.client.get(reverse('home')),f'<option value="{self.project.pk}"')
        self.project.fields={'FIN':{'type':'text','value':'SYN KEEP'}};self.project.save()
        self.client.post(url,{'scope':'auto_template','project':self.project.pk,'version':self.project.version})
        self.project.refresh_from_db();self.assertEqual(self.project.fields['FIN']['value'],'SYN KEEP')
        self.assertIn('Motorcode',self.project.fields);self.assertIn('Leistung PS',self.project.fields)

    def test_invoice_uses_same_editor_and_keeps_single_entry_and_cost(self):
        from .scan_entries import ensure_scan_entry
        p=self.proposal();p.update(gross_total='110.00',currency='EUR',positions=[{'description':'SYN oil','part_number':None,'amount':'110.00','quantity':None,'unit':None,'unit_price':None,'price_basis':'unknown'}])
        doc=Document.objects.create(owner=self.user,project=self.project,status='review',proposal=p,paperless_id=101,remote_state='current',verified_at=timezone.now())
        doc=ensure_scan_entry(doc)
        page=self.client.get(reverse('review',args=[doc.pk]));self.assertContains(page,'entry-title-grid',count=1)
        entry_form=page.context['entry_form'];payload={}
        for field in entry_form:
            value=field.value()
            if value not in (None,False):payload[field.html_name]=str(value)
        for field in self.oil.extra_fields.all():payload['chronicle-extra_version_'+field.pk.hex]=field.version
        payload.update(action='accept',version=doc.version,gross_total='110.00',currency='EUR',keep_unallocated='on',
            positions_editor='expert',positions=__import__('json').dumps(p['positions']),chronicle_mode='existing',chronicle_project=self.project.pk,chronicle_entry=doc.entry_id)
        payload['chronicle-title_suffix']='SYN review';payload['chronicle-date_text']='2026-03-01'
        payload['action']='save'
        self.assertEqual(self.client.post(reverse('review',args=[doc.pk]),payload).status_code,302)
        doc.refresh_from_db();self.assertEqual(CostSource.objects.count(),0)
        saved=self.client.get(reverse('review',args=[doc.pk])).context['entry_form']
        self.assertEqual(saved['title_suffix'].value(),'SYN review')
        payload.update(action='accept',version=doc.version)
        result=self.client.post(reverse('review',args=[doc.pk]),payload)
        self.assertEqual(result.status_code,302, getattr(result,'context',None) and result.context['form'].errors)
        doc.refresh_from_db();doc.entry.refresh_from_db()
        self.assertEqual(doc.status,'accepted');self.assertEqual(doc.entry.title,'Ölwechsel SYN review')
        self.assertEqual(doc.entry.date,date(2026,3,1));self.assertEqual(doc.entry.title_template_id,self.oil.pk)
        self.client.post(reverse('review',args=[doc.pk]),payload)
        self.assertEqual(CostSource.objects.count(),1);self.assertEqual(Entry.objects.count(),1)


class Release073MigrationTest(TransactionTestCase):
    def test_072_existing_definitions_snapshots_costs_survive(self):
        MigrationExecutor(connection).migrate([('akte','0019_project_entry_definitions')])
        old=MigrationExecutor(connection).loader.project_state([('akte','0019_project_entry_definitions')]).apps
        user=old.get_model('auth','User').objects.create(username='SYN migration073')
        project=old.get_model('akte','Project').objects.create(owner_id=user.pk,name='SYN old',fields={'FIN':{'type':'text','value':'KEEP'}})
        category=old.get_model('akte','Category').objects.create(project=project,owner_id=user.pk,name='Wartung',name_key='wartung')
        template=old.get_model('akte','TitleTemplate').objects.create(category=category,name='Ölwechsel')
        field=old.get_model('akte','TitleField').objects.create(template=template,label='Motoröl',legacy_key='oil')
        entry=old.get_model('akte','Entry').objects.create(project=project,category=category,title_template=template,title='Ölwechsel',snapshot={'oil':'KEEP','extra_fields':[{'id':str(field.pk),'value':'KEEP'}]})
        MigrationExecutor(connection).migrate([('akte','0020_project_builder')])
        current=Entry.objects.get(pk=entry.pk)
        self.assertEqual(current.snapshot,entry.snapshot);self.assertEqual(current.title_template_id,template.pk)
        self.assertEqual(TitleField.objects.get(pk=field.pk).template_id,template.pk)
        self.assertEqual(current.project.fields,project.fields)
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA foreign_key_check');self.assertEqual(cursor.fetchall(),[])
