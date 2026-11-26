"""Conservative local import. PDF text is evidence, never inferred service data."""
import csv
import io
import json
from pathlib import Path
from django.core.exceptions import ValidationError
from .pdf_limits import pdf_pages


def parse_source(name, raw):
    extension = Path(name).suffix.lower()
    if extension == '.pdf':
        try:
            pages = pdf_pages(raw)
            rows = []
            length = 0
            for number, page in enumerate(pages, 1):
                text = page.strip()
                length += len(text)
                if length > 250000:
                    raise ValidationError('PDF-Text zu umfangreich; Quelle aufteilen.')
                if text:
                    rows.append({'title': f'Importseite {number} – prüfen', 'date': '', 'km': '', 'cost': '',
                                 'description': text, 'kind': 'notiz', 'source_page': number})
            if not rows:
                raise ValidationError('Kein lesbarer PDF-Text. OCR über Paperless oder geprüfte CSV/JSON verwenden.')
            return rows
        except ValidationError:
            raise
        except Exception:
            raise ValidationError('PDF konnte nicht gelesen werden. Quelle prüfen.') from None
    if extension not in ('.csv', '.json'):
        raise ValidationError('CSV, JSON oder PDF verwenden.')
    text = raw.decode('utf-8-sig')
    rows = json.loads(text) if extension == '.json' else list(csv.DictReader(io.StringIO(text), delimiter=';'))
    if not isinstance(rows, list) or not 0 < len(rows) <= 1000:
        raise ValidationError('1–1000 Datensätze erwartet.')
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('title'), str) or not row['title'].strip():
            raise ValidationError('Jede Zeile braucht title. Unklare Angaben vor Bestätigung korrigieren.')
    return rows


def parse_bmw_layout(pages):
    """Known five-column BMW history layout; unknown cells stay empty."""
    import re
    if not pages or 'Fahrzeug: BMW' not in pages[0] or not any('Details & Teilenummern' in page for page in pages):
        return None
    fields={}
    for line in pages[0].splitlines():
        if ':' not in line: continue
        key,value=line.strip().split(':',1)
        if key and value.strip(): fields[key]={'type':'text','value':value.strip()}
    rows=[]
    for page_number,page in enumerate(pages[1:],2):
        current=None
        columns=None
        for line in page.splitlines():
            if 'Arbeit / Maßnahme' in line and 'Kosten / Ort' in line:
                columns=(line.index('km-')-1,line.index('Arbeit')-1,line.index('Details')-1,line.index('Kosten')-1)
                continue
            date_match=re.match(r'^\s*(\d{2}\.\d{2}\.(?:\d{4}|\d{2}))\s+',line)
            if date_match:
                if current: rows.append(current)
                if columns is None:
                    cells=list(re.finditer(r'\S.*?(?=\s{2,}|$)',line))
                    if len(cells)==4: columns=(14,cells[1].start()-1,cells[2].start()-1,cells[3].start()-1)
                    elif len(cells)==5: columns=tuple(cell.start()-1 for cell in cells[1:])
                    else: raise ValidationError('BMW-Tabellenspalten nicht eindeutig; PDF-Seitenentwurf manuell prüfen.')
                current={'source_page':page_number,'_cells':['','','','','']}
            if current:
                if line.strip().startswith(('Bremsanlage:','Vorderachse:','Zubehör:')):
                    rows.append(current);current=None;break
                bounds=(0,*columns,len(line)+1)
                for index in range(5):
                    value=line[bounds[index]:bounds[index+1]].strip()
                    if value: current['_cells'][index]+= ('\n' if current['_cells'][index] else '')+value
        if current: rows.append(current)
    normalized=[]
    for row in rows:
        raw_date,km,title,details,cost_place=row.pop('_cells')
        raw_date=raw_date.splitlines()[0]
        day,month,year=raw_date.split('.')
        if len(year)==2: year='20'+year
        from datetime import date
        parsed=date(int(year),int(month),int(day))
        km=km.replace(' ','').strip()
        if km=='-': km=''
        if km and not re.fullmatch(r'\d{1,3}(?:\.\d{3})*|\d+',km):
            raise ValidationError('BMW-Kilometerzelle nicht eindeutig. Quelle manuell prüfen.')
        amounts=re.findall(r'(-?(?:\d{1,3}(?:\.\d{3})*|\d+)(?:,\d{1,2})?)\s*€',cost_place)
        if len(amounts)>1: raise ValidationError('BMW-Kosten nicht eindeutig; Quelle manuell prüfen.')
        amount=amounts[0] if amounts else ('0,00' if 'kostenlos' in cost_place.lower() else '')
        title=' '.join(title.split())
        snapshot={}
        if title.lower()=='ölwechsel':
            oil=re.search(r'Öl:\s*(.+?)(?=Schraube:|$)',details.replace('\n',' '))
            filtr=re.search(r'Filter\s+(.+)$',details.replace('\n',' '))
            if oil: snapshot['oil']=oil.group(1).strip()
            if filtr: snapshot['filter']=filtr.group(1).strip()
        kind='umbau' if 'soundsystem' in title.lower() else ('reparatur' if any(word in title.lower() for word in ('reparatur','fensterheber','pannen','tausch')) else 'wartung')
        normalized.append({**row,'title':title,'date':parsed.isoformat(),'km':km,'cost':amount,
            'cost_state':'free' if amount in ('0','0,00') else ('known' if amount else 'unknown'),
            'kind':kind,'description':details+('\n\nKosten / Ort laut Quelle: '+cost_place if cost_place else ''),
            'snapshot':snapshot})
    for page in pages[1:]:
        if 'Bremsanlage:' in page and 'Bremsscheiben:' in page:
            fields['Bremsdetails (Quelle, Stand unbekannt)']={'type':'multiline','value':page[page.index('Bremsanlage:'):].strip()}
    return {'rows':normalized,'project_fields':fields,'warnings':[
        'Datierte Tabellenzeilen und Stammdaten sind Vorschläge; vor Bestätigung mit Original vergleichen.',
        'Zweistellige Jahreszahlen wurden als 20xx vorbelegt.',
        'Fehlende Kosten und Kilometer bleiben unbekannt; keine Ölmenge ergänzt.',
        'Reifenangaben und Ausstattungsstand wörtlich übernommen; keine technische Richtigkeitsprüfung.',
        'Zusätzliche Bremsen-/Zubehörliste auf Seite 3 bleibt in der Quelle; keine undatierten Einbauten erzeugt.']}


def parse_source_details(name,raw):
    if Path(name).suffix.lower()=='.json':
        data=json.loads(raw.decode('utf-8-sig'))
        if isinstance(data,dict):
            rows=parse_source('rows.json',json.dumps(data.get('rows')).encode())
            fields=data.get('project_fields',{})
            if not isinstance(fields,dict) or len(fields)>100: raise ValidationError('Import-Stammdaten müssen höchstens 100 benannte Felder enthalten.')
            for key,item in fields.items():
                if not isinstance(key,str) or len(key)>100 or not isinstance(item,dict) or item.get('type') not in ('text','multiline','number','date','choice','boolean','link'):
                    raise ValidationError('Ungültige Stammdaten-Felddefinition im Import.')
            warnings=data.get('warnings',[])
            if not isinstance(warnings,list) or len(warnings)>30 or any(not isinstance(w,str) or len(w)>1000 for w in warnings): raise ValidationError('Ungültige Importhinweise.')
            return {'rows':rows,'project_fields':fields,'warnings':warnings}
    if Path(name).suffix.lower()=='.pdf':
        try:
            pages=pdf_pages(raw,layout=True)
            result=parse_bmw_layout(pages)
            if result:return result
        except ValidationError: raise
        except Exception: pass
    return {'rows':parse_source(name,raw),'project_fields':{},'warnings':[]}
