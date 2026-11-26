from akte.operations import guarded
import uuid
from datetime import date
from decimal import Decimal
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand,CommandError
from django.utils import timezone
from akte.models import Project, Entry, Step, PhotoLink, Document, Job, CostSource, Allocation, Part, Task
from akte.services import save_entry,receive_document,accept_document,ingest_asset,thumbnail
from akte.adapters import demo_proposal
from akte.demo import synthetic_photo,synthetic_pdf

def uid(name): return uuid.uuid5(uuid.NAMESPACE_URL,'projektakte-demo:'+name)

class Command(BaseCommand):
    help='Reproduzierbare synthetische Testdaten. Ausschließlich DEMO_MODE=1.'
    def add_arguments(self, parser):
        parser.add_argument('--public', action='store_true')

    @guarded
    def handle(self,*args,**options):
        if options.get('public'):
            from akte.demo_boundary import demo_scope
            from akte.demo_seed import seed_story, expire_visitors
            with demo_scope():
                user, _ = get_user_model().objects.get_or_create(username='demo', defaults={'is_staff': False, 'is_superuser': False})
                user.set_unusable_password()
                user.is_staff = user.is_superuser = False
                user.save(using='demo')
                seed_story(user)
                expire_visitors()
            self.stdout.write('Isolierter Musterbestand in demo.sqlite3 angelegt.')
            return
        if not settings.DEMO_MODE: raise CommandError('Testdaten sind im Livebetrieb gesperrt.')
        User=get_user_model();user,new=User.objects.get_or_create(username='demo')
        if new: user.set_password('Projektakte-Demo-2026!');user.save()
        auto,new=Project.objects.get_or_create(pk=uid('auto'),defaults={'owner':user,'name':'BMW – Demonstration','kind':'auto','description':'Wartung, Technik und Umbauten. Synthetische Demonstration.','favorite':True,'fields':{'Öl':{'type':'text','value':'Demo 5W-40'},'Baujahr':{'type':'number','value':2010}}})
        for kind,name in [('aquarium','Aquarium – Demonstration'),('drohne','Drohne – Demonstration'),('frei','Werkstatt – Demonstration')]: Project.objects.get_or_create(pk=uid(kind),defaults={'owner':user,'name':name,'kind':kind,'description':'Synthetisches Beispielprojekt.'})
        oil=Entry.objects.filter(request_key=uid('oil')).first()
        if not oil:
            oil=save_entry(user,auto,{'title':'Ölwechsel','kind':'wartung','status':'erledigt','date':date(2026,11,12),'precision':'day','meter':Decimal('165200'),'snapshot':{'oil':'Demo 5W-40','quantity_l':'5.0','filter':'DEMO-FILTER-01','project_fields':auto.fields},'cost_state':'known','manual_amount':Decimal('75.00'),'description':'Motoröl und Filter gewechselt. Angaben aus dem synthetischen Bedienbeispiel.'},uid('oil'))
        audio=Entry.objects.filter(request_key=uid('audio')).first()
        if not audio:
            audio=save_entry(user,auto,{'title':'Soundsystem-Umbau','kind':'umbau','status':'erledigt','date':date(2026,11,1),'precision':'day','end_date':date(2026,11,12),'budget':Decimal('1400'),'cost_state':'known','snapshot':{'project_fields':auto.fields},'description':'## Ziel\nKlarer Klang und sauber verlegte Kabel.\n\n## Ergebnis\nRückrüstbarer Einbau mit dokumentierten Arbeitsschritten.\n\n- Ausgangszustand festgehalten\n- Komponenten geprüft\n- Abschluss dokumentiert'},uid('audio'))
        steps=[]
        for i,title in enumerate(['Ausgangszustand dokumentieren','Türverkleidung und Leitungen','Lautsprecher einbauen','Verstärker anschließen','Abstimmen und abschließen']):
            step,_=Step.objects.get_or_create(pk=uid('step'+str(i)),defaults={'entry':audio,'title':title,'position':i,'date':date(2026,11,i+1),'text':'Synthetischer Arbeitsschritt.\n\n**Problem:** Einbauposition prüfen.\n\n**Lösung:** Anordnung und Ergebnis dokumentiert.'});steps.append(step)
        for n in range(1,13):
            if not PhotoLink.objects.filter(pk=uid('photo'+str(n))).exists():
                asset=ingest_asset(user,SimpleUploadedFile(f'testbild-{n:02}.jpg',synthetic_photo(n),content_type='image/jpeg'));thumbnail(asset)
                PhotoLink.objects.create(pk=uid('photo'+str(n)),asset=asset,entry=audio,step=steps[(n-1)%5],position=n,caption=f'Synthetisches Testbild {n:02} · Soundsystem',role='vorher' if n==1 else 'nachher' if n==12 else '')
        for i,amount in enumerate(['500.00','450.00','300.00']):
            title=['Lautsprecher','Verstärker','Material und Einbau'][i]
            raw=synthetic_pdf(f'SYNTHETISCHER TESTBELEG\nRechnung DEMO-AUDIO-{i+1}\nRechnungsdatum 01.11.2026\n{title}: {amount} EUR')
            doc,_=receive_document(user,SimpleUploadedFile(f'audio-{i+1}.pdf',raw,content_type='application/pdf'),auto,audio)
            if doc.status!='accepted':
                doc.paperless_id=200+i;doc.verified_at=timezone.now();doc.ocr=f'{title} {amount} EUR';doc.status='review';doc.save()
                p={'invoice_number':f'DEMO-AUDIO-{i+1}','invoice_date':'2026-11-01','service_date':None,'odometer_km':None,'gross_total':amount,'net_total':None,'tax_total':None,'currency':'EUR','vendor':title,'positions':[{'description':title,'amount':amount,'part_number':''}]}
                accept_document(user,doc,p,[{'project':auto,'entry':audio,'amount':amount}],expected_version=doc.version)
            Job.objects.filter(document=doc).update(status='done')
        txt=(settings.BASE_DIR/'spezifikation/beispiele/BELEG_DEMO_OEL.txt').read_text(encoding='utf8')
        doc,_=receive_document(user,SimpleUploadedFile('DEMO-OEL-001.pdf',synthetic_pdf(txt),content_type='application/pdf'),auto,oil)
        if doc.status!='accepted':
            doc.paperless_id=100;doc.verified_at=timezone.now();doc.ocr=txt;doc.proposal=demo_proposal(txt);doc.status='review';doc.save();Job.objects.filter(document=doc).update(status='done')
        Part.objects.get_or_create(pk=uid('filter'),defaults={'project':auto,'name':'Ölfilter','number':'DEMO-FILTER-01','manufacturer':'Demo','state':'eingebaut'})
        Task.objects.get_or_create(pk=uid('task'),defaults={'project':auto,'entry':audio,'title':'Kabelverlegung nachprüfen','due':date(2026,11,20)})
        self.stdout.write('Synthetische Demo angelegt. Zugang: demo / Projektakte-Demo-2026! (nur lokal).')
