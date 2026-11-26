"""Invoice uploads must lead to reviewable, traceable chronology entries."""
import copy
import json
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.db import DatabaseError

from .demo import synthetic_pdf
from .models import Allocation, CostSource, Document, Entry, Job, Project
from .services import receive_document


class InvoiceChronicleTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        override = override_settings(DATA_DIR=self.data, MEDIA_ROOT=self.data / 'media',
                                     DEMO_MODE=True, ALLOWED_HOSTS=['testserver'])
        override.enable()
        self.addCleanup(override.disable)
        self.user = get_user_model().objects.create_user('chronicle-review')
        self.client.force_login(self.user)
        self.project = Project.objects.create(owner=self.user, name='Synthetisches Auto', kind='auto')
        self.proposal = {
            'invoice_number': 'SYN-CHRONICLE', 'vendor': 'Synthetische Werkstatt',
            'invoice_date': '2026-11-10', 'service_date': '2026-11-09',
            'gross_total': '75.00', 'currency': 'EUR', 'positions': [],
            'entry': {'title': 'Ölwechsel', 'kind': 'wartung', 'status': 'erledigt',
                      'date': '2026-11-09', 'meter': '165200', 'meter_unit': 'km'},
            'inferred_fields': ['entry.title', 'entry.kind'],
        }

    def document(self):
        doc, _ = receive_document(self.user, SimpleUploadedFile(
            'synthetisch.pdf', synthetic_pdf('Synthetischer Beleg')), self.project)
        doc.status = 'review'
        doc.paperless_id = 701
        doc.verified_at = timezone.now()
        doc.proposal = copy.deepcopy(self.proposal)
        doc.save()
        Job.objects.filter(document=doc).update(status='done')
        return doc

    def payload(self, doc, **changes):
        return {
            'action': 'accept', 'version': doc.version, 'currency': 'EUR',
            'gross_total': '75,00', 'invoice_number': 'SYN-CHRONICLE',
            'invoice_date': '2026-11-10', 'service_date': '2026-11-09',
            'positions_editor': 'expert', 'positions': '[]',
            'chronicle_mode': 'create', 'chronicle_project': str(self.project.pk),
            'entry_title': 'Ölwechsel', 'entry_kind': 'wartung',
            'entry_status': 'erledigt', 'entry_date': '2026-11-09',
            'entry_meter': '165200', 'entry_meter_unit': 'km',
            'alloc_project': str(self.project.pk), 'alloc_entry': '',
            'alloc_amount': '75,00', 'alloc_ref': '', **changes,
        }

    def test_project_upload_is_visible_in_chronicle_before_acceptance(self):
        doc = self.document()
        page = self.client.get(reverse('project', args=[self.project.pk]))
        self.assertContains(page, 'Chronikvorschlag')
        self.assertContains(page, reverse('review', args=[doc.pk]))
        self.assertEqual(Entry.objects.count(), 0)

    def test_ai_entry_fields_are_editable_and_interpretations_are_marked(self):
        doc = self.document()
        page = self.client.get(reverse('review', args=[doc.pk]))
        self.assertContains(page, 'name="entry_title"')
        self.assertContains(page, 'value="Ölwechsel"')
        self.assertContains(page, 'data-ai-field="entry_title"')
        self.assertContains(page, 'name="entry_kind"')

    def test_confirmation_creates_one_entry_with_work_date_and_same_cost_source(self):
        doc = self.document()
        url = reverse('review', args=[doc.pk])
        self.assertEqual(self.client.post(url, self.payload(doc)).status_code, 302)
        doc.refresh_from_db()
        self.assertIsNotNone(doc.entry_id)
        entry = Entry.objects.get()
        self.assertEqual(entry.date, date(2026, 11, 9))
        self.assertEqual(entry.title, 'Ölwechsel')
        self.assertEqual(entry.cost, 75)
        self.assertEqual(self.project.cost, 75)
        self.assertEqual(doc.source.invoice_date, date(2026, 11, 10))
        self.assertIn('title', entry.snapshot['_inferred_fields'])
        self.client.post(url, self.payload(doc))
        self.assertEqual(Entry.objects.count(), 1)
        self.assertEqual(CostSource.objects.count(), 1)
        self.assertEqual(Allocation.objects.count(), 1)

    def test_missing_entry_facts_stay_empty_instead_of_using_invoice_or_upload_date(self):
        doc = self.document()
        changes = {key: '' for key in ('entry_title','entry_kind','entry_status','entry_date',
                                       'entry_meter','entry_meter_unit')}
        self.assertEqual(self.client.post(reverse('review',args=[doc.pk]),
                         self.payload(doc,**changes)).status_code,302)
        entry = Entry.objects.get()
        self.assertEqual((entry.title,entry.kind,entry.status),('','',''))
        self.assertIsNone(entry.date)
        self.assertIsNone(entry.meter)
        self.assertEqual(entry.precision,'unknown')
        self.assertEqual(entry.snapshot['_inferred_fields'],[])

    def test_month_precision_is_not_promoted_to_an_exact_day(self):
        doc = self.document()
        self.client.post(reverse('review',args=[doc.pk]),self.payload(doc,entry_date='11/2026'))
        entry=Entry.objects.get()
        self.assertEqual(entry.precision,'month')
        self.assertEqual(entry.display_date,'11/2026')

    def test_existing_entry_is_linked_without_overwriting_its_historical_data(self):
        entry=Entry.objects.create(project=self.project,title='Eigene Wartung',kind='wartung',date=date(2025,1,1))
        doc=self.document()
        response=self.client.post(reverse('review',args=[doc.pk]),self.payload(doc,
            chronicle_mode='existing',chronicle_entry=str(entry.pk)))
        self.assertEqual(response.status_code,302)
        entry.refresh_from_db();doc.refresh_from_db()
        self.assertEqual(entry.title,'Eigene Wartung')
        self.assertEqual(entry.date,date(2025,1,1))
        self.assertEqual(doc.entry_id,entry.pk)
        self.assertEqual(Entry.objects.count(),1)
        self.assertEqual(entry.cost,75)

    def test_explicit_document_only_is_still_possible(self):
        doc=self.document()
        self.client.post(reverse('review',args=[doc.pk]),self.payload(doc,chronicle_mode='document'))
        self.assertEqual(Entry.objects.count(),0)
        self.assertEqual(self.project.cost,75)

    def test_failure_rolls_back_source_entry_and_acceptance_together(self):
        doc=self.document()
        with patch('akte.invoice_chronicle.link_document',side_effect=ValidationError('synthetic link failure')):
            response=self.client.post(reverse('review',args=[doc.pk]),self.payload(doc))
        self.assertEqual(response.status_code,200)
        doc.refresh_from_db()
        self.assertEqual(doc.status,'review')
        self.assertEqual((Entry.objects.count(),CostSource.objects.count(),Allocation.objects.count()),(0,0,0))

    def test_another_owners_entry_cannot_be_linked(self):
        other=get_user_model().objects.create_user('foreign-chronicle')
        project=Project.objects.create(owner=other,name='Fremd')
        entry=Entry.objects.create(project=project,title='Privat',kind='notiz')
        doc=self.document()
        response=self.client.post(reverse('review',args=[doc.pk]),self.payload(doc,
            chronicle_mode='existing',chronicle_entry=str(entry.pk)))
        self.assertEqual(response.status_code,200)
        self.assertEqual(CostSource.objects.count(),0)
        self.assertIsNone(Document.objects.get(pk=doc.pk).entry_id)

    def test_accepted_legacy_invoice_can_get_chronicle_without_rebooking(self):
        from .services import accept_document
        doc=self.document()
        source=accept_document(self.user,doc,doc.proposal,[{'project':self.project,'amount':'75'}],expected_version=doc.version)
        doc.refresh_from_db()
        payload=self.payload(doc,action='chronicle')
        response=self.client.post(reverse('review',args=[doc.pk]),payload)
        self.assertEqual(response.status_code,302)
        self.client.post(reverse('review',args=[doc.pk]),payload)
        doc.refresh_from_db()
        self.assertEqual(doc.source.pk,source.pk)
        self.assertEqual((Entry.objects.count(),CostSource.objects.count()),(1,1))
        self.assertEqual(self.project.cost,75)
        self.assertEqual(doc.entry.cost,75)

    def test_manual_replacement_removes_only_its_own_marker(self):
        doc=self.document()
        self.client.post(reverse('review',args=[doc.pk]),self.payload(doc,entry_title='Mein eigener Titel'))
        entry=Entry.objects.get()
        self.assertNotIn('title',entry.inferred_fields)
        self.assertIn('kind',entry.inferred_fields)
        self.assertContains(self.client.get(reverse('entry',args=[entry.pk])),'Von der KI interpretiert')

    def test_review_autosave_restores_chronicle_choices_and_incomplete_fields(self):
        doc=self.document()
        payload=self.payload(doc,entry_title='Eigener Entwurf',entry_date='11/',draft_version=0)
        self.assertEqual(self.client.post(reverse('review_draft',args=[doc.pk]),payload).status_code,200)
        page=self.client.get(reverse('review',args=[doc.pk]))
        self.assertContains(page,'value="Eigener Entwurf"')
        self.assertContains(page,'value="11/"')
        self.assertEqual(Entry.objects.count(),0)

    def test_invalid_ai_choices_dates_and_markers_are_rejected(self):
        from .billing import validate_proposal
        for changes in [{'kind':'einkauf'},{'status':'fertig'},{'date':'2026-02-30'},
                        {'meter':'NaN'},{'quantity_l':'-1'}]:
            proposal=copy.deepcopy(self.proposal)
            proposal['entry'].update(changes)
            with self.subTest(changes=changes),self.assertRaises(ValidationError):
                validate_proposal(proposal)
        for field in ['entry.unknown','positions.99.amount','tax_total']:
            proposal=copy.deepcopy(self.proposal);proposal['inferred_fields']=[field]
            with self.subTest(field=field),self.assertRaises(ValidationError):
                validate_proposal(proposal)

    def test_missing_position_amount_can_be_reviewed_but_not_booked(self):
        from .billing import validate_proposal
        proposal=copy.deepcopy(self.proposal)
        proposal['positions']=[{'description':'Teil ohne Preis','amount':None,'part_number':None}]
        validate_proposal(proposal)
        with self.assertRaises(ValidationError):validate_proposal(proposal,final=True)

    def test_gemini_knows_choices_free_fields_and_interpretation_contract(self):
        import httpx
        from .adapters import GeminiAI,SCHEMA
        from .proposal_schema import empty_entry
        proposal={**self.proposal,'entry':{**empty_entry(),**self.proposal['entry']},'project_assignment':{'project_id':None,'confidence':'none','reason':None}}
        response=httpx.Response(200,request=httpx.Request('POST','https://example.invalid'),
            json={'candidates':[{'content':{'parts':[{'text':json.dumps(proposal)}]}}]})
        with override_settings(GEMINI_API_KEY='synthetic'),patch('akte.adapters.httpx.post',return_value=response) as post:
            actual,_=GeminiAI().extract('Synthetischer Beleg',context={'project':{'kind':'auto'}})
        prompt=post.call_args.kwargs['json']['systemInstruction']['parts'][0]['text']
        for required in ['inferred_fields','Rechnungsdatum','Kauf belegt','null','freie','wartung','reparatur','umbau']:
            self.assertIn(required,prompt)
        self.assertEqual(SCHEMA['properties']['entry']['properties']['kind']['enum'],[None,*dict(Entry.KINDS)])
        self.assertIsNone(actual['entry']['oil'])

    def test_export_retains_interpretation_metadata(self):
        import zipfile
        from .exports import portable_export
        doc=self.document()
        self.client.post(reverse('review',args=[doc.pk]),self.payload(doc))
        target=self.data/'chronicle.zip'
        portable_export(target,self.user,self.project)
        with zipfile.ZipFile(target) as archive:
            data=json.loads(archive.read('daten.json'))
            entry=next(item for item in data['objects'] if item['model']=='akte.entry')
            self.assertIn('title',entry['fields']['snapshot']['_inferred_fields'])
            self.assertIn('Ölwechsel*',archive.read('index.html').decode())

    def test_database_failure_does_not_leave_a_half_accepted_invoice(self):
        from .services import accept_document
        doc=self.document()
        with patch('akte.invoice_chronicle.Entry.objects.get_or_create',side_effect=DatabaseError('synthetic')):
            with self.assertRaises(DatabaseError):
                accept_document(self.user,doc,doc.proposal,[{'project':self.project,'amount':'75'}],
                    expected_version=doc.version,chronicle={'mode':'create','project':self.project})
        self.assertEqual((Entry.objects.count(),CostSource.objects.count()),(0,0))
        self.assertEqual(Document.objects.get(pk=doc.pk).status,'review')

    def test_manual_entry_edit_keeps_only_unchanged_interpretations(self):
        import uuid
        from .services import save_entry
        doc=self.document()
        self.client.post(reverse('review',args=[doc.pk]),self.payload(doc))
        entry=Entry.objects.get()
        save_entry(self.user,self.project,{'title':'Manuell ersetzt','snapshot':entry.snapshot.copy()},
                   uuid.uuid4(),entry,entry.version)
        entry.refresh_from_db()
        self.assertNotIn('title',entry.inferred_fields)
        self.assertIn('kind',entry.inferred_fields)
        self.assertEqual(entry.cost,75)

    def test_split_keeps_explicit_other_entry_and_counts_each_amount_once(self):
        other=Entry.objects.create(project=self.project,title='Anderer Vorgang',kind='reparatur')
        doc=self.document()
        payload=self.payload(doc,alloc_project=[str(self.project.pk)]*2,
            alloc_entry=['',str(other.pk)],alloc_amount=['45','30'],alloc_ref=['',''])
        self.assertEqual(self.client.post(reverse('review',args=[doc.pk]),payload).status_code,302)
        doc.refresh_from_db()
        self.assertEqual(doc.entry.cost,45)
        self.assertEqual(other.cost,30)
        self.assertEqual(self.project.cost,75)

    def test_partial_date_and_zero_meter_keep_their_markers(self):
        from .proposal_schema import retained_inferences
        from .invoice_chronicle import marked
        original={'entry':{'meter':'0'},'inferred_fields':['entry.meter']}
        current={'entry':{'meter':'0.000'}}
        self.assertEqual(retained_inferences(original,current),['entry.meter'])
        entry=Entry(snapshot={'_inferred_fields':['meter']})
        self.assertEqual(marked(entry,'meter',0),'0*')

    def test_pdf_and_maintenance_table_keep_stars_and_explanation(self):
        from pypdf import PdfReader
        from .exports import report_pdf
        from .maintenance_pdf import maintenance_pdf
        doc=self.document()
        self.client.post(reverse('review',args=[doc.pk]),self.payload(doc))
        for name,writer in [('report.pdf',lambda p:report_pdf(p,self.project,Entry.objects.all())),
                            ('table.pdf',lambda p:maintenance_pdf(p,self.project))]:
            target=self.data/name;writer(target)
            text=' '.join(page.extract_text() for page in PdfReader(target).pages)
            self.assertIn('Ölwechsel*',text)
            self.assertIn('interpretiert',text)

    def test_inbox_upload_does_not_guess_a_project(self):
        doc=self.document();doc.project=None;doc.save(update_fields=['project'])
        page=self.client.get(reverse('review',args=[doc.pk]))
        self.assertEqual(page.context['form']['chronicle_mode'].value(),'document')
        self.assertIsNone(page.context['form']['chronicle_project'].value())
