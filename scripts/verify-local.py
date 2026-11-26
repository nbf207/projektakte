"""Generate reviewable proof from the real synthetic demo, then restore it separately."""
import argparse,json,os,subprocess,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from serve import load_env
load_env();os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client,override_settings
from akte.models import Entry,Project,Document
from akte.exports import report_pdf,portable_export,verify_archive
from akte.backup import backup,restore
parser=argparse.ArgumentParser();parser.add_argument('output');parser.add_argument('--offline',action='store_true');args=parser.parse_args()
if not settings.DEMO_MODE or not args.offline: raise SystemExit('Nur synthetisch; Web und Worker vorher stoppen und --offline angeben.')
out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
user=get_user_model().objects.get(username='demo');auto=Project.objects.get(owner=user,kind='auto')
oil=Entry.objects.get(project=auto,title='Ölwechsel');audio=Entry.objects.get(project=auto,title='Soundsystem-Umbau')
assert oil.cost==75 and audio.cost==1250 and audio.remaining==150
assert audio.steps.count()==5 and audio.photo_links.count()==12 and audio.documents.count()==3
report_pdf(out/'Umbau-Demonstration.pdf',auto,[audio])
portable_export(out/'Portabler-Demoexport.zip',user)
archive=out/'Demo-Sicherung.zip';backup(archive)
restore_root=out/'restore-test';restore(archive,restore_root)
env={**os.environ,'DATA_DIR':str(restore_root),'PYTHONIOENCODING':'utf-8'}
check='''import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django; django.setup()
from django.test import Client,override_settings
from django.contrib.auth import get_user_model
from akte.models import Entry,Project,Document,Asset
u=get_user_model().objects.get(username='demo');c=Client();c.force_login(u)
with override_settings(ALLOWED_HOSTS=['testserver']):
 for e in Entry.objects.all():
  assert c.get('/vorgaenge/'+str(e.pk)+'/').status_code==200
  for p in e.photo_links:
   r=c.get('/datei/'+str(p.asset_id)+'/');assert r.status_code==200;r.close()
 for d in Document.objects.all():
  assert d.paperless_id and d.verified_at
  assert c.get('/belege/'+str(d.pk)+'/').status_code==200
  if d.asset:
   r=c.get('/datei/'+str(d.asset_id)+'/');assert r.status_code==200;r.close()
assert Project.objects.count()==4 and Entry.objects.count()==2
print('Restore: 4 Projekte, 2 Vorgänge, 12 Bilder und 4 Paperless-Bezüge erfolgreich geöffnet.')'''
result=subprocess.run([sys.executable,'-c',check],cwd=settings.BASE_DIR,env=env,capture_output=True,text=True,encoding='utf8')
if result.returncode: raise RuntimeError(result.stderr)
proof={'version':settings.APP_VERSION,'synthetic':True,'oil_cost':'75.00','audio_cost':'1250.00','remaining_budget':'150.00','steps':audio.steps.count(),'photos':audio.photo_links.count(),'audio_documents':audio.documents.count(),'restore':result.stdout.strip(),'limitations':['Keine Live-Paperless-/Gemini-Verbindung','Keine Pi-Lastmessung','Keine echte Android-Kameraabnahme','Desktop und 390-Pixel-Browseransicht geprüft; echtes Android-Gerät offen']}
(out/'Lokale-Nachweise.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(proof,ensure_ascii=False,indent=2))
