"""Home scanning, bounded AI routing and reversible deletion must compose safely."""
import copy
import json
from decimal import Decimal
from unittest.mock import patch

import httpx
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import tests_invoice_chronicle
from .demo import synthetic_photo
from .models import AIUsage, Allocation, CostSource, Document, DocumentLink, Entry, Job, Project
from .document_trash import change_document_deleted
from .project_routing import ai_context, assign_project
from .services import accept_document, receive_document
from .worker import commit_job, reserve, run_one


class ScanTrashTests(TestCase):
    setUp=tests_invoice_chronicle.InvoiceChronicleTests.setUp
    document=tests_invoice_chronicle.InvoiceChronicleTests.document

    def test_home_has_camera_and_optional_short_note(self):
        page=self.client.get(reverse('home'))
        self.assertContains(page,'capture="environment"')
        self.assertContains(page,'name="text"')
        self.assertContains(page,'action="'+reverse('capture_upload')+'"')

    def test_global_scan_stores_note_and_requests_project_routing(self):
        response=self.client.post(reverse('document_upload'),{
            'camera':SimpleUploadedFile('synthetic.jpg',synthetic_photo(5)),
            'scan_note':'Öl für mein Auto','auto_route':'1'})
        self.assertEqual(response.status_code,302)
        from .models import Document
        doc=Document.objects.get()
        self.assertEqual(doc.scan_note,'Öl für mein Auto')
        self.assertTrue(doc.auto_route)
        self.assertIsNone(doc.project_id)

    def test_document_has_reversible_trash_action(self):
        doc=self.document()
        response=self.client.post(reverse('document_delete',args=[doc.pk]),{'version':doc.version})
        self.assertEqual(response.status_code,302)
        doc.refresh_from_db()
        self.assertIsNotNone(doc.deleted_at)
        self.assertNotContains(self.client.get(reverse('inbox')),'synthetisch.pdf')

    def routing_doc(self, note='Öl für mein Auto'):
        from .demo import synthetic_pdf
        doc,_=receive_document(self.user,SimpleUploadedFile('routing.pdf',synthetic_pdf('routing')),note=note,auto_route=True)
        doc.status='ai';doc.paperless_id=702;doc.verified_at=timezone.now();doc.save()
        Job.objects.filter(document=doc).update(stage='ai',status='pending')
        return doc

    def proposal_for(self, project=None, confidence='high'):
        proposal=copy.deepcopy(self.proposal)
        proposal['project_assignment']={'project_id':str(project.pk) if project else None,
            'confidence':confidence if project else 'none','reason':'Passender Nutzerhinweis.' if project else None}
        return proposal

    def process(self, proposal):
        with patch('akte.worker.ai') as adapter:
            adapter.return_value.extract.return_value=(proposal,{})
            self.assertTrue(run_one())
            return adapter.return_value.extract.call_args

    def test_high_confidence_routes_once_without_booking_or_confirming_work(self):
        doc=self.routing_doc()
        self.process(self.proposal_for(self.project))
        doc.refresh_from_db()
        self.assertEqual(doc.project_id,self.project.pk)
        self.assertEqual(doc.routing['status'],'automatic')
        self.assertEqual(DocumentLink.objects.filter(document=doc).count(),1)
        self.assertEqual((Entry.objects.count(),CostSource.objects.count()),(0,0))
        self.assertEqual(doc.status,'review')
        self.assertContains(self.client.get(reverse('project',args=[self.project.pk])),'Chronikvorschlag')

    def test_medium_confidence_stays_in_inbox_for_choice(self):
        doc=self.routing_doc()
        self.process(self.proposal_for(self.project,'medium'))
        doc.refresh_from_db()
        self.assertIsNone(doc.project_id)
        self.assertEqual(DocumentLink.objects.count(),0)
        self.assertEqual(doc.routing['confidence'],'medium')
        self.assertContains(self.client.get(reverse('review',args=[doc.pk])),'Noch keine eindeutige Projektzuordnung')

    def test_auto_route_without_explanation_does_not_guess(self):
        doc=self.routing_doc();proposal=self.proposal_for(self.project)
        proposal['project_assignment']['reason']=None
        self.process(proposal)
        self.assertIsNone(Document.objects.get(pk=doc.pk).project_id)

    def test_owner_and_archived_projects_are_not_disclosed_as_candidates(self):
        other=get_user_model().objects.create_user('foreign-routing')
        Project.objects.create(owner=other,name='FOREIGN-PRIVATE')
        Project.objects.create(owner=self.user,name='OLD-ARCHIVE',archived=True)
        doc=self.routing_doc()
        context=ai_context(doc)
        self.assertEqual([item['id'] for item in context['routing_projects']],[str(self.project.pk)])
        self.assertNotIn('FOREIGN-PRIVATE',json.dumps(context))
        self.assertEqual(context['user_note'],'Öl für mein Auto')

    def test_ai_cannot_route_to_a_foreign_or_archived_project(self):
        foreign=get_user_model().objects.create_user('routing-outsider')
        target=Project.objects.create(owner=foreign,name='Other')
        doc=self.routing_doc()
        self.process(self.proposal_for(target))
        doc.refresh_from_db();self.assertIsNone(doc.project_id)
        self.assertEqual(DocumentLink.objects.count(),0)

    def test_manual_assignment_wins_over_inflight_ai_and_resets_old_cost_targets(self):
        doc=self.routing_doc()
        alternative=Project.objects.create(owner=self.user,name='Anderes Ziel')
        doc.review_draft={'fields':{'vendor':'Meine Korrektur','chronicle_project':str(self.project.pk)},
                          'allocations':[{'project_id':str(self.project.pk),'amount':'75'}]}
        doc.save()
        def extract(*args,**kwargs):
            assign_project(self.user,doc,alternative,doc.version)
            return self.proposal_for(self.project),{}
        with patch('akte.worker.ai') as adapter:
            adapter.return_value.extract.side_effect=extract
            run_one()
        doc.refresh_from_db()
        self.assertEqual(doc.project_id,alternative.pk)
        self.assertEqual(doc.routing['status'],'manual')
        self.assertFalse(doc.auto_route)
        self.assertNotIn('allocations',doc.review_draft)
        self.assertEqual(doc.review_draft['fields']['vendor'],'Meine Korrektur')

    def test_correction_removes_only_the_previous_automatic_project_link(self):
        doc=self.routing_doc();self.process(self.proposal_for(self.project));doc.refresh_from_db()
        alternative=Project.objects.create(owner=self.user,name='Korrektes Ziel')
        assign_project(self.user,doc,alternative,doc.version)
        self.assertEqual(list(DocumentLink.objects.filter(document=doc).values_list('project_id',flat=True)),[alternative.pk])

    def test_project_archived_during_request_is_not_assigned(self):
        doc=self.routing_doc()
        def extract(*args,**kwargs):
            self.project.archived=True;self.project.save()
            return self.proposal_for(self.project),{}
        with patch('akte.worker.ai') as adapter:
            adapter.return_value.extract.side_effect=extract
            run_one()
        doc.refresh_from_db();self.assertIsNone(doc.project_id)

    def test_database_failure_rolls_back_assignment_and_link(self):
        doc=self.routing_doc()
        with patch('akte.project_routing.audit',side_effect=DatabaseError('synthetic')):
            self.process(self.proposal_for(self.project))
        doc.refresh_from_db()
        self.assertIsNone(doc.project_id)
        self.assertEqual(DocumentLink.objects.count(),0)

    def test_100_project_bound_avoids_sending_an_incomplete_arbitrary_selection(self):
        doc=self.routing_doc()
        Project.objects.bulk_create([Project(owner=self.user,name=f'Candidate {i}') for i in range(100)])
        self.assertFalse(ai_context(doc)['routing_enabled'])
        self.assertEqual(ai_context(doc)['routing_projects'],[])

    def test_gemini_schema_contains_only_actual_candidate_ids(self):
        from .adapters import AIResponseInvalid, GeminiAI
        from .proposal_schema import empty_entry
        doc=self.routing_doc()
        proposal=self.proposal_for(self.project)
        proposal['entry']={**empty_entry(),**proposal['entry']}
        proposal['project_assignment']['project_id']='f38fc7f2-fb6f-4a6b-9cb3-bc0643e37b89'
        response=httpx.Response(200,request=httpx.Request('POST','https://example.invalid'),
            json={'candidates':[{'content':{'parts':[{'text':json.dumps(proposal)}]}}]})
        with override_settings(GEMINI_API_KEY='synthetic'),patch('akte.adapters.httpx.post',return_value=response) as post:
            with self.assertRaises(AIResponseInvalid):GeminiAI().extract('synthetic',context=ai_context(doc))
        body=post.call_args.kwargs['json']
        choices=body['generationConfig']['responseJsonSchema']['properties']['project_assignment']['properties']['project_id']['enum']
        self.assertEqual(choices,[None,str(self.project.pk)])
        self.assertNotIn('tools',body)

    def test_capture_duplicate_keeps_original_note_and_creates_no_second_job(self):
        payload=synthetic_photo(7)
        for note in ['Erster Hinweis','Anderer Hinweis']:
            response=self.client.post(reverse('document_upload'),{'document':SimpleUploadedFile('same.jpg',payload),
                'scan_note':note,'auto_route':'1'})
            self.assertEqual(response.status_code,302)
        self.assertEqual(Document.objects.count(),1)
        self.assertEqual(Document.objects.get().scan_note,'Erster Hinweis')
        self.assertEqual(Job.objects.filter(kind='document').count(),1)

    def test_upload_errors_return_json_and_do_not_acknowledge_a_missing_file(self):
        response=self.client.post(reverse('document_upload'),{'scan_note':'Text bleibt beim Client'},
                                  HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code,400)
        self.assertIn('auswählen',response.json()['error'])
        self.assertEqual(Document.objects.count(),0)

    def test_too_long_note_is_rejected_before_file_is_ingested(self):
        response=self.client.post(reverse('document_upload'),{'scan_note':'x'*1001,
            'camera':SimpleUploadedFile('a.jpg',synthetic_photo(3))})
        self.assertEqual(response.status_code,400)
        self.assertEqual(Document.objects.count(),0)

    def test_deleting_accepted_document_keeps_costs_and_original_and_restores_once(self):
        doc=self.document()
        source=accept_document(self.user,doc,doc.proposal,[{'project':self.project,'amount':'75'}],expected_version=doc.version)
        doc.refresh_from_db();original=doc.asset.file.path
        deleted=change_document_deleted(self.user,doc,doc.version)
        self.assertEqual(self.project.cost,75)
        self.assertEqual(CostSource.objects.get().pk,source.pk)
        from pathlib import Path
        self.assertTrue(Path(original).is_file())
        restored=change_document_deleted(self.user,deleted,deleted.version,restore=True)
        change_document_deleted(self.user,restored,restored.version,restore=True)
        self.assertEqual((Document.objects.count(),CostSource.objects.count()),(1,1))
        self.assertEqual(restored.status,'accepted')
        self.assertEqual(restored.job.status,'done')

    def test_trash_invalidates_inflight_results_even_after_restore(self):
        doc=self.routing_doc();job=reserve()
        deleted=change_document_deleted(self.user,doc,doc.version)
        self.assertFalse(commit_job(job,{'proposal':self.proposal_for(self.project)},status='done'))
        restored=change_document_deleted(self.user,deleted,deleted.version,restore=True)
        self.assertEqual(restored.job.status,'pending')
        self.assertFalse(commit_job(job,{'proposal':self.proposal_for(self.project)},status='done'))
        self.assertFalse(Document.objects.get(pk=doc.pk).proposal)

    def test_trash_restore_does_not_retry_a_previously_failed_job(self):
        doc=self.document();Job.objects.filter(document=doc).update(status='failed',attempts=3)
        deleted=change_document_deleted(self.user,doc,doc.version)
        restored=change_document_deleted(self.user,deleted,deleted.version,restore=True)
        self.assertEqual(restored.job.status,'failed')
        self.assertEqual(restored.job.attempts,3)

    def test_duplicate_of_trashed_file_requires_restore_instead_of_reupload(self):
        doc=self.document();change_document_deleted(self.user,doc,doc.version)
        with doc.asset.file.open('rb') as file:content=file.read()
        with self.assertRaisesMessage(ValidationError,'Papierkorb'):
            receive_document(self.user,SimpleUploadedFile('same.pdf',content),self.project)
        self.assertEqual(Document.objects.count(),1)

    def test_trashed_document_cannot_be_accepted_or_retried(self):
        from .documents import schedule_document
        doc=self.document();deleted=change_document_deleted(self.user,doc,doc.version)
        with self.assertRaises(ValidationError):accept_document(self.user,deleted,deleted.proposal,[],expected_version=deleted.version)
        with self.assertRaises(ValidationError):schedule_document(deleted,'ai')
        self.assertEqual(CostSource.objects.count(),0)

    def test_delete_confirmation_is_read_only_and_post_checks_csrf_and_owner(self):
        doc=self.document();url=reverse('document_delete',args=[doc.pk])
        self.assertContains(self.client.get(url),'Kosten bleiben erhalten')
        self.assertIsNone(Document.objects.get(pk=doc.pk).deleted_at)
        secure=Client(enforce_csrf_checks=True);secure.force_login(self.user)
        self.assertEqual(secure.post(url,{'version':doc.version}).status_code,403)
        outsider=get_user_model().objects.create_user('delete-outsider');self.client.force_login(outsider)
        self.assertEqual(self.client.post(url,{'version':doc.version}).status_code,404)

    def test_deleted_document_disappears_from_fts_but_returns_after_restore(self):
        entry=Entry.objects.create(project=self.project,title='Normale Arbeit',kind='notiz')
        doc=self.document();doc.entry=entry;doc.ocr='UNIQUEPAPERTRASHTERM';doc.scan_note='UNIQUEUSERNOTETERM';doc.save()
        response=self.client.get(reverse('search'),{'q':'UNIQUEUSERNOTETERM'})
        self.assertEqual(len(response.context['documents']),1)
        deleted=change_document_deleted(self.user,doc,doc.version)
        response=self.client.get(reverse('search'),{'q':'UNIQUEPAPERTRASHTERM'})
        self.assertEqual(len(response.context['documents']),0)
        self.assertEqual(len(response.context['entries']),0)
        change_document_deleted(self.user,deleted,deleted.version,restore=True)
        response=self.client.get(reverse('search'),{'q':'UNIQUEPAPERTRASHTERM'})
        self.assertEqual(len(response.context['documents']),1)

    def test_deleted_financial_document_remains_explicitly_in_portable_export(self):
        import zipfile
        from .exports import portable_export
        doc=self.document();accept_document(self.user,doc,doc.proposal,[{'project':self.project,'amount':'75'}],expected_version=doc.version)
        doc.refresh_from_db();change_document_deleted(self.user,doc,doc.version)
        target=self.data/'trash-export.zip';portable_export(target,self.user,self.project)
        with zipfile.ZipFile(target) as archive:
            data=json.loads(archive.read('daten.json'))
            record=next(item for item in data['objects'] if item['model']=='akte.document')
            self.assertIsNotNone(record['fields']['deleted_at'])
            self.assertTrue(any(name.startswith('belege/') for name in archive.namelist()))
