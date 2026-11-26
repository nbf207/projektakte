"""Synthetic desktop benchmark in an explicitly isolated, initially empty DATA_DIR."""
import json,os,statistics,sys,time,uuid,platform
from datetime import date
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.conf import settings
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.test import Client,override_settings
from django.urls import reverse
from akte.models import Project,Entry,PhotoLink,Asset
from akte.services import ingest_asset,save_entry,thumbnail
from akte.demo import synthetic_photo
from django.core.files.uploadedfile import SimpleUploadedFile
if not settings.DEMO_MODE: raise SystemExit('Nur synthetisch.')
call_command('migrate',verbosity=0)
if Project.objects.exists(): raise SystemExit('Abbruch: DATA_DIR muss ein frischer, getrennter Testbestand sein.')
user=get_user_model().objects.create_user('benchmark',password=uuid.uuid4().hex)
projects=[Project(owner=user,name=f'Benchmark {i}',kind='frei') for i in range(10)];Project.objects.bulk_create(projects)
entries=[Entry(project=projects[i%10],title=f'Wartung {i}',kind='wartung',date=date(2026,11,i%28+1),snapshot={'filter':f'DEMO-PART-{i}'}) for i in range(5000)];Entry.objects.bulk_create(entries,batch_size=500)
asset=ingest_asset(user,SimpleUploadedFile('synthetisch.jpg',synthetic_photo(1)));thumbnail(asset)
PhotoLink.objects.bulk_create([PhotoLink(entry=entries[i%5000],asset=asset,position=i//5000,caption=f'Bildverknüpfung {i}') for i in range(10000)],batch_size=500)
client=Client();client.force_login(user);timings={}
with override_settings(ALLOWED_HOSTS=['testserver']):
    for name,url in [('Chronik',reverse('project',args=[projects[0].pk])),('Suche',reverse('search')+'?q=DEMO-PART-4999'),('Details',reverse('entry',args=[entries[0].pk]))]:
        samples=[]
        for _ in range(12):
            start=time.perf_counter();response=client.get(url);assert response.status_code==200;samples.append(time.perf_counter()-start)
        timings[name]={'median_ms':round(statistics.median(samples)*1000,1),'max_ms':round(max(samples)*1000,1),'samples':len(samples)}
    samples=[]
    for i in range(12):
        start=time.perf_counter();save_entry(user,projects[0],{'title':f'Speichertest {i}','kind':'notiz','date':date(2026,11,12),'snapshot':{'synthetic':True}},uuid.uuid4());samples.append(time.perf_counter()-start)
    timings['Speichern']={'median_ms':round(statistics.median(samples)*1000,1),'max_ms':round(max(samples)*1000,1),'samples':len(samples)}
result={'environment':platform.system()+' '+platform.machine(), 'execution':'Django-Testclient, kein HTTP-Netzwerktest','dataset':{'projects':10,'entries':5000,'photo_links':10000,'distinct_test_images':1},'timings':timings,'warning':'Host-RAM und Gesamtlast auf dem Pi separat messen. Bilder werden hier geteilt; dies misst DB/Ansichten, keine reale Fotoserie.'}
Path(sys.argv[1]).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(result,ensure_ascii=False,indent=2))
