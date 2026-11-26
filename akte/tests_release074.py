"""Direct tests for account, settings, media and serial system actions."""
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import uuid
import zipfile
from decimal import Decimal
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PIL import Image
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import (Preferences, SessionDevice, LoginAttempt, Project, Entry, Asset, Document,
    CostSource, Allocation, PhotoLink, Job, AIUsage, ManagedBackup, ControlTask, ErrorEvent)
from .control_tasks import schedule_control, create_backup, periodic_housekeeping, restart_stale
from .feedback_store import list_feedback, read_feedback, image_path
from .files import ingest_asset
from .media_controls import orphan_files, remove_orphans, compress_capture_photos
from .worker import run_one, reserve_ai, record_usage
from .adapters import IntegrationError
from .adapters import GeminiAI, AIRequestRejected


class Settings074Tests(TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.root=Path(folder.name)
        override=override_settings(DATA_DIR=self.root,MEDIA_ROOT=self.root/'media',DEMO_MODE=True,ALLOWED_HOSTS=['testserver'],
            PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],LOGIN_RATE_LIMIT=100)
        override.enable();self.addCleanup(override.disable)
        (self.root/'media').mkdir();self.user=get_user_model().objects.create_user('SYN074',password='SYN-old-password-074')
        self.project=Project.objects.create(owner=self.user,name='SYN Wagen')
        self.client.force_login(self.user)

    def url(self,section):return reverse('settings_center',args=[section])
    def pref(self,**values):
        row,_=Preferences.objects.get_or_create(owner=self.user)
        for key,value in values.items():setattr(row,key,value)
        row.save();return row
    def photo(self,name='SYN.jpg',width=3200):
        stream=io.BytesIO();Image.new('RGB',(width,1000),(70,130,160)).save(stream,'JPEG',quality=97)
        return SimpleUploadedFile(name,stream.getvalue(),content_type='image/jpeg')

    def test_all_settings_pages_render_and_require_login_csrf(self):
        for section in ('konto','anbindungen','medien','system','feedback'):
            response=self.client.get(self.url(section));self.assertEqual(response.status_code,200)
            self.assertContains(response,'aria-current="page"')
            self.assertEqual(Client().get(self.url(section)).status_code,302)
        client=Client(enforce_csrf_checks=True);client.force_login(self.user)
        self.assertEqual(client.post(self.url('konto'),{'action':'sessions'}).status_code,403)
        self.assertEqual(client.post(reverse('control_action'),{'kind':'backup','request_key':uuid.uuid4()}).status_code,403)

    def test_password_keeps_this_session_and_invalidates_others(self):
        other=Client();other.force_login(self.user);old_key=self.client.session.session_key
        response=self.client.post(self.url('konto'),{'action':'password','old_password':'SYN-old-password-074',
            'new_password1':'SYN-new-password-074','new_password2':'SYN-new-password-074'})
        self.assertEqual(response.status_code,302);self.assertNotEqual(self.client.session.session_key,old_key)
        self.assertEqual(self.client.get(reverse('home')).status_code,200)
        self.assertEqual(other.get(reverse('home')).status_code,302)
        self.user.refresh_from_db();self.assertTrue(self.user.check_password('SYN-new-password-074'))
        self.assertTrue(SessionDevice.objects.filter(owner=self.user,session_key=self.client.session.session_key).exists())

    def test_duration_revoke_legacy_sessions_and_bounded_login_history(self):
        self.client.post(self.url('konto'),{'action':'preferences','version':0,'session_days':7})
        other=Client();other.force_login(self.user)
        self.assertLessEqual(other.session.get_expiry_age(),7*86400)
        SessionDevice.objects.filter(session_key=other.session.session_key).delete()
        page=self.client.get(self.url('konto'));self.assertContains(page,'Ältere Sitzung')
        self.client.post(self.url('konto'),{'action':'sessions'})
        self.assertEqual(other.get(reverse('home')).status_code,302)
        self.assertEqual(self.client.get(reverse('home')).status_code,200)
        attacker=Client()
        for unused in range(12):attacker.post(reverse('login'),{'username':self.user.username,'password':'SYN wrong'})
        self.assertEqual(LoginAttempt.objects.filter(owner=self.user).count(),10)
        self.assertFalse(LoginAttempt.objects.filter(owner=self.user,success=True).exists())

    def test_models_preserve_reserved_prices_and_budget_limit(self):
        self.pref(ai_mode='complex')
        doc=Document.objects.create(owner=self.user,ocr='SYN simple note',ai_enabled=True)
        with override_settings(DEMO_MODE=False,AI_INPUT_USD_PER_M=Decimal('.30'),AI_OUTPUT_USD_PER_M=Decimal('2.50'),AI_MONTHLY_LIMIT=Decimal('4.5')):
            usage=reserve_ai(doc)
            self.assertEqual(usage.model,'gemini-3.8-flash')
            input_rate=usage.input_rate;output_rate=usage.output_rate
            self.pref(ai_mode='daily');record_usage(usage,{'promptTokenCount':1000,'totalTokenCount':2000})
            usage.refresh_from_db();self.assertEqual(usage.estimated_usd,(input_rate+output_rate)/1000)
            AIUsage.objects.create(owner=self.user,estimated_usd='4.5',model='SYN')
            with self.assertRaises(IntegrationError):reserve_ai(doc)

    def test_model_specific_thinking_parameters(self):
        with override_settings(GEMINI_API_KEY='SYN nonsecret key'),patch('akte.adapters.httpx.post') as post:
            post.return_value.status_code=400
            for model,level in [('gemini-3.8-flash','low'),('gemini-3.5-flash-lite','minimal')]:
                with self.assertRaises(AIRequestRejected):GeminiAI(model).extract('SYN text')
                self.assertIn(model,post.call_args.args[0])
                self.assertEqual(post.call_args.kwargs['json']['generationConfig']['thinkingConfig']['thinkingLevel'],level)
                if model=='gemini-3.8-flash':self.assertEqual(post.call_args.kwargs['timeout'].read,150)

    def test_complex_timeout_blocks_retry_keeps_budget_and_work(self):
        import httpx
        from .adapters import AIOutcomeUncertain
        from .worker import reserve,fail
        doc=Document.objects.create(owner=self.user,status='ai',ocr='SYN retained')
        Job.objects.create(document=doc,kind='capture',stage='capture')
        job=reserve();self.assertGreater(job.leased_until,timezone.now()+timedelta(minutes=4))
        usage=AIUsage.objects.create(owner=self.user,document=doc,estimated_usd='.8',model='gemini-3.8-flash')
        with override_settings(GEMINI_API_KEY='SYN'),patch('akte.adapters.httpx.post',side_effect=httpx.ReadTimeout('SYN')):
            with self.assertRaises(AIOutcomeUncertain) as error:GeminiAI('gemini-3.8-flash').extract('SYN text')
        fail(job,error.exception);job.refresh_from_db();doc.refresh_from_db();usage.refresh_from_db()
        self.assertEqual(job.status,'blocked');self.assertEqual(doc.ocr,'SYN retained')
        self.assertEqual(usage.state,'reserved');self.assertEqual(usage.estimated_usd,Decimal('.8'))

    def test_integration_preferences_validate_and_are_versioned(self):
        payload={'action':'preferences','version':0,'ai_mode':'complex','keep_audio':'on','paperless_tags':'1, 2, 1',
            'mail_senders':'sender@example.invalid\nalice@example.com'}
        self.assertEqual(self.client.post(self.url('anbindungen'),payload).status_code,302)
        pref=Preferences.objects.get(owner=self.user);self.assertEqual(pref.paperless_tags,[1,2]);self.assertTrue(pref.keep_audio)
        payload.update(version=pref.version,mail_senders='bad\naddress',paperless_tags='../../etc')
        self.assertEqual(self.client.post(self.url('anbindungen'),payload).status_code,200)
        pref.refresh_from_db();self.assertEqual(pref.mail_senders,['sender@example.invalid','alice@example.com'])

    def test_work_photos_compress_but_invoice_originals_remain_exact(self):
        self.pref(photo_width=1600,photo_quality=85)
        photo=self.photo();original=photo.read();photo.seek(0)
        asset=ingest_asset(self.user,photo)
        with Image.open(asset.file.path) as image:self.assertEqual(image.width,1600)
        self.assertNotEqual(asset.sha256,hashlib.sha256(original).hexdigest())
        receipt=ingest_asset(self.user,SimpleUploadedFile('SYN invoice.jpg',original),document=True)
        self.assertEqual(Path(receipt.file.path).read_bytes(),original)
        doc=Document.objects.create(owner=self.user,project=self.project,entry=Entry.objects.create(project=self.project),capture_type='snap',voice_data={'result':{'is_receipt':True}})
        from .models import DocumentPage
        DocumentPage.objects.create(document=doc,asset=receipt,position=0)
        compress_capture_photos(doc);self.assertEqual(doc.pages.get().asset_id,receipt.pk)
        doc.voice_data={'result':{'is_receipt':False}};doc.save()
        PhotoLink.objects.create(entry=doc.entry,asset=receipt)
        # The same original remains retained by a true invoice.
        Document.objects.create(owner=self.user,asset=receipt,status='review')
        compress_capture_photos(doc)
        self.assertEqual(doc.pages.get().asset_id,asset.pk);self.assertTrue(Path(receipt.file.path).exists())

    def test_audio_default_and_shared_file_retention(self):
        import wave
        from .capture import receive_capture,discard_capture_audio
        stream=io.BytesIO()
        with wave.open(stream,'wb') as audio:audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(8000);audio.writeframes(b'\0\0'*800)
        self.pref(keep_audio=True)
        first=receive_capture(self.user,text='SYN first',audio=SimpleUploadedFile('SYN.wav',stream.getvalue()),project=self.project,token=uuid.uuid4())
        self.assertTrue(first.keep_audio);discard_capture_audio(first);asset=first.pages.get().asset
        self.pref(keep_audio=False)
        second=receive_capture(self.user,text='SYN second',audio=SimpleUploadedFile('SYN.wav',stream.getvalue()),project=self.project,token=uuid.uuid4())
        discard_capture_audio(second);self.assertFalse(second.pages.exists());self.assertTrue(Path(asset.file.path).exists())

    def test_repeated_snap_after_compression_leaves_no_unused_original(self):
        from .capture import receive_capture,finish_chronicle
        self.pref(photo_width=1600)
        original=self.photo().read()
        first=receive_capture(self.user,text='SYN work',uploads=[SimpleUploadedFile('SYN.jpg',original)],project=self.project,token=uuid.uuid4())
        first.voice_data['result']={'is_receipt':False};first.save()
        with self.captureOnCommitCallbacks(execute=True):finish_chronicle(first)
        self.assertEqual(Asset.objects.count(),1)
        with self.captureOnCommitCallbacks(execute=True):
            again=receive_capture(self.user,text='SYN work',uploads=[SimpleUploadedFile('SYN.jpg',original)],project=self.project,token=uuid.uuid4())
        self.assertEqual(again.pk,first.pk);self.assertEqual(Asset.objects.count(),1)
        self.assertEqual(len(list((self.root/'media/originals').iterdir())),1)

    def test_feedback_image_edit_ownership_and_backup(self):
        response=self.client.post(reverse('feedback'),{'text':'SYN <script>bad</script>','image':self.photo(width=400)},HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code,200)
        row=list_feedback(self.user)[0];image=row['record']['images'][0]
        self.assertTrue(image_path(self.user,row['key'],image['id']).exists())
        self.assertContains(self.client.get(self.url('feedback')),'&lt;script&gt;')
        url=reverse('feedback_image',args=[row['key'],image['id']]);response=self.client.get(url);self.assertEqual(response.status_code,200);response.close()
        other=get_user_model().objects.create_user('SYN other');client=Client();client.force_login(other)
        self.assertEqual(client.get(url).status_code,404)
        data={'action':'feedback_edit','key':row['key'],'digest':row['digest'],'text':'SYN corrected','done':'on'}
        self.assertEqual(self.client.post(self.url('feedback'),data).status_code,302)
        self.assertEqual(read_feedback(self.user,row['key'])[0]['status'],'done')
        self.assertEqual(self.client.post(self.url('feedback'),data).status_code,200)
        self.assertEqual(self.client.post(reverse('feedback'),{'text':'SYN','image':SimpleUploadedFile('bad.svg',b'<svg/>')}).status_code,400)
        db=sqlite3.connect(self.root/'db.sqlite3');db.execute('CREATE TABLE synthetic(id integer)');db.close()
        from .backup import backup
        result=backup(self.root/'archive.zip')
        with zipfile.ZipFile(result) as z:self.assertTrue(any(name.startswith('feedback/images/') for name in z.namelist()))

    def test_orphan_cleanup_is_previewed_reference_checked_and_scoped(self):
        folder=self.root/'media/originals';folder.mkdir();file=folder/'orphan.jpg';file.write_bytes(b'SYN')
        os.utime(file,(time_value:=timezone.now().timestamp()-1200,time_value))
        rows=orphan_files(self.user);self.assertEqual(len(rows),1)
        Asset.objects.create(owner=self.user,file='originals/orphan.jpg',sha256='1'*64,name='SYN',mime='image/jpeg',size=3)
        with self.assertRaises(ValidationError):remove_orphans(self.user,[rows[0]['token']])
        self.assertTrue(file.exists())
        token=signing.dumps({'owner':self.user.pk,'path':'../outside','size':1,'mtime':1},salt='orphan074')
        with self.assertRaises(ValidationError):remove_orphans(self.user,[token])
        Asset.objects.all().delete();self.assertEqual(remove_orphans(self.user,[orphan_files(self.user)[0]['token']]),1)
        self.assertFalse(file.exists())

    def test_trash_policy_keeps_costs_until_explicit_consent(self):
        old=timezone.now()-timedelta(days=40)
        Entry.objects.create(project=self.project,title='SYN discarded',deleted_at=old)
        live=Entry.objects.create(project=self.project,title='SYN keep')
        doc=Document.objects.create(owner=self.user,project=self.project,deleted_at=old)
        source=CostSource.objects.create(owner=self.user,document=doc,label='SYN receipt',amount='12.34')
        self.pref(trash_days=30);periodic_housekeeping()
        self.assertFalse(Entry.objects.filter(title='SYN discarded').exists());self.assertTrue(CostSource.objects.filter(pk=source.pk).exists())
        page=self.client.get(self.url('medien'));token=page.context['trash_preview']
        source.amount='13.34';source.save()
        self.assertEqual(self.client.post(self.url('medien'),{'action':'empty','snapshot':token,'confirmed':'LEEREN','remove_costs':'1'}).status_code,200)
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())
        self.pref(trash_days=30,trash_costs=True);periodic_housekeeping()
        self.assertFalse(Document.objects.filter(pk=doc.pk).exists());self.assertFalse(CostSource.objects.filter(pk=source.pk).exists())
        self.assertTrue(Entry.objects.filter(pk=live.pk).exists())

    def test_paperless_queue_dedup_and_accounting_preserved(self):
        self.pref(paperless_tags=[7])
        remote={'id':42,'content':'SYN bill','modified':'2026-11-01','tags':[7]}
        adapter=Mock();adapter.request.return_value.json.return_value={'results':[remote],'next':None}
        with override_settings(DEMO_MODE=False,PAPERLESS_URL='http://paperless.test'),patch('akte.control_tasks.paperless',return_value=adapter):
            key=uuid.uuid4();task=schedule_control(self.user,'paperless_sync',key)
            self.assertEqual(schedule_control(self.user,'paperless_sync',key).pk,task.pk)
            self.assertTrue(run_one());doc=Document.objects.get(paperless_id=42)
            self.assertEqual(doc.status,'review');self.assertTrue(doc.entry.is_provisional)
            self.assertEqual(adapter.request.call_args.kwargs['params']['tags__id__in'],'7')
            self.assertEqual(AIUsage.objects.count(),0);self.assertEqual(CostSource.objects.count(),0)
            source=CostSource.objects.create(owner=self.user,document=doc,label='SYN already accepted',amount='12.34')
            doc.status='accepted';doc.save();remote['modified']='2026-11-02'
            schedule_control(self.user,'paperless_sync',uuid.uuid4());run_one();doc.refresh_from_db();source.refresh_from_db()
            self.assertEqual(doc.status,'accepted');self.assertEqual(doc.remote_state,'changed');self.assertEqual(str(source.amount),'12.34')
            self.assertEqual(Document.objects.count(),1)

    def test_imap_whitelist_and_unread_ignored_senders(self):
        from .mail_ingest import MailPoller
        from .mail_config import MailConfig
        self.pref(mail_override=True,mail_senders=['alice@example.com'])
        config=MailConfig('imap.gmail.com','SYN@example.com','SYN NOT REAL',capture_user=self.user.username)
        client=Mock();client.select.return_value=('OK',[])
        def uid(command,*args):
            if command=='search':return 'OK',[b'1 2']
            if command=='store':return 'OK',[]
            if args[1].startswith('(RFC822'):
                address='evil@example.com' if args[0]=='1' else 'alice@example.com'
                return 'OK',[(b'RFC822.SIZE 400',('From: '+address+'\r\n\r\n').encode())]
            return 'OK',[(b'BODY[]',b'SYN mail')]
        client.uid.side_effect=uid;context=Mock();context.__enter__=Mock(return_value=client);context.__exit__=Mock(return_value=False)
        with patch('akte.mail_ingest.imaplib.IMAP4_SSL',return_value=context),patch('akte.mail_ingest.import_message',return_value=object()) as ingest:
            self.assertEqual(MailPoller().collect(config),1)
            self.assertEqual(ingest.call_args.args[1].sender,'alice@example.com')
            stores=[call.args for call in client.uid.call_args_list if call.args[0]=='store'];self.assertEqual(stores[0][1],'2')

    def test_managed_backup_download_retention_and_path_safety(self):
        self.pref(backup_keep=1)
        first=uuid.uuid4()
        db=sqlite3.connect(self.root/'db.sqlite3');db.execute('CREATE TABLE synthetic(id integer)')
        db.execute('CREATE TABLE akte_job(id text,status text,lease_token text,leased_until text,control_id text)')
        db.execute('INSERT INTO akte_job VALUES (?,?,?,?,?)',('SYN','running','SYN lease','2099',first.hex));db.commit();db.close()
        create_backup(self.user,first);first_path=self.root/ManagedBackup.objects.get().path
        with zipfile.ZipFile(first_path) as archive:
            copied=self.root/'copied.sqlite3';copied.write_bytes(archive.read('db.sqlite3'))
        with closing(sqlite3.connect(copied)) as check:self.assertEqual(check.execute('SELECT status FROM akte_job').fetchone()[0],'done')
        with closing(sqlite3.connect(self.root/'db.sqlite3')) as check:self.assertEqual(check.execute('SELECT status FROM akte_job').fetchone()[0],'running')
        protected=self.root/'safety/rollback.zip';protected.parent.mkdir();protected.write_bytes(first_path.read_bytes())
        legacy=ManagedBackup.objects.create(owner=self.user,path='safety/rollback.zip',size=protected.stat().st_size)
        create_backup(self.user,uuid.uuid4());self.assertFalse(first_path.exists());self.assertTrue(protected.exists())
        record=ManagedBackup.objects.get(managed=True)
        response=self.client.get(reverse('backup_download',args=[record.pk]));self.assertEqual(response.status_code,200);response.close()
        other=get_user_model().objects.create_user('SYN foreign');client=Client();client.force_login(other)
        self.assertEqual(client.get(reverse('backup_download',args=[record.pk])).status_code,404)
        self.user.is_superuser=True;self.user.save()
        malicious=ManagedBackup.objects.create(owner=self.user,path='../outside.zip',size=1)
        self.assertEqual(self.client.get(reverse('backup_download',args=[malicious.pk])).status_code,404)

    def test_expired_jobs_restart_without_reopening_accepted_invoices(self):
        doc=Document.objects.create(owner=self.user,status='accepted')
        first=Job.objects.create(document=doc,status='running',leased_until=timezone.now()-timedelta(minutes=1),lease_token=uuid.uuid4())
        second_doc=Document.objects.create(owner=self.user,status='ai')
        second=Job.objects.create(document=second_doc,status='running',leased_until=timezone.now()+timedelta(minutes=1),lease_token=uuid.uuid4())
        self.assertEqual(restart_stale(self.user),1);first.refresh_from_db();second.refresh_from_db();doc.refresh_from_db()
        self.assertEqual(first.status,'done');self.assertEqual(second.status,'running');self.assertEqual(doc.status,'accepted')

    def test_handover_hides_selected_data_and_other_project(self):
        entry=Entry.objects.create(project=self.project,title='SYN work',description='SYN_PRIVATE 12.34 SYN_DEALER',snapshot={'extra_fields':[{'label':'Preis','value':'SYN_PRICE','unit':''}]})
        doc=Document.objects.create(owner=self.user,project=self.project,entry=entry,proposal={'vendor':'SYN_DEALER'})
        source=CostSource.objects.create(owner=self.user,document=doc,label='SYN',amount='12.34')
        Allocation.objects.create(source=source,project=self.project,entry=entry,amount='12.34')
        response=self.client.post(reverse('handover'),{'project':self.project.pk,'hide_costs':'on','hide_notes':'on','hide_dealers':'on'})
        self.assertEqual(response.status_code,200)
        for text in ('12.34','SYN_PRIVATE','SYN_DEALER','SYN_PRICE'):self.assertNotContains(response,text)
        response=self.client.post(reverse('handover'),{'project':self.project.pk});self.assertContains(response,'SYN_PRIVATE');self.assertContains(response,'12.34')
        other=get_user_model().objects.create_user('SYN export foreign');project=Project.objects.create(owner=other,name='SYN foreign')
        self.assertEqual(self.client.post(reverse('handover'),{'project':project.pk}).status_code,400)

    def test_error_records_are_bounded_sanitized_and_owner_scoped(self):
        from .account_events import error_event,SafeErrorHandler
        import logging
        for index in range(35):error_event('SYN','SYN error '+str(index),self.user)
        self.assertEqual(ErrorEvent.objects.filter(owner=self.user).count(),30)
        other=get_user_model().objects.create_user('SYN log foreign');error_event('SYN','SYN FOREIGN ERROR',other)
        request=SimpleNamespace(user=self.user,resolver_match=SimpleNamespace(url_name='review'))
        record=logging.LogRecord('django.request',logging.ERROR,'',1,'password=SYN_SECRET',(),None)
        record.request=request;record.status_code=500;SafeErrorHandler().emit(record)
        response=self.client.get(self.url('system'));self.assertContains(response,'HTTP 500');self.assertNotContains(response,'SYN_SECRET');self.assertNotContains(response,'SYN FOREIGN ERROR')
        task=ControlTask.objects.create(owner=other,kind='backup',request_key=uuid.uuid4(),result='SYN FOREIGN RESULT')
        Job.objects.create(kind='control',control=task)
        status=self.client.get(reverse('settings_status'),{'monitor':1})
        self.assertEqual(status.status_code,200);self.assertNotContains(status,'SYN FOREIGN RESULT')
        self.assertIsInstance(status.json()['monitor']['free'],int)
        self.assertEqual(Client().get(reverse('settings_status')).status_code,302)


class Settings074MigrationTests(TransactionTestCase):
    def test_073_account_documents_costs_and_title_values_survive(self):
        MigrationExecutor(connection).migrate([('akte','0020_project_builder')])
        old=MigrationExecutor(connection).loader.project_state([('akte','0020_project_builder')]).apps
        user=old.get_model('auth','User').objects.create(username='SYN migration074',password='SYN unchanged hash')
        project=old.get_model('akte','Project').objects.create(owner_id=user.pk,name='SYN',ai_profile='SYN profile',hidden_tabs=['teile'])
        entry=old.get_model('akte','Entry').objects.create(project=project,title='SYN',snapshot={'oil':'KEEP','extra_fields':[{'id':'SYN','value':'KEEP'}]})
        source=old.get_model('akte','CostSource').objects.create(owner_id=user.pk,label='SYN cost',amount='12.34')
        MigrationExecutor(connection).migrate([('akte','0021_settings_center')])
        self.assertEqual(get_user_model().objects.get(pk=user.pk).password,'SYN unchanged hash')
        self.assertEqual(Project.objects.get(pk=project.pk).ai_profile,'SYN profile')
        self.assertEqual(Entry.objects.get(pk=entry.pk).snapshot,entry.snapshot)
        self.assertEqual(str(CostSource.objects.get(pk=source.pk).amount),'12.34')
        with connection.cursor() as cursor:cursor.execute('PRAGMA foreign_key_check');self.assertEqual(cursor.fetchall(),[])
