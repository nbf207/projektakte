"""Owner-bound, read-only original repair documents. No source scripts are executed."""
import json
import re
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from html import escape
from xml.etree import ElementTree as ET
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_safe
from .models import RepairLibrary
from .private_files import private_path

KINDS={'repair':'Reparaturanleitungen','torque':'Drehmomente','technical':'Technische Daten','description':'Funktionsbeschreibungen','tool':'Spezialwerkzeuge','diagnostic':'Diagnoseunterlagen','service':'Serviceinformationen','other':'Weitere Unterlagen'}
CATEGORIES={'engine':'Motor & Aufladung','service':'Wartung & Betriebsstoffe','cooling':'Kühlung, Kraftstoff & Abgas','drive':'Kupplung & Antrieb','chassis':'Fahrwerk, Lenkung & Räder','brakes':'Bremsen & Pedale','body':'Karosserie & Innenraum','electric':'Elektrik & Komfort','other':'Weitere Unterlagen'}
ALIASES={'tauschen':['ersetzen','erneuern','ausbauen','austausch','wechsel'],'wechseln':['ersetzen','erneuern','ausbauen','wechsel'],'tausch':['ersetzen','erneuern','ausbauen','austausch','wechsel'],'wechsel':['ersetzen','erneuern','ausbauen','wechsel'],'turbo':['turbolader','abgasturbolader'],'turbolader':['turbolader','abgasturbolader'],'bremsen':['brems'],'olwechsel':['motorolservice','motorolwechsel','olwechsel'],'bremsflussigkeit':['bremsflussigkeit','bremsfluessigkeit','bremssystem'],'handbremse':['handbremse','feststellbremse'],'steuerkette':['steuerkette','steuerketten','kettentrieb'],'gluhkerzen':['gluhstift','gluhkerz'],'dpf':['dieselpartikelfilter','partikelfilter'],'wasserpumpe':['kuhlmittelpumpe','wasserpumpe'],'stossdampfer':['stossdampfer','stoßdämpfer','federbein'],'bremsbelag':['bremsbelag','bremsbelage']}

def search_expression(query):
    query=''.join(c for c in unicodedata.normalize('NFD',query.lower()) if not unicodedata.combining(c)).replace('ß','ss')
    query=re.sub(r'(turbo(?:lader)?)(tausch|wechsel)',r'\1 tausch',query)
    stop={'ich','mochte','den','die','das','ein','eine','der','des','dem','und','fur','wie','mein','meinem','am','im'}
    query=re.sub(r'([^\W_]+?)(wechsel|tausch)\b',r'\1 tausch',query) if not any(w in query for w in ('olwechsel','oelwechsel')) else query
    words=[w for w in re.findall(r'[^\W_]+',query) if w not in stop][:10]
    return ' AND '.join('('+' OR '.join('"'+x+'"*' for x in ALIASES.get(w,[w]))+')' for w in words)

def library_for(request,pk):
    return get_object_or_404(RepairLibrary,pk=pk,owner=request.user,project__owner=request.user)

@contextmanager
def database(library):
    path=private_path('media',f'reference/{library.pk.hex}/reparatur-index.sqlite')
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON');db.execute('PRAGMA trusted_schema=OFF');db.execute('PRAGMA cache_size=-8192')
    deadline=time.monotonic()+4
    db.set_progress_handler(lambda: int(time.monotonic()>deadline),10000)
    try:yield db
    finally:db.close()

def resource(db,identity):
    if not re.fullmatch(r'[0-9]{1,24}',str(identity)):return None
    row=db.execute('select * from resources where id=?',(identity,)).fetchone()
    return dict(row) if row else None

def resource_path(library,row):
    from pathlib import PurePosixPath
    name=PurePosixPath(row['file'])
    if name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0] not in ('Original_XML','Anhaenge') or chr(92) in row['file']:raise Http404()
    return private_path('media',f'reference/{library.pk.hex}/'+row['file'])

def resource_url(library,identity,download=False):
    return reverse('repair_file' if download else 'repair_resource',args=[library.pk,identity])

@login_required
@require_safe
def catalogue(request,pk):
    library=library_for(request,pk)
    query=request.GET.get('q','')[:200];kind=request.GET.get('kind','repair');category=request.GET.get('category','')
    scope=request.GET.get('scope','profile');page=max(1,min(int(request.GET.get('page','1')) if request.GET.get('page','1').isdigit() else 1,667))
    where=['d.has_content=1'];args=[];error=''
    if scope!='all':where.append('d.profile=1')
    if kind in KINDS:where.append('d.kind=?');args.append(kind)
    if category in CATEGORIES:where.append('d.category=?');args.append(category)
    expression=search_expression(query);source='docs d';order='d.title';snippet="'' snippet"
    if expression:
        source='doc_search JOIN docs d ON d.rowid=doc_search.rowid';where.append('doc_search MATCH ?');args.append(expression)
        order='d.profile DESC,bm25(doc_search,12.0,1.0,6.0)';snippet="snippet(doc_search,1,'','',' … ',28) snippet"
    rows=[];total=0
    try:
        with database(library) as db:
            sql=source+' WHERE '+' AND '.join(where)
            total=db.execute('SELECT count(*) FROM '+sql,args).fetchone()[0]
            rows=[dict(row) for row in db.execute('SELECT d.id,d.title,d.work_code,d.engine,d.profile,d.type_label,'+snippet+' FROM '+sql+' ORDER BY '+order+' LIMIT 30 OFFSET ?',[*args,(page-1)*30])]
    except sqlite3.Error:error='Suche zu umfangreich. Bitte mit einem Bauteilnamen eingrenzen.'
    contributions = library.contributions.filter(owner=request.user, project=library.project)
    for token in query.split()[:12]:
        contributions = contributions.filter(Q(title__icontains=token) | Q(content__icontains=token) | Q(subject__icontains=token) | Q(source_url__icontains=token))
    contributions = contributions.exclude(state='rejected').order_by('-updated')[:12]
    return render(request,'repair_library.html',{'library':library,'project':library.project,'rows':rows,'contributions':contributions,'total':total,'q':query,'kind':kind,'category':category,'scope':scope,'kinds':KINDS.items(),'categories':CATEGORIES.items(),'page':page,'previous':page-1 if page>1 else None,'next':page+1 if page*30<total else None,'error':error})

class OriginalRenderer:
    """Render a fixed subset; never trust source markup, attributes, URLs or scripts."""
    def __init__(self,db,library,source):
        self.db=db;self.library=library;self.source=source;self.nodes=0
        self.is_repair=False;self.tables=0;self.sections=[]
        self.links={}
        for row in db.execute('select link_key,target from links where source=?',(source,)):
            self.links.setdefault(row['link_key'],[]).append(row['target'])
    def targets(self,node):
        ids=self.links.get(node.get('LINKID'),[])[:30]
        name=node.get('SRC','')
        if not ids and name:
            names=[name,re.sub(r'\.[^.]+$','',name),re.sub(r'\.[^.]+$','',name).removeprefix('--').upper()]
            for alias in names:
                ids=[r[0] for r in self.db.execute('select resource_id from aliases where name=? limit 30',(alias,))]
                if ids:break
        if not ids:
            ids=[v for v in node.attrib.values() if re.fullmatch(r'\d{10,24}',v)]
        return [r for identity in dict.fromkeys(ids) if (r:=resource(self.db,identity))]
    def children(self,node,depth):
        return escape(node.text or '')+''.join(self.render(child,depth+1)+escape(child.tail or '') for child in node)
    def render(self,node,depth=0):
        self.nodes+=1
        if depth>80 or self.nodes>30000:raise ValueError('Document complexity exceeds viewer limit')
        tag=node.tag.rsplit('}',1)[-1].upper()
        if tag in ('SCRIPT','STYLE','COLSPEC','SPANSPEC'):return ''
        if tag=='TABLE':return self.table(node,depth)
        if tag=='BR':return '<br>'
        targets=self.targets(node) if tag in ('GRAPHIC','IMAGE','HOTSPOT','REFERENCE','VIDEO','AUDIO') or node.get('LINKID') else []
        if tag in ('GRAPHIC','IMAGE'):
            images=[r for r in targets if r['format'] in ('png','jpg','jpeg','gif')]
            if images:
                row=images[0];url=escape(resource_url(self.library,row['id'],True),quote=True)
                small=name_is_symbol(node.get('SRC',''))
                if small:return f'<img class="repair-symbol" src="{url}" alt="Hinweissymbol" loading="lazy">'
                return f'<figure><a href="{url}" target="_blank" rel="noopener"><img src="{url}" alt="Originalabbildung" loading="lazy"></a><figcaption>Originalabbildung · zum Vergrößern öffnen</figcaption></figure>'
            return '<p class="notice">Abbildung nicht aufgelöst: '+escape(node.get('SRC',''))+'</p>'
        section=None
        if tag=='OPERATINGSTEP' and self.is_repair and not self.tables:
            heading=node.find('HINT/TITLE')
            caption=' '.join(heading.itertext()).strip() if heading is not None else 'Arbeitsschritt'
            section={'number':len(self.sections)+1,'title':caption}
            self.sections.append(section)
        body=self.children(node,depth)
        if section:body='<p class="repair-section-label">Abschnitt '+str(section['number'])+'</p>'+body
        if targets and node.get('LINKID') and tag!='ILLUSTRATION':
            label=' '.join(node.itertext()).strip() or 'Verknüpfte Unterlage'
            links=''.join('<li><a href="'+escape(resource_url(self.library,r['id']),quote=True)+'">'+escape(r['title'] or label)+' · '+escape(r['format'])+'</a></li>' for r in targets)
            address=re.search(r'\b[0-9]+AZ\b',label.upper())
            fragment='#address-'+address.group(0) if address else ''
            if len(targets)==1:return '<a class="repair-reference" href="'+escape(resource_url(self.library,targets[0]['id'])+fragment,quote=True)+'">'+escape(label)+'</a>'
            return '<details class="repair-reference"><summary>'+escape(label)+' · Varianten</summary><ul>'+links+'</ul></details>'
        if node.get('LINKID') and not targets and tag in ('HOTSPOT','REFERENCE'):
            return body+' <small>(Verweis im Export nicht aufgelöst)</small>'
        tags={'HEADING':'h2','SUBJECT':'h2','TITLE':'h3','SUBGROUPTITLE':'h3','PROCESSTITLE':'h3','PARAGRAPH':'p','LISTENTRY':'li','LIST':'ol' if node.get('TYPE')=='NUMBER' else 'ul','EMPHASIZE':'strong','EMPHASIZE2':'strong','HINT':'aside','OPERATINGSTEP':'section','ADDRESS':'strong','TORQUE':'span','MEASURE':'span','UNIT':'span','SERIES':'span','ENGINE':'span'}
        htmltag=tags.get(tag,'div' if len(node) else 'span')
        attrs=''
        if tag=='OPERATINGSTEP':attrs=' class="repair-step"'+(' id="step-'+str(section['number'])+'"' if section else '')
        if tag=='HINT':attrs=' class="notice repair-hint"'
        if tag in ('SERIES','ENGINE','UNIT'):body+=' '
        if tag=='ADDRESS':attrs=' id="address-'+re.sub('[^A-Za-z0-9_-]','',''.join(node.itertext()))+'"'
        return '<'+htmltag+attrs+'>'+body+'</'+htmltag+'>'
    def table(self,node,depth):
        self.tables+=1
        try:return self._table(node,depth)
        finally:self.tables-=1
    def _table(self,node,depth):
        group=node.find('TGROUP')
        if group is None:group=node
        cols=[c.get('COLNAME') for c in group.findall('COLSPEC')]
        spans={c.get('SPANNAME'):c for c in group.findall('SPANSPEC')}
        sections=[c for c in group if c.tag in ('THEAD','TBODY','TFOOT')]
        if not sections:sections=[group]
        output=['<div class="repair-table"><table>']
        for section in sections:
            sectiontag={'THEAD':'thead','TFOOT':'tfoot'}.get(section.tag,'tbody');output.append('<'+sectiontag+'>');occupied=[0]*max(1,len(cols))
            for row in section.findall('ROW'):
                output.append('<tr>');cursor=0
                for cell in row.findall('ENTRY'):
                    span=spans.get(cell.get('SPANNAME'));attrs=span.attrib if span is not None else {}
                    first=cell.get('NAMEST') or attrs.get('NAMEST') or cell.get('COLNAME');last=cell.get('NAMEEND') or attrs.get('NAMEEND')
                    desired=cols.index(first) if first in cols else cursor
                    while cursor<max(cursor,desired):
                        if cursor>=len(occupied) or not occupied[cursor]:output.append('<td></td>')
                        cursor+=1
                    while cursor<len(occupied) and occupied[cursor]:cursor+=1
                    end=cols.index(last) if last in cols else cursor
                    colspan=max(1,min(30,end-cursor+1));more=cell.get('MOREROWS','0')
                    rowspan=min(100,int(more)+1) if more.isdigit() else 1
                    celltag='th' if section.tag=='THEAD' else 'td'
                    output.append(f'<{celltag} colspan="{colspan}" rowspan="{rowspan}">'+self.children(cell,depth)+f'</{celltag}>')
                    while len(occupied)<cursor+colspan:occupied.append(0)
                    for i in range(cursor,cursor+colspan):occupied[i]=rowspan
                    cursor+=colspan
                while cursor<len(occupied):
                    if not occupied[cursor]:output.append('<td></td>')
                    cursor+=1
                occupied=[max(0,n-1) for n in occupied];output.append('</tr>')
            output.append('</'+sectiontag+'>')
        return ''.join(output)+'</table></div>'

def name_is_symbol(name):return bool(re.match(r'GRA-SYM-|(?:--)?GRCI',name,re.I))

@login_required
@require_safe
def document(request,pk,identity):
    library=library_for(request,pk)
    with database(library) as db:
        doc=db.execute('select * from docs where id=?',(identity,)).fetchone()
        if not doc:raise Http404()
        row=resource(db,doc['main_id'])
        if not row:raise Http404()
        return show_document(request,db,library,row,dict(doc))

@login_required
@require_safe
def resource_detail(request,pk,identity):
    library=library_for(request,pk)
    with database(library) as db:
        row=resource(db,identity)
        if not row:raise Http404()
        doc=db.execute('select d.* from docs d join doc_files f on d.id=f.doc_id where f.resource_id=? order by d.profile desc limit 1',(identity,)).fetchone()
        return show_document(request,db,library,row,dict(doc) if doc else None)

def show_document(request,db,library,row,doc):
    body='';error='';sections=[]
    if row['format']=='xml':
        path=resource_path(library,row)
        if path.stat().st_size>8*1024*1024:error='Original ist für die Leseansicht zu umfangreich. Bitte herunterladen.'
        else:
            raw=path.read_bytes()
            if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():error='XML mit Entitätsdefinitionen wird nur als Original angeboten.'
            else:
                try:
                    tree=ET.fromstring(raw);renderer=OriginalRenderer(db,library,row['id'])
                    renderer.is_repair=tree.tag=='REPAIRMANUALDOCUMENT'
                    body=renderer.render(tree);sections=renderer.sections
                except (ET.ParseError,ValueError,RecursionError,sqlite3.Error):error='Leseansicht nicht möglich. Originaldatei bleibt verfügbar.'
    variants=[]
    if doc and not error:
        for item in json.loads(doc['files']):
            if str(item[2])!=str(row['id']) and resource(db,str(item[2])):
                variants.append({'url':resource_url(library,str(item[2])),'format':item[1],'language':item[3] if len(item)>3 else ''})
    contributions = library.contributions.filter(
        owner=request.user, project=library.project, repair_document_id=doc['id'],
    ).exclude(state='rejected').order_by('-updated') if doc else ()
    return render(request,'repair_document.html',{'library':library,'project':library.project,'resource':row,'doc':doc,'contributions':contributions,'body':mark_safe(body),'error':error,'sections':sections,'variants':variants,'file_url':resource_url(library,row['id'],True)})

@login_required
@require_safe
def file_download(request,pk,identity):
    library=library_for(request,pk)
    with database(library) as db:row=resource(db,identity)
    if not row:raise Http404()
    path=resource_path(library,row)
    mime={'png':'image/png','jpg':'image/jpeg','jpeg':'image/jpeg','gif':'image/gif','pdf':'application/pdf','mp4':'video/mp4','mp3':'audio/mpeg'}.get(row['format'],'application/octet-stream')
    response=FileResponse(path.open('rb'),content_type=mime,as_attachment=row['format'] not in ('png','jpg','jpeg','gif','pdf','mp4','mp3'),filename=path.name)
    response['Cache-Control']='private, no-store';response['X-Content-Type-Options']='nosniff'
    if row['format']=='pdf':response['Content-Security-Policy']="sandbox"
    return response
