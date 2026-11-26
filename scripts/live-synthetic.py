import os,sys,time,io,json,secrets
from pathlib import Path
from datetime import date,timedelta
from decimal import Decimal
sys.path.insert(0,'/app');os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django;django.setup()
from django.conf import settings
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image,ImageDraw,ImageFont
import reportlab
from akte.files import receive_document,receive_bundle
from akte.models import Project,Entry,CostSource,Allocation,Document,Job,ExportRequest,AIUsage,PhotoLink
from akte.worker import run_one
from akte.billing import accept_document,allocate
from akte.exports import verify_archive
if settings.DEMO_MODE:raise RuntimeError('This check must use actual adapters')
if (settings.DATA_DIR/'db.sqlite3').exists():raise RuntimeError('Use a fresh QA data directory')
call_command('migrate',interactive=False,verbosity=0)
owner=get_user_model().objects.create_user('schnittstellen-test',password=secrets.token_urlsafe(24))
project=Project.objects.create(owner=owner,name='SYNTHETISCHE LIVE-PRUEFUNG 060',kind='auto')
entry=Entry.objects.create(project=project,title='SYNTHETISCHER Ölwechsel',kind='wartung',status='erledigt',date=date(2026,11,12),precision='day',meter=Decimal('165200'),cost_state='known')
source=CostSource.objects.create(owner=owner,label='Bereits erfasster Ölwechsel',amount=Decimal('75'),currency='EUR')
allocate(owner,source,[{'project':project,'entry':entry,'amount':Decimal('75')}])
font=ImageFont.truetype(str(Path(reportlab.__file__).parent/'fonts'/'Vera.ttf'),32)
def photo(lines):
 image=Image.new('RGB',(1654,2339),'white');draw=ImageDraw.Draw(image)
 for index,line in enumerate(lines):draw.text((90,100+index*70),line,font=font,fill='black')
 out=io.BytesIO();image.save(out,'JPEG',quality=90,dpi=(200,200));image.close();return out.getvalue()
marker='SYN-060-'+secrets.token_hex(4)
raw=photo(['SYNTHETISCHE TESTRECHNUNG - KEIN ECHTER BELEG','Projektakte Testteile','Rechnung '+marker,'Rechnungsdatum: 10.11.2026','Leistungsdatum: 12.11.2026','Kilometerstand: 165200 km','Motoröl 5 l x 8,00 EUR brutto = 40,00 EUR','Ölfilter 1 Stück x 20,00 EUR brutto = 20,00 EUR','Versand 1 Stück x 15,00 EUR brutto = 15,00 EUR','Netto: 63,03 EUR','Umsatzsteuer 19%: 11,97 EUR','Gesamtbetrag brutto: 75,00 EUR'])
doc,new=receive_document(owner,SimpleUploadedFile('SYNTHETISCHE-Foto-Rechnung.jpg',raw,'image/jpeg'),project,entry)
again,new2=receive_document(owner,SimpleUploadedFile('SYNTHETISCHE-Foto-Rechnung.jpg',raw,'image/jpeg'),project,entry)
assert doc.pk==again.pk and not new2
import httpx
original_post=httpx.post
http_statuses=[]
def observed_post(*args,**kwargs):
    response=original_post(*args,**kwargs)
    http_statuses.append(response.status_code)
    return response
httpx.post=observed_post
result={'synthetic_only':True,'real_paperless':True,'real_gemini':True,'deduplicated_upload':True,'checks':[]}
started=time.monotonic();queued=False
while time.monotonic()-started<420:
 run_one();doc.refresh_from_db()
 if doc.task_id and not queued:
  for kind in ['pdf','zip','wartung']:
   export=ExportRequest.objects.create(owner=owner,project=project,kind=kind);Job.objects.create(kind='export',export=export,stage='export')
  queued=True
 if doc.status=='review' and doc.proposal:break
 job=Job.objects.get(document=doc)
 if job.status in ('failed','blocked'):break
 time.sleep(.5)
doc.refresh_from_db();result['first_document']={'status':doc.status,'paperless_id':doc.paperless_id,'ocr_chars':len(doc.ocr),'proposal':doc.proposal,'error':doc.error,'elapsed_s':round(time.monotonic()-started,2)}
if doc.status=='review' and doc.proposal:
 proposal=dict(doc.proposal)
 result['first_ai_correct']={key:proposal.get(key)==expected for key,expected in [('invoice_number',marker),('invoice_date','2026-11-10'),('service_date','2026-11-12'),('gross_total','75.00')]}
 # Human review remains explicit; force the documented synthetic expected values.
 proposal.update(invoice_number=marker,invoice_date='2026-11-10',service_date='2026-11-12',gross_total='75.00',net_total='63.03',tax_total='11.97',currency='EUR')
 proposal['positions']=[{'description':'Motoröl','amount':'40.00','quantity':'5','unit':'l','unit_price':'8','price_basis':'gross'},{'description':'Ölfilter','amount':'20.00','quantity':'1','unit':'Stück','unit_price':'20','price_basis':'gross'},{'description':'Versand','amount':'15.00','quantity':'1','unit':'Stück','unit_price':'15','price_basis':'gross'}]
 accepted=accept_document(owner,doc,proposal,[],expected_version=doc.version,connect=source)
 again=accept_document(owner,doc,proposal,[],expected_version=doc.version,connect=source)
 assert accepted.pk==again.pk and CostSource.objects.count()==1 and project.cost==Decimal('75.00')
 result['once_only_accept']={'sources':CostSource.objects.count(),'project_cost':str(project.cost),'invoice_date':str(CostSource.objects.get(pk=source.pk).invoice_date),'work_date':str(entry.date)}
else:result['once_only_accept']='not reached'
# Two real image pages, one Paperless document; no personal receipts.
page1=photo(['SYNTHETISCHE TESTRECHNUNG - SEITE 1/2','Projektakte Testbedarf','Rechnung MULTI-'+marker,'Rechnungsdatum: 09.11.2026','Auto-Testteil 1 Stück x 60,00 EUR brutto = 60,00 EUR','Fortsetzung auf Seite 2'])
page2=photo(['SYNTHETISCHE TESTRECHNUNG - SEITE 2/2','Rechnung MULTI-'+marker,'Aquarium-Testteil 1 Stück x 40,00 EUR brutto = 40,00 EUR','Gesamtbetrag brutto: 100,00 EUR','Währung EUR'])
multi,new=receive_bundle(owner,[SimpleUploadedFile('test-seite1.jpg',page1,'image/jpeg'),SimpleUploadedFile('test-seite2.jpg',page2,'image/jpeg')],project)
started_multi=time.monotonic()
while time.monotonic()-started_multi<360:
 run_one();multi.refresh_from_db()
 if multi.status=='review' and multi.proposal:break
 if multi.job.status in ('failed','blocked'):break
 time.sleep(.5)
result['multi_page']={'status':multi.status,'paperless_id':multi.paperless_id,'pages':multi.pages.count(),'ocr_chars':len(multi.ocr),'proposal':multi.proposal,'error':multi.error,'elapsed_s':round(time.monotonic()-started_multi,2)}
if multi.status=='review' and multi.proposal:
 aquarium=Project.objects.create(owner=owner,name='SYNTHETISCHES Aquarium',kind='aquarium')
 values={'invoice_number':'MULTI-'+marker,'invoice_date':'2026-11-09','service_date':None,'gross_total':'100.00','currency':'EUR','vendor':'Synthetischer Testbedarf','positions':[{'description':'Auto-Testteil','amount':'60.00'},{'description':'Aquarium-Testteil','amount':'40.00'}]}
 accept_document(owner,multi,values,[{'project':project,'amount':Decimal('60'),'position_ref':'1'},{'project':aquarium,'amount':Decimal('40'),'position_ref':'2'}],expected_version=multi.version)
 result['multi_page']['allocation']={'auto':str(project.cost),'aquarium':str(aquarium.cost),'sources':CostSource.objects.count()}
 assert project.cost==Decimal('135') and aquarium.cost==Decimal('40')
result['gemini_http_statuses']=http_statuses

# Complete queued exports independently of delayed provider jobs.
for _ in range(40):
 run_one()
 if not Job.objects.filter(kind='export').exclude(status__in=['done','failed']).exists():break
 time.sleep(.1)
result['exports']=list(ExportRequest.objects.values('kind','status','error'))
for export in ExportRequest.objects.filter(kind='zip',status='done'):verify_archive(settings.DATA_DIR/export.path)
result['estimated_or_reserved_usd']=str(sum(AIUsage.objects.values_list('estimated_usd',flat=True),Decimal('0')))
(settings.DATA_DIR/'Live-Pruefung.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
print(json.dumps(result,indent=2,ensure_ascii=False))
