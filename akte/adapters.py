"""Test adapters and network boundaries. Live contracts require an installation test."""
import json, re, base64
from typing import Protocol
import httpx
from django.conf import settings
from django.core.exceptions import ValidationError
from .pdf_limits import pdf_pages
from .http_limits import bounded_request, checked_base, read_bounded
from .services import validate_proposal
from .proposal_schema import empty_entry, normalize_interpretations, response_schema, system_instruction, validate_metadata, validate_ai_content

class IntegrationError(Exception): pass
class AIOutcomeUncertain(IntegrationError): pass
class UploadUncertain(IntegrationError): pass
class UploadRejected(IntegrationError): pass
class Pending(IntegrationError): pass
class DocumentMissing(IntegrationError): pass
class TaskAccessDenied(IntegrationError): pass

class AIRequestRejected(IntegrationError):
    """An explicit HTTP refusal; no generated result and no blind billing retries."""
    def __init__(self,message,status_code):
        super().__init__(message)
        self.status_code=status_code
        self.retryable=status_code==429

class AIResponseInvalid(IntegrationError):
    def __init__(self,message,usage):
        super().__init__(message)
        self.usage=usage if isinstance(usage,dict) else {}

class AIAdapter(Protocol):
    def extract(self,text:str,*,original:bytes|None=None,mime_type:str|None=None,context=None) -> tuple[dict,dict]: ...

class DemoPaperless:
    def upload(self,doc): return 'demo-'+str(doc.pk)
    def poll(self,doc): return int(doc.id.int % 2_000_000_000)+1
    def read(self,document_id,doc=None):
        if doc and doc.asset:
            with doc.asset.file.open('rb') as f:
                if doc.asset.mime=='application/pdf': text='\n'.join(pdf_pages(f.read(settings.MAX_DOCUMENT_BYTES+1)))
                else: text='Synthetisches Bild. Testadapter ohne OCR. Bitte manuell prüfen.'
        else: text='Vorhandenes synthetisches Dokument. Bitte manuell prüfen.'
        return {'id':document_id,'content':text,'title':f'Demo-Dokument {document_id}'}
    def reconcile(self,doc,*,pending_if_missing=False): return self.poll(doc)
    def search(self,query): return [{'id':101,'title':'Synthetisches Bestandsdokument'}]
    def original(self,document_id,max_bytes=None):
        from .demo import synthetic_pdf
        raw=synthetic_pdf(f'Synthetisches Bestandsdokument {document_id}')
        if max_bytes and len(raw)>max_bytes: raise IntegrationError('Original überschreitet die KI-Dateigrenze.')
        return raw

class LivePaperless:
    def __init__(self):
        if not settings.PAPERLESS_URL or not settings.PAPERLESS_TOKEN: raise IntegrationError('Paperless-Zugang nicht eingerichtet.')
        self._base_url=checked_base(settings.PAPERLESS_URL)+'/'
        self._headers={'Authorization':'Token '+settings.PAPERLESS_TOKEN,'Accept':'application/json; version='+settings.PAPERLESS_API_VERSION,'Accept-Encoding':'identity'}
    def _client(self):
        return httpx.Client(base_url=self._base_url,headers=self._headers,timeout=30,follow_redirects=False,trust_env=False)
    def request(self,method,path,**kwargs):
        try:
            with self._client() as client:
                r=bounded_request(client,method,path,**kwargs)
            if path=='api/tasks/' and r.status_code in (401,403): raise TaskAccessDenied('Paperless-Auftragsliste für dieses Konto gesperrt.')
            if r.status_code==404: raise DocumentMissing('Paperless-Dokument nicht vorhanden oder nicht zugänglich.')
            r.raise_for_status();return r
        except (httpx.HTTPError,ValueError): raise IntegrationError('Paperless nicht erreichbar oder Zugriff verweigert. Gespeicherte Datei bleibt erhalten.') from None
    def upload(self,doc):
        # Filename carries a deterministic submission identity for reconciliation.
        from pathlib import Path
        name=f'projektakte-{doc.submission_key}'+Path(doc.asset.file.name).suffix
        try:
            with self._client() as client, doc.asset.file.open('rb') as f:
                r=bounded_request(client,'POST','api/documents/post_document/',data={'title':f'Projektakte {doc.submission_key}'},files={'document':(name,f,doc.asset.mime)})
            r.raise_for_status();task=r.json()
            if not isinstance(task,str) or not re.fullmatch('[0-9a-fA-F-]{36}',task): raise UploadUncertain('Unerwartete Uploadantwort. Zuerst in Paperless abgleichen.')
            return task
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401,403): raise UploadRejected('Paperless hat den Upload vor Annahme wegen fehlender Rechte abgelehnt. Zugang korrigieren und erneut versuchen.') from None
            if e.response.status_code>=500: raise UploadUncertain('Uploadantwort unklar. Kein automatisches erneutes Einreichen.') from None
            raise IntegrationError('Paperless hat den Upload abgelehnt. Rechte und Dateiformat prüfen.') from None
        except (httpx.HTTPError,ValueError): raise UploadUncertain('Verbindung während Upload unklar. Zuerst abgleichen; nicht blind erneut hochladen.') from None
    def poll(self,doc):
        try:
            tasks=self.request('GET','api/tasks/',params={'task_id':doc.task_id}).json()
        except TaskAccessDenied:
            # Do not grant archive-wide task visibility just to resolve our upload.
            records=self.request('GET','api/documents/',params={'title__icontains':str(doc.submission_key),'page_size':10}).json().get('results',[])
            matches=[item for item in records if str(doc.submission_key) in item.get('title','') and not item.get('deleted_at')]
            if len(matches)==1:
                return int(matches[0]['id'])
            if len(matches)>1:
                raise UploadUncertain('Mehrere Belege zur Uploadkennung gefunden. Manuell abgleichen; kein neuer Upload.')
            raise Pending('Eigener Beleg noch nicht sichtbar. OCR abwarten; kein erneuter Upload.')
        if isinstance(tasks,dict): tasks=tasks.get('results',[])
        task=next((t for t in tasks if t.get('task_id')==doc.task_id),None)
        if not task: raise Pending('Auftrag in Paperless noch nicht sichtbar; Abgleich erforderlich, falls dauerhaft fehlend.')
        if task.get('status')=='SUCCESS':
            related=task.get('related_document')
            if related: return int(related)
            raise UploadUncertain('Paperless meldet Erfolg ohne Dokument-ID. Manuell abgleichen.')
        if task.get('status')=='FAILURE': raise UploadUncertain('Paperless-Verarbeitung fehlgeschlagen; mögliches vorhandenes Dokument zuerst abgleichen.')
        raise Pending('Paperless verarbeitet das Dokument.')
    def read(self,document_id,doc=None): return self.request('GET',f'api/documents/{int(document_id)}/').json()
    def search(self,query): return self.request('GET','api/documents/',params={'query':query,'page_size':30}).json().get('results',[])
    def reconcile(self,doc,*,pending_if_missing=False):
        if doc.task_id: return self.poll(doc)
        records=self.request('GET','api/documents/',params={'title__icontains':str(doc.submission_key),'page_size':10}).json().get('results',[])
        matches=[r for r in records if str(doc.submission_key) in r.get('title','')]
        if len(matches)==1: return int(matches[0]['id'])
        if not matches and pending_if_missing: raise Pending('Vorheriger Upload wird noch in Paperless verarbeitet. Kein erneutes Einreichen.')
        raise UploadUncertain('Kein eindeutiger Treffer. In Paperless prüfen und vorhandene Dokument-ID auswählen. Automatischer Neu-Upload bleibt gesperrt.')
    def original_sha256(self,document_id):
        import hashlib
        from django.conf import settings
        digest=hashlib.sha256();size=0
        try:
            with self._client() as client, client.stream('GET',f'api/documents/{int(document_id)}/download/',params={'original':'true'}) as response:
                response.raise_for_status()
                if response.headers.get('Content-Encoding','identity').casefold() not in ('','identity'):raise IntegrationError('Komprimiertes Archivoriginal abgelehnt.')
                length=response.headers.get('Content-Length','')
                if length and (not length.isdecimal() or int(length)>settings.MAX_DOCUMENT_BYTES):raise IntegrationError('Archivoriginal zu groß.')
                for chunk in response.iter_bytes(chunk_size=65536):
                    size+=len(chunk)
                    if size>settings.MAX_DOCUMENT_BYTES:
                        raise IntegrationError('Archivoriginal überschreitet die Dateigrenze. Manuell abgleichen.')
                    digest.update(chunk)
            return digest.hexdigest()
        except httpx.HTTPError:
            raise IntegrationError('Archivoriginal konnte nicht geprüft werden. Kein erneuter Upload.') from None

    def original(self,document_id,max_bytes=None):
        if max_bytes is None: max_bytes=settings.MAX_DOCUMENT_BYTES
        try:
            with self._client() as client, client.stream('GET',f'api/documents/{int(document_id)}/download/',params={'original':'true'}) as r:
                r.raise_for_status()
                return read_bounded(r,max_bytes)
        except (httpx.HTTPError,ValueError): raise IntegrationError('Belegoriginal derzeit nicht erreichbar. OCR und manuelle Prüfung bleiben verfügbar.') from None

def paperless(doc=None):
    from .demo_boundary import deny_live_operation
    deny_live_operation()
    expected='demo' if settings.DEMO_MODE else settings.PAPERLESS_URL
    if doc is not None and doc.instance!=expected: raise IntegrationError('Dieser Beleg gehört zu einer anderen Paperless-Instanz. Instanzkonfiguration und Zuordnung zuerst prüfen.')
    return DemoPaperless() if settings.DEMO_MODE else LivePaperless()

def demo_proposal(text):
    p={'invoice_number':None,'invoice_date':None,'service_date':None,'odometer_km':None,'gross_total':None,'net_total':None,'tax_total':None,'currency':'EUR','positions':[],'vendor':None}
    if 'DEMO-OEL-001' in text:
        p.update(invoice_number='DEMO-OEL-001',invoice_date='2026-11-10',gross_total='75.00',net_total='63.03',tax_total='11.97',vendor='Musterteile Demo',positions=[{'description':'Motoröl Demo 5W-40, 5 Liter','amount':'40.00','part_number':''},{'description':'Ölfilter','part_number':'DEMO-FILTER-01','amount':'20.00'},{'description':'Versand','part_number':'','amount':'15.00'}])
    elif 'DEMO-SPLIT-001' in text:
        p.update(invoice_number='DEMO-SPLIT-001',invoice_date='2026-11-09',gross_total='105.00',vendor='Hobbybedarf Demo',positions=[{'description':'Auto-Demoteil','part_number':'DEMO-AUTO-01','amount':'60.00'},{'description':'Aquarium-Demoteil','part_number':'DEMO-AQUA-01','amount':'40.00'},{'description':'Versand','part_number':'','amount':'10.00'},{'description':'Rabatt','part_number':'','amount':'-5.00'}])
    p.update(entry=empty_entry(),inferred_fields=[],project_assignment={'project_id':None,'confidence':'none','reason':None})
    if 'DEMO-OEL-001' in text:
        p['entry'].update(title='Öl und Filter gekauft',kind='notiz',description='Motoröl und Ölfilter laut Rechnung. Einbau und Arbeitsdatum nicht belegt.')
        p['inferred_fields']=['entry.title','entry.kind','entry.description']
    return p

SCHEMA=response_schema()

class DemoAI:
    def extract(self,text,*,original=None,mime_type=None,context=None): return demo_proposal(text),{'promptTokenCount':0,'candidatesTokenCount':0}

def original_for_ai(doc):
    limit=settings.GEMINI_ORIGINAL_MAX_BYTES
    if doc.asset_id:
        with doc.asset.file.open('rb') as f: raw=f.read(limit+1)
    elif doc.paperless_id: raw=paperless(doc).original(doc.paperless_id,max_bytes=limit)
    else: raise IntegrationError('Kein bestätigtes Original für die zusätzliche KI-Auswertung verfügbar.')
    if len(raw)>limit: raise IntegrationError(f'Original zu groß für direkte KI-Auswertung (Grenze {limit//1024//1024} MB). Bitte ausdrücklich nur OCR wählen oder eine kleinere Datei verwenden.')
    if raw.startswith(b'%PDF-'): mime='application/pdf'
    elif raw.startswith(b'\xff\xd8\xff'): mime='image/jpeg'
    elif raw.startswith(b'\x89PNG'): mime='image/png'
    elif raw[:4]==b'RIFF' and raw[8:12]==b'WEBP': mime='image/webp'
    else: raise IntegrationError('Originalformat für KI nicht unterstützt. JPEG, PNG, WebP oder PDF erforderlich.')
    return raw,mime

class GeminiAI:
    def __init__(self,model=None):
        self.model=model or settings.GEMINI_MODEL
        if not re.fullmatch(r'[A-Za-z0-9._-]{1,100}',self.model):raise IntegrationError('Ungültige serverseitige Modellkennung.')
    def extract(self,text,*,original=None,mime_type=None,context=None,voice=False,capture=False,attachments=None,repair=False):
        from .integration_secrets import gemini_key
        if not gemini_key(): raise IntegrationError('Gemini-Schlüssel fehlt. Manuelle Prüfung bleibt möglich.')
        if original is not None and (len(original)>settings.GEMINI_ORIGINAL_MAX_BYTES or mime_type not in (('audio/webm','audio/ogg','audio/wav','audio/mpeg','audio/flac','audio/m4a') if voice else ('application/pdf','image/jpeg','image/png','image/webp'))):
            raise IntegrationError('Original überschreitet die KI-Dateigrenze oder besitzt ein ungültiges Format.')
        parts=[{'text':text[:60000]}]
        if original is not None: parts.append({'inlineData':{'mimeType':mime_type,'data':base64.b64encode(original).decode('ascii')}})
        if capture:
            from .capture_ai import MAX_CAPTURE_BYTES
            attachments=attachments or []
            allowed=('application/pdf','image/jpeg','image/png','image/webp','audio/webm','audio/ogg','audio/wav','audio/mpeg','audio/flac','audio/m4a')
            if len(attachments)>9 or sum(len(item['data']) for item in attachments)>MAX_CAPTURE_BYTES or any(item['mime'] not in allowed or len(item['data'])>settings.GEMINI_ORIGINAL_MAX_BYTES for item in attachments):
                raise IntegrationError('Erfassung überschreitet die KI-Dateigrenze oder enthält ein ungültiges Format.')
            for index,item in enumerate(attachments):
                parts.append({'text':f"Dateiindex {index}: {item['name']}"})
                parts.append({'inlineData':{'mimeType':item['mime'],'data':base64.b64encode(item['data']).decode('ascii')}})
        context=context or {}
        projects=context.get('routing_projects',[]) if context.get('routing_enabled') else []
        schema=response_schema(projects,context)
        system=system_instruction(context)
        if voice:
            from .voice import voice_schema, voice_instruction
            schema=voice_schema(projects,context);system=voice_instruction(context)
        if capture:
            from .capture_ai import capture_schema,capture_instruction
            schema=capture_schema(context);system=capture_instruction(context)
        if repair:
            from .repair_advice import advice_schema, advice_instruction
            schema=advice_schema(context);system=advice_instruction()
        from django.utils import translation
        from .i18n import normalize_language
        resp_lang = normalize_language(context.get('response_language')) or normalize_language(translation.get_language()) or 'de'
        if resp_lang == 'en':
            system = (
                system
                + ' OUTPUT LANGUAGE: Write all natural-language text values (entry.title, entry.description, reason, knowledge titles/contents, advice) '
                + 'in natural English. Keep all JSON keys and enum values (kind, status, category, confidence, price_basis) strictly unchanged as defined by the JSON schema. '
                + 'Keep proper names, vendor names, part numbers, and literal source_quote excerpts in their original language.'
            )
        else:
            system = (
                system
                + ' AUSGABESPRACHE: Formuliere alle Freitextwerte (entry.title, entry.description, reason, Wissensvorschläge, Hinweise) auf natürlichem Deutsch. '
                + 'Alle JSON-Schlüssel und Enum-Werte bleiben strikt unverändert gemäß Schema.'
            )
        request_timeout=httpx.Timeout(150,connect=10,write=30,pool=10) if self.model=='gemini-3.8-flash' else (httpx.Timeout(90,connect=10,write=30,pool=10) if capture else 45)
        try:
            with httpx.Client(follow_redirects=False,trust_env=False) as client:
                r=bounded_request(client,'POST',f'{checked_base(settings.GEMINI_API_BASE,https=True)}/models/{self.model}:generateContent',headers={'x-goog-api-key':gemini_key(),'Accept-Encoding':'identity'},json={'systemInstruction':{'parts':[{'text':system}]},'contents':[{'role':'user','parts':parts}],'generationConfig':{'responseMimeType':'application/json','responseJsonSchema':schema,'maxOutputTokens':settings.AI_MAX_OUTPUT,'temperature':0,'thinkingConfig':{'thinkingLevel':'low' if self.model=='gemini-3.8-flash' else 'minimal'}}},timeout=request_timeout)
        except httpx.TimeoutException:
            if self.model=='gemini-3.8-flash':
                raise AIOutcomeUncertain('Komplexes Gemini-Modell hat das Zeitlimit von 150 Sekunden überschritten. Eingaben bleiben gespeichert; wegen unklarer API-Kosten keine automatische Wiederholung. Später ausdrücklich erneut versuchen oder Alltagsmodell wählen.') from None
            if capture:
                raise IntegrationError('Gemini hat nicht innerhalb des Zeitlimits geantwortet (Leselimit 90 Sekunden). Eingaben und Dateien bleiben gespeichert; später erneut versuchen.') from None
            raise IntegrationError('Gemini hat nicht innerhalb von 45 Sekunden geantwortet. OCR bleibt für die Prüfung erhalten; später erneut versuchen.') from None
        except (httpx.HTTPError,ValueError):
            raise IntegrationError('Verbindung zu Gemini fehlgeschlagen. OCR bleibt für die manuelle Prüfung verfügbar.') from None
        rejections={
            400:'Gemini hat die Anfrage abgelehnt (HTTP 400). Modell und KI-Schema prüfen.',
            401:'Gemini-Schlüssel wird nicht akzeptiert (HTTP 401). Serverzugang prüfen.',
            402:'Google blockiert die KI-Auswertung: Vorauszahlungsguthaben fehlt (HTTP 402). Google-Abrechnung für Projektakte prüfen. Keine automatische Wiederholung; OCR und manuelle Prüfung bleiben verfügbar.',
            403:'Google verweigert die KI-Auswertung (HTTP 403). Schlüsselberechtigung und Google-Projekt prüfen.',
            404:'Das konfigurierte Gemini-Modell ist nicht verfügbar (HTTP 404). Modellkonfiguration prüfen.',
            429:'Google-Kontingent oder Ratenlimit erreicht (HTTP 429). Später erneut versuchen; dies ist nicht das App-Monatsbudget.',
        }
        if r.status_code in rejections:raise AIRequestRejected(rejections[r.status_code],r.status_code)
        if r.status_code!=200:
            raise IntegrationError(f'Gemini derzeit nicht verfügbar (HTTP {r.status_code}). Später erneut versuchen; manuelle Prüfung bleibt möglich.')
        usage={}
        try:
            data=r.json();usage=data.get('usageMetadata',{})
            candidate=data['candidates'][0]
            if candidate.get('finishReason')=='MAX_TOKENS':
                raise AIResponseInvalid('KI-Antwort wegen Ausgabelimit unvollständig. Beleg manuell prüfen oder Auswertung mit kleinerem Umfang wiederholen.',usage)
            parts=candidate['content']['parts']
            text=''.join(part.get('text','') for part in parts if not part.get('thought'))
            p=json.loads(text)
            validate_ai_content(p)
            if repair:
                from .repair_advice import validate_advice
                return validate_advice(p,context),usage
            from .proposal_schema import canonicalize_ai_proposal
            canonicalize_ai_proposal(p.get('proposal') if capture or voice else p,context)
            if capture:
                from .capture_ai import validate_capture
                return validate_capture(p,context),data.get('usageMetadata',{})
            if voice:
                from .voice import validate_voice
                validate_voice(p,projects)
                from .definitions import validate_ai_definition
                validate_ai_definition(p['proposal'],context)
                p['proposal']=normalize_interpretations(p['proposal'])
                return p,data.get('usageMetadata',{})
            validate_proposal(p,check_totals=False);validate_metadata(p,ai_response=True)
            from .definitions import validate_ai_definition
            validate_ai_definition(p,context)
            if p['project_assignment']['project_id'] not in {None,*[item['id'] for item in projects]}:
                raise ValidationError('KI hat ein nicht freigegebenes Projekt vorgeschlagen.')
            return normalize_interpretations(p),data.get('usageMetadata',{})
        except ValidationError as exc:
            raise AIResponseInvalid('KI-Antwort muss korrigiert werden: '+'; '.join(exc.messages),usage) from None
        except (KeyError,ValueError,IndexError,TypeError,AttributeError,RecursionError):
            raise AIResponseInvalid('Gemini hat keine gültigen strukturierten Prüfdaten geliefert. OCR bleibt erhalten; manuell prüfen oder erneut auswerten.',usage) from None

def ai(model=None):
    from .demo_boundary import deny_live_operation
    deny_live_operation()
    return DemoAI() if settings.DEMO_MODE else GeminiAI(model)
