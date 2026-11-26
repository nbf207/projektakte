"""Direct regression for editable categories and preservation of existing data."""
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse

from .forms import EntryForm
from .models import Allocation, Asset, Category, CostSource, Entry, Project, Task


class CategoryWorkflowTest(TransactionTestCase):
    def test_migration_grouping_editing_and_safe_removal(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(
            DATA_DIR=Path(folder), MEDIA_ROOT=Path(folder)/'media', DEMO_MODE=True, ALLOWED_HOSTS=['testserver']
        ):
            executor = MigrationExecutor(connection)
            executor.migrate([('akte', '0017_voice_capture')])
            old = executor.loader.project_state([('akte', '0017_voice_capture')]).apps
            user = old.get_model('auth', 'User').objects.create(username='category-owner')
            other = old.get_model('auth', 'User').objects.create(username='category-other')
            project = old.get_model('akte', 'Project').objects.create(owner_id=user.pk, name='SYN project')
            old.get_model('akte', 'Project').objects.create(owner_id=other.pk, name='PRIVATE project')
            entry = old.get_model('akte', 'Entry').objects.create(project=project, title='SYN service', kind='wartung', cost_state='known')
            second = old.get_model('akte', 'Entry').objects.create(project=project, title='SYN separate conversion', kind='umbau')
            task = old.get_model('akte', 'Task').objects.create(project=project, entry=entry, title='SYN task')
            document = old.get_model('akte', 'Document').objects.create(owner_id=user.pk, project=project, entry=entry, status='accepted')
            source = old.get_model('akte', 'CostSource').objects.create(owner_id=user.pk, document=document, amount=Decimal('12.34'), label='SYN cost')
            old.get_model('akte', 'Allocation').objects.create(source=source, project=project, entry=entry, amount=Decimal('12.34'))
            media = Path(folder)/'media/originals';media.mkdir(parents=True)
            original = media/'kept.bin';original.write_bytes(b'SYN original')
            old.get_model('akte', 'Asset').objects.create(owner_id=user.pk, file='originals/kept.bin', name='kept.bin', mime='application/octet-stream', size=12, sha256='a'*64)
            # Compare the actual old columns, not only object counts.
            baseline = {}
            with connection.cursor() as cursor:
                for table in ['auth_user','akte_project','akte_entry','akte_task','akte_document','akte_costsource','akte_allocation','akte_asset']:
                    cursor.execute('PRAGMA table_info('+table+')');columns=[row[1] for row in cursor.fetchall()]
                    query='SELECT '+','.join('"'+column+'"' for column in columns)+' FROM '+table+' ORDER BY id'
                    cursor.execute(query);baseline[query]=cursor.fetchall()
            MigrationExecutor(connection).migrate([('akte','0019_project_entry_definitions')])
            with connection.cursor() as cursor:
                for query, rows in baseline.items():cursor.execute(query);self.assertEqual(cursor.fetchall(), rows)
            call_command('makemigrations','akte',check=True,dry_run=True,verbosity=0)
            owner=get_user_model().objects.get(pk=user.pk)
            entry=Entry.objects.get(pk=entry.pk);task=Task.objects.get(pk=task.pk)
            service=entry.category
            self.assertEqual(service.name,'Wartung');self.assertEqual(task.category_id,service.pk)
            self.assertEqual(Category.objects.filter(owner=owner).count(),6)
            self.client.force_login(owner)
            url=reverse('project',args=[project.pk])
            self.assertContains(self.client.get(url),'chronicle-category')
            self.assertNotContains(self.client.get(url,{'category':service.pk}),'SYN separate conversion')
            manage=reverse('categories');token=uuid.uuid4()
            self.assertEqual(self.client.post(manage,{'action':'save','category_id':token,'version':0,'name':'Eigene Kategorie'}).status_code,302)
            custom=Category.objects.get(pk=token)
            duplicate=self.client.post(manage,{'action':'save','category_id':uuid.uuid4(),'version':0,'name':'EIGENE KATEGORIE'})
            self.assertContains(duplicate,'bereits vorhanden');self.assertEqual(Category.objects.filter(owner=owner).count(),7)
            self.assertEqual(self.client.post(manage,{'action':'save','category_id':service.pk,'version':service.version,'name':'Service'}).status_code,302)
            task.refresh_from_db();self.assertEqual(task.category.name,'Service')
            foreign=Category.objects.filter(owner_id=other.pk).first()
            self.assertEqual(self.client.post(manage,{'action':'delete','category_id':foreign.pk,'version':foreign.version,'name':foreign.name}).status_code,404)
            self.assertNotIn(foreign,EntryForm(owner=owner).fields['category'].queryset)
            csrf=Client(enforce_csrf_checks=True);csrf.force_login(owner)
            self.assertEqual(csrf.post(manage,{'action':'delete'}).status_code,403)
            edit=reverse('entry_edit',args=[entry.pk])
            payload={'title':entry.title,'kind':entry.kind,'status':entry.status,'cost_state':'known','amount':'12,34',
                     'token':uuid.uuid4(),'version':entry.version,'category':custom.pk}
            self.assertEqual(self.client.post(edit,payload).status_code,302)
            entry.refresh_from_db();self.assertEqual(entry.category_id,custom.pk)
            self.assertEqual(self.client.post(reverse('task_edit',args=[task.pk]),{'title':task.title,'entry':entry.pk,'category':custom.pk,'version':task.version}).status_code,302)
            self.assertEqual(self.client.post(manage,{'action':'delete','category_id':custom.pk,'version':custom.version,'name':''}).status_code,302)
            entry.refresh_from_db();task.refresh_from_db()
            self.assertIsNone(entry.category_id);self.assertIsNone(task.category_id)
            self.assertEqual(task.entry_id,entry.pk);self.assertEqual(entry.cost,Decimal('12.34'))
            self.assertEqual(CostSource.objects.count(),1);self.assertEqual(Allocation.objects.count(),1)
            self.assertTrue(original.exists());self.assertEqual(Asset.objects.count(),1)
            self.assertContains(self.client.get(url,{'category':'none'}),'SYN service')
            self.assertContains(self.client.get(reverse('tasks')),'Weitere Zuordnung')
            with connection.cursor() as cursor:
                cursor.execute('PRAGMA foreign_key_check');self.assertEqual(cursor.fetchall(),[])
