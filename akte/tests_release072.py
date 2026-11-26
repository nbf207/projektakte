"""Focused 0.7.2 regression tests; no real user data or external requests."""
import copy
import json
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from .backup import backup
from .capture import receive_capture, receipt_draft, read_inputs, commit_capture
from .capture_ai import capture_context, validate_capture
from .definitions import catalog, ensure_project_definitions, field_name, validate_ai_definition
from .demo import synthetic_pdf, synthetic_photo
from .files import ingest_asset
from .maintenance import complete_maintenance, next_due
from .models import (Allocation, Asset, Category, CostSource, DiscardedDraft, Document, Draft,
                     Entry, Job, MaintenanceCompletion, MaintenanceRule, PhotoLink, Project,
                     Step, Task, TitleTemplate, TitleField)
from .proposal_schema import empty_entry
from .services import save_entry
from .scan_entries import apply_scan_proposal
from .project_routing import assign_project
from .worker import commit_job, reserve, run_one


class Release072Tests(TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.data=Path(temporary.name)
        override=override_settings(DATA_DIR=self.data,MEDIA_ROOT=self.data/'media',DEMO_MODE=True,ALLOWED_HOSTS=['testserver'])
        override.enable();self.addCleanup(override.disable)
        self.user=get_user_model().objects.create_user('release072')
        self.project=Project.objects.create(owner=self.user,name='SYN BMW',kind='auto')
        self.other_project=Project.objects.create(owner=self.user,name='SYN second',kind='auto')
        ensure_project_definitions(self.project);ensure_project_definitions(self.other_project)
        self.oil=TitleTemplate.objects.get(category__project=self.project,name='Ölwechsel')
        self.fields=list(self.oil.extra_fields.all())
        self.client.force_login(self.user)

    def selection(self, template=None, suffix=None, values=None):
        template=template or self.oil;category=template.category
        return {'category_id':str(category.pk),'category_version':category.version,
                'template_id':str(template.pk),'template_version':template.version,'suffix':suffix,
                'values':[{'field_id':str(f.pk),'field_version':f.version,'value':(values or {}).get(f.legacy_key or f.label),'inferred':False} for f in template.extra_fields.all()]}

    def proposal(self):
        entry=empty_entry();entry.update(title='Ölwechsel vor dem Winter',kind='wartung')
        return {'invoice_number':None,'vendor':None,'invoice_date':None,'service_date':None,'odometer_km':None,
                'gross_total':None,'net_total':None,'tax_total':None,'currency':None,'positions':[],
                'entry':entry,'inferred_fields':[],
                'project_assignment':{'project_id':str(self.project.pk),'confidence':'high','reason':'Projekt ausdrücklich genannt.'},
                'definition':self.selection(suffix='vor dem Winter',values={'oil':'SYN 5W30','quantity_l':'4.5'})}

    def entry_payload(self, entry=None, template=None, category=None, title='SYN eigener Titel', suffix=''):
        category=category or (template.category if template else None)
        result={'title':title,'kind':'wartung','category':str(category.pk) if category else '',
                'category_version':category.version if category else '',
                'title_template':str(template.pk) if template else '', 'template_version':template.version if template else '',
                'title_suffix':suffix,'status':entry.status if entry else 'erledigt','cost_state':'unknown',
                'token':str(uuid.uuid4()),'version':entry.version if entry else 0}
        if template:
            for field in template.extra_fields.all():
                result[field_name(field)]={'oil':'SYN 5W30','quantity_l':'4,5'}.get(field.legacy_key,'')
                result['extra_version_'+field.pk.hex]=field.version
        return result

    def test_templates_suffix_fields_and_free_title_are_project_scoped(self):
        page=self.client.get(reverse('entry_new',args=[self.project.pk]))
        self.assertEqual(page.status_code,200)
        self.assertTrue(page.context['form'].fields['oil'].widget.is_hidden)
        self.assertEqual(len(page.context['form'].dynamic_fields),3,repr(page.context['form'].initial))
        payload=self.entry_payload(template=self.oil,suffix='vor dem Winter')
        response=self.client.post(reverse('entry_new',args=[self.project.pk]),payload)
        self.assertEqual(response.status_code,302)
        entry=Entry.objects.get(request_key=payload['token'])
        self.assertEqual(entry.title,'Ölwechsel vor dem Winter');self.assertEqual(entry.title_template_id,self.oil.pk)
        self.assertEqual(entry.snapshot['quantity_l'],'4.5')
        self.assertContains(self.client.get(reverse('entry',args=[entry.pk])),'SYN 5W30')
        self.assertContains(self.client.get(reverse('project',args=[self.project.pk])),'SYN 5W30')
        foreign=TitleTemplate.objects.get(category__project=self.other_project,name='Ölwechsel')
        invalid=self.entry_payload(entry,template=foreign)
        self.assertEqual(self.client.post(reverse('entry_edit',args=[entry.pk]),invalid).status_code,200)
        entry.refresh_from_db();self.assertEqual(entry.title_template_id,self.oil.pk)
        free=self.entry_payload(entry,category=self.oil.category,title='SYN freier Titel')
        self.assertEqual(self.client.post(reverse('entry_edit',args=[entry.pk]),free).status_code,302)
        entry.refresh_from_db();self.assertIsNone(entry.title_template_id);self.assertEqual(entry.snapshot['extra_fields'],[])
        self.assertTrue(entry.snapshot['_previous_extra_fields'])
        form=self.client.get(reverse('entry_edit',args=[entry.pk])).context['form']
        self.assertEqual(form.dynamic_fields,[])

    def test_umbau_edit_preserves_costs_files_steps_tasks_and_search(self):
        category=Category.objects.create(owner=self.user,project=self.project,name='Umbauten')
        entry=Entry.objects.create(project=self.project,title='SYN old work',kind='wartung',category=self.oil.category,cost_state='known')
        step=Step.objects.create(entry=entry,title='SYN Schritt')
        task=Task.objects.create(project=self.project,entry=entry,title='SYN todo',category=entry.category)
        asset=ingest_asset(self.user,SimpleUploadedFile('SYN.jpg',synthetic_photo(72)))
        PhotoLink.objects.create(entry=entry,asset=asset)
        doc=Document.objects.create(owner=self.user,entry=entry,project=self.project,status='accepted')
        source=CostSource.objects.create(owner=self.user,document=doc,label='SYN bill',amount=Decimal('12.34'))
        Allocation.objects.create(source=source,project=self.project,entry=entry,amount=source.amount)
        manual=CostSource.objects.create(owner=self.user,label='SYN manual',amount=Decimal('5.00'))
        Allocation.objects.create(source=manual,project=self.project,entry=entry,amount=manual.amount)
        page=self.client.get(reverse('entry_edit',args=[entry.pk]));self.assertEqual(page.context['form'].initial['amount'],'5,00')
        payload=self.entry_payload(entry,category=category,title='SYN new work');payload.update(cost_state='known',amount='5,00')
        self.assertEqual(self.client.post(reverse('entry_edit',args=[entry.pk]),payload).status_code,302)
        entry.refresh_from_db();task.refresh_from_db()
        self.assertEqual(entry.kind,'umbau');self.assertEqual(entry.cost,Decimal('17.34'));self.assertEqual(task.category_id,category.pk)
        self.assertEqual(Step.objects.get(pk=step.pk).entry_id,entry.pk);self.assertTrue(Path(asset.file.path).exists())
        self.assertContains(self.client.get(reverse('project',args=[self.project.pk]),{'tab':'umbauten'}),'SYN new work')
        self.assertContains(self.client.get(reverse('search'),{'q':'SYN new work'}),'SYN new work')
        save_entry(self.user,self.project,{'description':'SYN partial edit'},uuid.uuid4(),entry,entry.version)
        entry.refresh_from_db();self.assertEqual(entry.cost,Decimal('17.34'));self.assertEqual(CostSource.objects.count(),2)
        self.assertEqual(Entry.objects.count(),1)

    def test_existing_maintenance_completion_is_updated_without_new_completion(self):
        entry=Entry.objects.create(project=self.project,title='SYN service',kind='wartung',category=self.oil.category,
            date=date(2026,10,1),precision='day',meter=1000,meter_unit='km',status='erledigt')
        rule=MaintenanceRule.objects.create(project=self.project,title='SYN recurring',interval_months=12,interval_km=500,
            base_date=date(2025,10,1),base_meter=500)
        complete_maintenance(self.user,rule,entry,rule.version)
        save_entry(self.user,self.project,{'date':date(2026,11,1),'meter':Decimal('1100')},uuid.uuid4(),entry,entry.version)
        rule.refresh_from_db();entry.refresh_from_db()
        self.assertEqual(rule.base_date,entry.date);self.assertEqual(rule.base_meter,entry.meter)
        self.assertEqual(next_due(rule),(date(2027,11,1),Decimal('1600')))
        self.assertEqual(MaintenanceCompletion.objects.count(),1)
        with self.assertRaises(ValidationError):save_entry(self.user,self.project,{'status':'geplant'},uuid.uuid4(),entry,entry.version)
        entry.refresh_from_db();self.assertEqual(entry.status,'erledigt')

    def test_settings_crud_is_scoped_and_historical_field_values_survive(self):
        url=reverse('settings')
        self.assertEqual(self.client.get(url,{'project':self.project.pk}).context['nav_section'],'settings')
        category_id=uuid.uuid4()
        data={'project':self.project.pk,'scope':'category','object_id':category_id,'version':0,'name':'SYN eigene Kategorie','action':'save'}
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertFalse(Category.objects.filter(project=self.other_project,name=data['name']).exists())
        template_id=uuid.uuid4()
        self.assertEqual(self.client.post(url,{'project':self.project.pk,'scope':'template','object_id':template_id,'version':0,'category':category_id,'name':'SYN Prüfung','action':'save'}).status_code,302)
        field_id=uuid.uuid4()
        self.assertEqual(self.client.post(url,{'project':self.project.pk,'scope':'field','object_id':field_id,'version':0,'template':template_id,'label':'SYN Spannung','field_type':'number','unit':'V','placeholder':'12,6','action':'save'}).status_code,302)
        entry=Entry.objects.create(project=self.project,title='SYN Prüfung',category_id=category_id,title_template_id=template_id,
            snapshot={'extra_fields':[{'id':str(field_id),'label':'SYN Spannung','type':'number','unit':'V','value':'12.6'}]})
        field=TitleField.objects.get(pk=field_id)
        self.assertEqual(self.client.post(url,{'project':self.project.pk,'scope':'field','object_id':field_id,'version':field.version,'action':'delete'}).status_code,302)
        self.assertContains(self.client.get(reverse('entry',args=[entry.pk])),'12.6')
        other=get_user_model().objects.create_user('PRIVATE user');foreign=Project.objects.create(owner=other,name='PRIVATE project')
        self.assertEqual(self.client.get(url,{'project':foreign.pk}).status_code,404)
        self.assertEqual(self.client.post(url,{'project':self.other_project.pk,'scope':'category','object_id':category_id,'version':2,'action':'delete'}).status_code,404)

    def test_gemini_context_validation_and_capture_persistence(self):
        proposal=self.proposal();context=capture_context(self.user,self.project)
        self.assertIsInstance(context['project_catalog'],list)
        json.dumps(context)
        self.assertNotIn(str(self.other_project.pk),json.dumps(context['entry_definitions']))
        result={'knowledge':[],'transcript':'','category':'wartung','is_receipt':False,'receipt_files':[],
                'open_entry':{'id':None,'confidence':'none','reason':None},'proposal':proposal}
        validate_capture(result,context)
        doc=receive_capture(self.user,text='SYN work',project=self.project,token=uuid.uuid4())
        with patch('akte.capture.analyze_capture',return_value=result):self.assertTrue(run_one())
        doc.refresh_from_db();entry=doc.entry
        self.assertEqual(doc.status,'accepted');self.assertEqual(entry.title_template_id,self.oil.pk)
        self.assertEqual(entry.title_suffix,'vor dem Winter');self.assertEqual(entry.snapshot['quantity_l'],'4.5')
        self.assertEqual(CostSource.objects.count(),0);self.assertFalse(run_one())
        invalid=copy.deepcopy(proposal);invalid['definition']['values'][0]['field_id']=str(uuid.uuid4())
        with self.assertRaises(ValidationError):validate_ai_definition(invalid,context)
        free=copy.deepcopy(proposal);free['definition'].update(template_id=None,template_version=None,suffix=None)
        with self.assertRaises(ValidationError):validate_ai_definition(free,context)
        stale=copy.deepcopy(proposal);stale['definition']['template_version']+=1
        with self.assertRaises(ValidationError):validate_ai_definition(stale,context)

    def test_template_move_updates_links_and_deleted_template_keeps_history(self):
        target=Category.objects.create(owner=self.user,project=self.project,name='Umbauten')
        entry=Entry.objects.create(project=self.project,category=self.oil.category,title_template=self.oil,title='Ölwechsel',kind='wartung',
            snapshot={'extra_fields':[{'id':str(self.fields[1].pk),'label':'Ölmenge','type':'number','unit':'l','value':'4.5'}],'title_template_name':'Ölwechsel'})
        task=Task.objects.create(project=self.project,entry=entry,category=entry.category,title='SYN linked task')
        url=reverse('settings')
        response=self.client.post(url,{'scope':'template','project':self.project.pk,'object_id':self.oil.pk,'version':self.oil.version,'category':target.pk,'name':self.oil.name,'action':'save'})
        self.assertEqual(response.status_code,302)
        entry.refresh_from_db();task.refresh_from_db()
        self.assertEqual(entry.kind,'umbau');self.assertEqual(entry.category_id,target.pk);self.assertEqual(task.category_id,target.pk)
        self.oil.refresh_from_db()
        self.assertEqual(self.client.post(url,{'scope':'template','project':self.project.pk,'object_id':self.oil.pk,'version':self.oil.version,'action':'delete'}).status_code,302)
        entry.refresh_from_db();self.assertIsNone(entry.title_template_id)
        self.assertEqual(self.client.post(reverse('entry_edit',args=[entry.pk]),self.entry_payload(entry,category=target,title='SYN free')).status_code,302)
        entry.refresh_from_db()
        retained=entry.snapshot.get('extra_fields') or (entry.snapshot.get('_previous_extra_fields')[-1]['fields'] if entry.snapshot.get('_previous_extra_fields') else [])
        self.assertEqual(retained[0]['value'],'4.5')

    def test_delete_image_capture_cleans_files_and_fences_delayed_jobs(self):
        doc=receive_capture(self.user,uploads=[SimpleUploadedFile('SYN.jpg',synthetic_photo(73))],project=self.project,token=uuid.uuid4())
        original=Path(doc.pages.get().asset.file.path);entry_id=doc.entry_id
        # Reserve the capture, as though Gemini is still on the network.
        Job.objects.filter(kind='thumbnail').update(status='done')
        job=reserve();self.assertEqual(job.document_id,doc.pk)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.post(reverse('document_draft_delete',args=[doc.pk]),{'version':doc.version}).status_code,302)
        self.assertFalse(Document.objects.filter(pk=doc.pk).exists());self.assertFalse(Entry.objects.filter(pk=entry_id).exists())
        self.assertFalse(Asset.objects.exists());self.assertFalse(original.exists())
        self.assertFalse(commit_job(job,{'status':'review'},status='done'))
        with self.assertRaises(ValidationError):receive_capture(self.user,text='SYN retry',project=self.project,token=doc.submission_key)

    def test_manual_title_fields_survive_a_delayed_ai_result(self):
        doc=receive_capture(self.user,text='SYN pending',project=self.project,token=uuid.uuid4())
        definition=self.selection(suffix='manuell korrigiert',values={'oil':'SYN 10W40','quantity_l':'5.1'})
        entry=save_entry(self.user,self.project,{'_definition':definition},uuid.uuid4(),doc.entry,doc.entry.version)
        apply_scan_proposal(doc,self.proposal())
        entry.refresh_from_db();self.assertEqual(entry.title_suffix,'manuell korrigiert')
        self.assertEqual(entry.snapshot['quantity_l'],'5.1');self.assertEqual(entry.snapshot['oil'],'SYN 10W40')

    def test_invoice_confirmation_keeps_later_manual_date_and_meter(self):
        doc=receive_capture(self.user,text='SYN pending',project=self.project,token=uuid.uuid4())
        proposal=self.proposal();doc.proposal=proposal;doc.save(update_fields=['proposal'])
        apply_scan_proposal(doc,proposal)
        entry=save_entry(self.user,self.project,{'date':date(2026,11,14),'precision':'day','meter':Decimal('123456'),'status':'erledigt'},uuid.uuid4(),doc.entry,doc.entry.version)
        apply_scan_proposal(doc,proposal,confirmed=True)
        entry.refresh_from_db();self.assertEqual(entry.date,date(2026,11,14));self.assertEqual(entry.meter,Decimal('123456'));self.assertEqual(entry.status,'erledigt')

    def test_settings_changed_during_ai_processing_are_not_applied(self):
        doc=receive_capture(self.user,text='SYN pending',project=self.project,token=uuid.uuid4())
        result={'transcript':'','category':'wartung','is_receipt':False,'receipt_files':[],
                'open_entry':{'id':None,'confidence':'none','reason':None},'proposal':self.proposal()}
        job=reserve();field=self.fields[0];field.version+=1;field.save()
        with self.assertRaises(ValidationError):commit_capture(job,result,[],doc.version)
        doc.refresh_from_db();self.assertEqual(doc.proposal,{})
        self.assertIsNone(doc.entry.title_template_id)
        foreign=get_user_model().objects.create_user('foreign-draft');self.client.force_login(foreign)
        self.assertEqual(self.client.post(reverse('document_draft_delete',args=[doc.pk]),{'version':doc.version}).status_code,404)

    def test_manual_project_correction_drops_foreign_template_ids_without_losing_values(self):
        doc=receive_capture(self.user,text='SYN wrong project',project=self.project,token=uuid.uuid4())
        doc.proposal=self.proposal();doc.save(update_fields=['proposal']);apply_scan_proposal(doc,doc.proposal)
        corrected=assign_project(self.user,doc,self.other_project,doc.version)
        corrected.entry.refresh_from_db()
        self.assertEqual(corrected.entry.project_id,self.other_project.pk)
        self.assertEqual(corrected.entry.category.project_id,self.other_project.pk)
        self.assertIsNone(corrected.entry.title_template_id)
        self.assertTrue(corrected.entry.snapshot['_previous_extra_fields'])
        self.assertEqual(corrected.entry.snapshot['oil'],'SYN 5W30')

    def test_custom_template_category_keeps_ai_work_classification(self):
        category=Category.objects.create(owner=self.user,project=self.project,name='SYN Optik')
        template=TitleTemplate.objects.create(category=category,name='SYN Tuning')
        field=TitleField.objects.create(template=template,label='Anzugsmoment',field_type='number',unit='Nm')
        proposal=self.proposal();proposal['entry']['kind']='umbau'
        proposal['definition']=self.selection(template,suffix='vorn',values={'Anzugsmoment':'140'})
        validate_ai_definition(proposal,capture_context(self.user,self.project))
        self.assertEqual(proposal['entry']['kind'],'umbau')
        doc=receive_capture(self.user,text='SYN tuning',project=self.project,token=uuid.uuid4())
        apply_scan_proposal(doc,proposal);doc.entry.refresh_from_db()
        self.assertEqual(doc.entry.kind,'umbau');self.assertEqual(doc.entry.title_template_id,template.pk)
        self.assertEqual(doc.entry.snapshot['extra_fields'][0]['value'],'140')

    def test_definition_interpretation_paths_become_visible_field_marks(self):
        proposal=self.proposal()
        proposal['inferred_fields']=['definition.template_id','definition.suffix','definition.values.0.value','definition.category_version','definition.template_version','definition.values.0.field_version']
        result={'knowledge':[],'transcript':'','category':'wartung','is_receipt':False,'receipt_files':[],
                'open_entry':{'id':None,'confidence':'none','reason':None},'proposal':proposal}
        validate_capture(result,capture_context(self.user,self.project))
        self.assertNotIn('definition.category_version',proposal['inferred_fields'])
        self.assertTrue(proposal['definition']['values'][0]['inferred'])
        doc=receive_capture(self.user,text='SYN interpreted',project=self.project,token=uuid.uuid4())
        apply_scan_proposal(doc,proposal);doc.entry.refresh_from_db()
        self.assertTrue(doc.entry.snapshot['extra_fields'][0]['inferred'])
        self.assertIn('definition.suffix',doc.entry.snapshot['_definition_inferred'])
        legacy=self.proposal();legacy['entry']['oil']='SYN 5W30';legacy['inferred_fields']=['entry.oil']
        validate_ai_definition(legacy,capture_context(self.user,self.project))
        self.assertTrue(legacy['definition']['values'][0]['inferred'])

    def test_german_ai_decimal_is_canonical_but_ambiguous_values_stay_invalid(self):
        proposal=self.proposal();proposal['entry']['quantity_l']='4,5'
        proposal['definition']['values'][1]['value']='4,5'
        result={'knowledge':[],'transcript':'','category':'wartung','is_receipt':False,'receipt_files':[],
                'open_entry':{'id':None,'confidence':'none','reason':None},'proposal':proposal}
        validate_capture(result,capture_context(self.user,self.project))
        self.assertEqual(proposal['entry']['quantity_l'],'4.5')
        self.assertEqual(proposal['definition']['values'][1]['value'],'4.5')
        proposal['entry']['quantity_l']='1,234'
        with self.assertRaises(ValidationError):validate_capture(result,capture_context(self.user,self.project))

    def test_draft_deletion_preserves_work_and_booked_receipts(self):
        doc=receive_capture(self.user,uploads=[SimpleUploadedFile('SYN.jpg',synthetic_photo(74))],project=self.project,token=uuid.uuid4())
        step=Step.objects.create(entry=doc.entry,title='SYN real work')
        original=Path(doc.pages.get().asset.file.path)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('document_draft_delete',args=[doc.pk]),{'version':doc.version})
        self.assertTrue(Entry.objects.filter(pk=step.entry_id).exists());self.assertTrue(original.exists())
        accepted=Document.objects.create(owner=self.user,project=self.project,status='accepted')
        source=CostSource.objects.create(owner=self.user,document=accepted,label='SYN accepted',amount=Decimal('10.00'))
        self.client.post(reverse('document_draft_delete',args=[accepted.pk]),{'version':accepted.version})
        self.assertTrue(CostSource.objects.filter(pk=source.pk).exists())
        draft=Draft.objects.create(owner=self.user,project=self.project,payload={'title':'SYN autosave'})
        token=draft.token
        self.assertEqual(self.client.post(reverse('draft_delete',args=[draft.pk]),{'version':draft.version}).status_code,302)
        self.assertEqual(self.client.post(reverse('draft_save',args=[self.project.pk]),{'token':token,'draft_version':0,'version':0}).status_code,409)
        self.assertTrue(DiscardedDraft.objects.filter(token=token).exists())

    def test_receipt_wrapper_draft_does_not_retain_hidden_uploads(self):
        doc=receive_capture(self.user,uploads=[SimpleUploadedFile('SYN.jpg',synthetic_photo(75))],project=self.project,token=uuid.uuid4())
        result={'receipt_files':[0],'proposal':self.proposal()}
        result['proposal'].update(gross_total='20.00',currency='EUR')
        receipt_draft(doc,result,read_inputs(doc));receipt=Document.objects.get(pk=doc.voice_data['receipt_id'])
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('document_draft_delete',args=[receipt.pk]),{'version':receipt.version})
        self.assertFalse(Document.objects.exists());self.assertFalse(Asset.objects.exists());self.assertFalse(Entry.objects.exists())

    @override_settings(FEEDBACK_WIDGET_ENABLED=True)
    def test_feedback_is_private_persistent_csrf_protected_and_backed_up(self):
        from django.conf import settings as django_settings
        url=reverse('feedback');client=Client(enforce_csrf_checks=True);client.force_login(self.user)
        self.assertEqual(client.post(url,{'text':'SYN'}).status_code,403)
        payload={'text':'SYN feedback','project':self.project.pk,'url':'http://testserver/projekte/?tab=chronik&token=NEVER_STORE','title':'SYN Page'}
        response=self.client.post(url,payload,HTTP_X_REQUESTED_WITH='XMLHttpRequest');self.assertEqual(response.status_code,200)
        files=list((self.data/'feedback').glob('*.json'));self.assertEqual(len(files),1)
        record=json.loads(files[0].read_text());self.assertEqual(record['text'],'SYN feedback');self.assertEqual(record['app_version'],django_settings.APP_VERSION)
        self.assertNotIn('NEVER_STORE',record['url']);self.assertEqual(record['user']['username'],self.user.username)
        self.assertEqual(self.client.get(reverse('home')).status_code,200)
        self.assertContains(self.client.get(reverse('home')),'feedback-open')
        db=sqlite3.connect(self.data/'db.sqlite3');db.execute('CREATE TABLE synthetic (id integer)');db.close()
        archive=backup(self.data/'backup.zip')
        with zipfile.ZipFile(archive) as z:self.assertIn('feedback/'+files[0].name,z.namelist())


class Release072MigrationTest(TransactionTestCase):
    databases = {'default', 'demo'}
    def test_071_data_is_preserved_and_split_per_project(self):
        executor=MigrationExecutor(connection);executor.migrate([('akte','0018_editable_categories')])
        old=executor.loader.project_state([('akte','0018_editable_categories')]).apps
        user=old.get_model('auth','User').objects.create(username='migration072')
        first=old.get_model('akte','Project').objects.create(owner_id=user.pk,name='SYN first',kind='auto')
        second=old.get_model('akte','Project').objects.create(owner_id=user.pk,name='SYN second',kind='auto')
        category=old.get_model('akte','Category').objects.create(owner_id=user.pk,name='Wartung',name_key='wartung',default_kind='wartung')
        snapshot={'oil':'SYN 5W30','quantity_l':'4.5','filter':'SYN Filter','unrelated':'KEEP'}
        entry=old.get_model('akte','Entry').objects.create(project=first,category=category,title='Ölwechsel vor dem Winter',kind='wartung',snapshot=snapshot)
        another=old.get_model('akte','Entry').objects.create(project=second,category=category,title='SYN free work',kind='wartung',snapshot=snapshot)
        task=old.get_model('akte','Task').objects.create(project=second,category=category,title='SYN task')
        source=old.get_model('akte','CostSource').objects.create(owner_id=user.pk,label='SYN cost',amount=Decimal('12.34'))
        old.get_model('akte','Allocation').objects.create(source=source,project=first,entry=entry,amount=Decimal('12.34'))
        try:
            MigrationExecutor(connection).migrate([('akte','0019_project_entry_definitions')])
            new_apps=MigrationExecutor(connection).loader.project_state([('akte','0019_project_entry_definitions')]).apps
            Entry019=new_apps.get_model('akte','Entry')
            Task019=new_apps.get_model('akte','Task')
            CostSource019=new_apps.get_model('akte','CostSource')
            entry=Entry019.objects.get(pk=entry.pk);another=Entry019.objects.get(pk=another.pk)
            self.assertNotEqual(entry.category_id,another.category_id)
            self.assertEqual(entry.category.project_id,first.pk);self.assertEqual(another.category.project_id,second.pk)
            self.assertEqual(Task019.objects.get(pk=task.pk).category_id,another.category_id)
            self.assertEqual(entry.title,'Ölwechsel vor dem Winter');self.assertEqual(entry.title_suffix,'vor dem Winter')
            self.assertEqual(entry.title_template.name,'Ölwechsel');self.assertIsNone(another.title_template_id)
            for key,value in snapshot.items():self.assertEqual(entry.snapshot[key],value);self.assertEqual(another.snapshot[key],value)
            self.assertEqual(CostSource019.objects.get(pk=source.pk).amount,Decimal('12.34'))
            with connection.cursor() as cursor:cursor.execute('PRAGMA foreign_key_check');self.assertEqual(cursor.fetchall(),[])
        finally:
            MigrationExecutor(connection).migrate([('akte',MigrationExecutor(connection).loader.graph.leaf_nodes('akte')[0][1])])
        call_command('makemigrations','akte',check=True,dry_run=True,verbosity=0)
