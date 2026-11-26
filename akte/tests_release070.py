"""Security boundaries and production checks for the stable release."""
import io
import os
import subprocess
import tempfile
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .demo import synthetic_pdf, synthetic_photo
from .files import ingest_asset
from .mail_config import MailConfig
from .mail_ingest import authenticated_sender
from .models import AIUsage, Document, Entry, ExportRequest, Project
from .templatetags.akte import rich


class ReleaseSecurityTests(TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.data=Path(folder.name)
        override=override_settings(DATA_DIR=self.data,MEDIA_ROOT=self.data/'media',DEMO_MODE=True,ALLOWED_HOSTS=['testserver'])
        override.enable();self.addCleanup(override.disable)
        self.owner=get_user_model().objects.create_user('release-owner')
        self.other=get_user_model().objects.create_user('release-other')
        self.project=Project.objects.create(owner=self.other,name='PRIVATE PROJECT',kind='auto')
        self.entry=Entry.objects.create(project=self.project,title='PRIVATE ENTRY')
        self.asset=ingest_asset(self.other,SimpleUploadedFile('private.jpg',synthetic_photo(201)))
        self.doc=Document.objects.create(owner=self.other,project=self.project,asset=self.asset,entry=self.entry)
        self.export=ExportRequest.objects.create(owner=self.other,status='done',path='exports/private.zip')

    def test_private_endpoints_require_login_and_owner(self):
        targets=[('project',[self.project.pk]),('project_edit',[self.project.pk]),('entry',[self.entry.pk]),
            ('entry_edit',[self.entry.pk]),('step_new',[self.entry.pk]),('project_upload',[self.project.pk]),
            ('media',[self.asset.pk]),('review',[self.doc.pk]),('document_original',[self.doc.pk]),
            ('document_status',[self.doc.pk]),('export_download',[self.export.pk]),('project_export',[self.project.pk])]
        for name,args in targets:
            with self.subTest(endpoint=name):
                response=self.client.get(reverse(name,args=args));self.assertEqual(response.status_code,302)
        self.client.force_login(self.owner)
        for name,args in targets:
            with self.subTest(owner_endpoint=name):self.assertEqual(self.client.get(reverse(name,args=args)).status_code,404)
        for name in ('home','inbox','maintenance','tasks','search','system','trash','share'):
            response=self.client.get(reverse(name));self.assertEqual(response.status_code,200)
            self.assertNotContains(response,'PRIVATE PROJECT');self.assertNotContains(response,'PRIVATE ENTRY')

    def test_all_mutating_routes_keep_csrf_enforcement(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.owner)
        targets=[('capture_upload',[]),('voice_upload',[]),('share_save',[]),('document_upload',[]),
            ('document_action',[self.doc.pk]),('review_draft',[self.doc.pk]),('document_route',[self.doc.pk]),
            ('entry_delete',[self.entry.pk]),('document_delete',[self.doc.pk]),('export',[]),('project_new',[])]
        for name,args in targets:
            with self.subTest(endpoint=name):self.assertEqual(client.post(reverse(name,args=args),{}).status_code,403)

    def test_consumption_is_owner_scoped(self):
        from django.utils import timezone
        AIUsage.objects.create(owner=self.other,estimated_usd=Decimal('1.234567'),created=timezone.now(),model='synthetic')
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse('system')).context['spent'],Decimal(0))

    def test_invalid_pdf_and_traversal_cannot_expose_files(self):
        with self.assertRaises(ValidationError):ingest_asset(self.owner,SimpleUploadedFile('bad.pdf',b'%PDF-1.7\nnot a PDF'),document=True)
        self.assertFalse(list((settings.MEDIA_ROOT/'incoming').glob('*.tmp')))
        asset=ingest_asset(self.owner,SimpleUploadedFile('C:\\fakepath\\file.pdf',synthetic_pdf('Safe fixture')),document=True)
        self.assertEqual(asset.name,'file.pdf');self.assertTrue(Path(asset.file.path).is_relative_to(settings.MEDIA_ROOT))
        (self.data/'private.txt').write_text('NEVER EXPOSE THIS')
        asset.file='../private.txt';asset.save(update_fields=['file'])
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse('media',args=[asset.pk])).status_code,404)

    def test_stored_markdown_and_template_content_is_not_executable(self):
        content=rich('<script>alert(1)</script>[click](javascript:alert(1))<img src=x onerror=alert(1)>')
        self.assertNotIn('<script',content);self.assertNotIn('javascript:',content);self.assertNotIn('<img',content)
        project=Project.objects.create(owner=self.owner,name='<script>alert(1)</script>',kind='auto')
        self.client.force_login(self.owner)
        page=self.client.get(reverse('project',args=[project.pk]))
        self.assertNotContains(page,'<script>alert(1)</script>');self.assertContains(page,'&lt;script&gt;')

    def test_gmail_sender_requires_receiver_authentication(self):
        config=MailConfig('imap.gmail.com','mailbox@example.invalid','not-real','sender@example.invalid')
        message=EmailMessage();message['From']='sender@example.invalid'
        self.assertFalse(authenticated_sender(message,config))
        message['Authentication-Results']='mx.google.com; dmarc=fail header.from=example.invalid'
        self.assertFalse(authenticated_sender(message,config))
        message.replace_header('Authentication-Results','mx.google.com; dmarc=pass header.from=example.invalid')
        self.assertTrue(authenticated_sender(message,config))
        message.replace_header('From','"sender@example.invalid" <attacker@invalid.example>')
        self.assertFalse(authenticated_sender(message,config))

    def test_production_check_has_no_warnings(self):
        env={**os.environ,'DEMO_MODE':'0','DEBUG':'0','HTTPS':'1',
            'SECRET_KEY':'release-check-only-not-a-live-credential-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ',
            'DATA_DIR':str(self.data/'deploy-check'),'ALLOWED_HOSTS':'projektakte.example.com',
            'CSRF_TRUSTED_ORIGINS':'https://projektakte.example.com'}
        env.pop('SECRET_KEY_FILE',None)
        import sys
        result=subprocess.run([sys.executable,'manage.py','check','--deploy','--fail-level','WARNING'],cwd=settings.BASE_DIR,env=env,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_double_submission_creates_projects_parts_tasks_and_exports_once(self):
        import uuid
        from .models import Part,Task,Job
        self.client.force_login(self.owner)
        token=uuid.uuid4()
        data={'name':'Once project','kind':'auto','field_editor':'expert','field_json':'{}','request_token':str(token)}
        for _ in range(2):self.assertEqual(self.client.post(reverse('project_new'),data).status_code,302)
        project=Project.objects.get(pk=token)
        for route,model,values in [('part_new',Part,{'name':'Once part','state':'vorhanden'}),('task_new',Task,{'title':'Once task'})]:
            with self.subTest(route=route):
                nonce=uuid.uuid4();payload={**values,'request_token':str(nonce)}
                for _ in range(2):self.assertEqual(self.client.post(reverse(route,args=[project.pk]),payload).status_code,302)
                self.assertEqual(model.objects.filter(pk=nonce).count(),1)
        nonce=uuid.uuid4()
        for _ in range(2):self.assertEqual(self.client.post(reverse('project_export',args=[project.pk]),{'request_token':str(nonce),'kind':'zip'}).status_code,302)
        self.assertEqual(ExportRequest.objects.filter(pk=nonce).count(),1);self.assertEqual(Job.objects.filter(export_id=nonce).count(),1)

    def test_cleanup_only_removes_old_unreferenced_media(self):
        import time
        from django.core.management import call_command
        old=settings.MEDIA_ROOT/'originals'/'orphan.bin';old.write_bytes(b'orphan')
        fresh=settings.MEDIA_ROOT/'originals'/'fresh.bin';fresh.write_bytes(b'fresh')
        os.utime(old,(time.time()-31*86400,time.time()-31*86400))
        os.utime(self.asset.file.path,(time.time()-31*86400,time.time()-31*86400))
        call_command('cleanup',stdout=io.StringIO());self.assertTrue(old.exists())
        call_command('cleanup',apply=True,stdout=io.StringIO())
        self.assertFalse(old.exists());self.assertTrue(fresh.exists());self.assertTrue(Path(self.asset.file.path).exists())

    def test_assetless_capture_and_global_export_render_without_template_errors(self):
        project=Project.objects.create(owner=self.owner,name='Capture project',kind='auto')
        entry=Entry.objects.create(project=project,title='Capture without original')
        Document.objects.create(owner=self.owner,project=project,entry=entry,capture_type='snap',status='accepted')
        self.client.force_login(self.owner)
        for url in (reverse('inbox'),reverse('export'),reverse('project',args=[project.pk])+'?tab=dokumente',reverse('entry',args=[entry.pk]),reverse('search')+'?q=Capture'):
            with self.subTest(url=url):self.assertEqual(self.client.get(url).status_code,200)
