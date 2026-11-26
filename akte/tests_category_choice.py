"""The editable category is the only visible classification control."""
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import EntryForm
from .models import Allocation, Category, CostSource, Document, Entry, Project


class UnifiedCategoryTest(TestCase):
    def test_one_category_choice_preserves_workflows_and_costs(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(DATA_DIR=Path(folder), ALLOWED_HOSTS=['testserver']):
            owner=get_user_model().objects.create_user('category-choice')
            project=Project.objects.create(owner=owner,name='SYN car',kind='auto')
            service=Category.objects.create(owner=owner,project=project,name='Wartung',default_kind='wartung')
            conversion=Category.objects.create(owner=owner,project=project,name='Umbau',default_kind='umbau')
            custom=Category.objects.create(owner=owner,project=project,name='Eigene Kategorie')
            entry=Entry.objects.create(project=project,title='SYN existing work',kind='wartung',category=service,cost_state='known')
            doc=Document.objects.create(owner=owner,project=project,entry=entry,status='accepted')
            source=CostSource.objects.create(owner=owner,document=doc,label='SYN bill',amount=Decimal('12.34'))
            Allocation.objects.create(source=source,project=project,entry=entry,amount=Decimal('12.34'))
            self.client.force_login(owner)
            edit=reverse('entry_edit',args=[entry.pk])
            page=self.client.get(edit)
            self.assertEqual(page.status_code,200)
            self.assertTrue(page.context['form'].fields['kind'].widget.is_hidden)
            self.assertContains(page,'<select name="category"',count=1)
            self.assertNotContains(page,'<select name="kind"')
            self.assertNotContains(self.client.get(reverse('project',args=[project.pk])),'<select name="kind"')

            def save(category):
                payload={'title':entry.title,'kind':'reparatur','category':str(category.pk) if category else '',
                         'status':'erledigt','cost_state':'known','amount':'12,34',
                         'token':str(uuid.uuid4()),'version':entry.version}
                response=self.client.post(edit,payload)
                self.assertEqual(response.status_code,302)
                entry.refresh_from_db()
                self.assertEqual(entry.cost,Decimal('12.34'))
                self.assertEqual(CostSource.objects.count(),1)
                return payload

            save(conversion)
            self.assertEqual(entry.kind,'umbau')
            self.assertEqual(entry.category_id,conversion.pk)
            self.assertContains(self.client.get(reverse('project',args=[project.pk]),{'tab':'umbauten'}),entry.title)
            conversion.name='Optik';conversion.save()
            save(conversion)
            self.assertEqual(entry.kind,'umbau')
            self.assertContains(self.client.get(reverse('entry',args=[entry.pk])),'OPTIK')
            save(custom)
            self.assertEqual(entry.kind,'umbau')
            save(None)
            self.assertEqual(entry.kind,'umbau');self.assertIsNone(entry.category_id)
            other=get_user_model().objects.create_user('other-category-choice')
            foreign=Category.objects.create(owner=other,project=Project.objects.create(owner=other,name='Other project'),name='PRIVATE',default_kind='reparatur')
            response=self.client.post(edit,{'title':entry.title,'category':foreign.pk,'kind':'wartung','token':uuid.uuid4(),'version':entry.version,'cost_state':'known','amount':'12,34'})
            self.assertEqual(response.status_code,200)
            entry.refresh_from_db();self.assertEqual(entry.kind,'umbau');self.assertIsNone(entry.category_id)
            form=EntryForm({'title':'SYN new','category':custom.pk,'cost_state':'unknown','token':uuid.uuid4(),'version':0},owner=owner,project=project,initial={'kind':'wartung'})
            self.assertTrue(form.is_valid(),form.errors)
            self.assertEqual(form.domain['kind'],'notiz')
            self.assertEqual(Allocation.objects.count(),1)
