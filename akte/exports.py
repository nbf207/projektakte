import csv, hashlib, html, io, json, os, zipfile, re, uuid, tempfile
from pathlib import Path
from xml.sax.saxutils import escape
from django.conf import settings
from django.core import serializers
from django.utils import timezone
from .models import (Job, Allocation, Asset, Category, CostSource, Document, DocumentLink, DocumentPage, Draft, Entry, ImportBatch, MaintenanceRule, MaintenanceCompletion, Part, PartEvent, PhotoLink, Project, Step, Task, TitleTemplate, TitleField)
from .operations import operation_gate
from .storage import durable_replace
from django.db import transaction
from .snapshots import consistent_export, export_snapshot, media_path
from .adapters import paperless,IntegrationError
from .services import check_owner
from django.core.exceptions import ValidationError
from django.db.models import Q,Sum
from decimal import Decimal
from .invoice_chronicle import marked

def report_pdf(destination,project,entries):
    with export_snapshot(project.owner):
        project=Project.objects.get(pk=project.pk)
        selection=[]
        stream=entries.iterator(chunk_size=20) if hasattr(entries,'iterator') else iter(entries)
        for entry in stream:
            selection.append(entry.pk)
            if len(selection)>settings.PDF_MAX_ENTRIES:
                raise IntegrationError('PDF-Umfang zu groß für die konfigurierte Pi-Grenze. Einzelvorgang oder vollständigen ZIP-Export wählen.')
        frozen=Entry.objects.filter(pk__in=selection,project=project).order_by(*Entry._meta.ordering)
        if frozen.count()!=len(set(selection)):
            raise ValidationError('PDF-Auswahl enthält einen fremden Projektvorgang.')
        photos=PhotoLink.objects.filter(entry__in=frozen,deleted_at__isnull=True).count()
        allocations=Allocation.objects.filter(entry__in=frozen).count()
        if photos>settings.PDF_MAX_PHOTOS or allocations>5000:
            raise IntegrationError('PDF-Umfang zu groß für die konfigurierte Pi-Grenze. Einzelvorgang oder vollständigen ZIP-Export wählen.')
        with tempfile.TemporaryDirectory(dir=settings.DATA_DIR,prefix='pdf-images-') as folder:
            return _render_pdf(destination,project,frozen,Path(folder))


def _render_pdf(destination,project,entries,image_folder):
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,Image as RLImage,PageBreak,KeepTogether
    from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import reportlab
    fonts=Path(reportlab.__file__).parent/'fonts'
    for name,file in [('Akte','Vera.ttf'),('AkteBold','VeraBd.ttf')]:
        if name not in pdfmetrics.getRegisteredFontNames(): pdfmetrics.registerFont(TTFont(name,str(fonts/file)))
    pdfmetrics.registerFontFamily('Akte',normal='Akte',bold='AkteBold',italic='Akte',boldItalic='AkteBold')
    styles=getSampleStyleSheet()
    for s in styles.byName.values(): s.fontName='Akte'
    styles['Normal'].fontSize=9;styles['Normal'].leading=14
    styles['Heading1'].fontName='AkteBold';styles['Heading1'].fontSize=23;styles['Heading1'].leading=29
    styles['Heading2'].fontName='AkteBold';styles['Heading2'].fontSize=15;styles['Heading2'].leading=20
    styles['Heading3'].fontName='AkteBold';styles['Heading3'].fontSize=11;styles['Heading3'].leading=16
    styles.add(ParagraphStyle('Caption',fontName='Akte',fontSize=8,leading=12,textColor=colors.HexColor('#67777c')))
    text_size=0
    def count_text(text):
        nonlocal text_size
        text_size+=len(str(text or ''))
        if text_size>settings.PDF_MAX_TEXT_CHARS: raise IntegrationError('PDF-Textumfang zu groß. Einzelvorgang oder vollständigen ZIP-Export wählen.')
    def p(text,style='Normal'):
        count_text(text)
        return Paragraph(escape(str(text or '')).replace('\n','<br/>'),styles[style])
    def blocks(text):
        count_text(text)
        result=[]
        for line in (text or '').splitlines():
            if not line.strip(): result.append(Spacer(1,6));continue
            match=re.match(r'^#{1,6}\s+(.*)',line)
            style='Heading3' if match else 'Normal'
            if match: line=match[1]
            elif re.match(r'^\s*[-*]\s+',line): line='• '+re.sub(r'^\s*[-*]\s+','',line)
            safe=escape(line)
            safe=re.sub(r'\*\*(.+?)\*\*',r'<b>\1</b>',safe)
            safe=re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)',r'<i>\1</i>',safe)
            result.append(Paragraph(safe,styles[style]))
        return result
    from django.utils.translation import gettext as _
    from .i18n import effective_currency, format_money
    curr = effective_currency(project.owner)
    def eur(n): return format_money(n, currency=curr, use_symbol=False)
    def table(rows,widths):
        t=Table([[p(c) for c in row] for row in rows],colWidths=widths,hAlign='LEFT',repeatRows=1)
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e6f2ef')),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8),('LINEBELOW',(0,0),(-1,-1),.4,colors.HexColor('#dce5e2'))]));return t
    content=[p('PROJEKTAKTE','Caption'),Spacer(1,10),p(project.name,'Heading1'),p(_('Synthetische Daten - lokale Abnahme') if settings.DEMO_MODE else _('Persönliche Dokumentation'),'Caption'),p(_('* = von der KI interpretiert; kann falsch sein.'),'Caption'),Spacer(1,18)]
    for idx,e in enumerate(entries.iterator(chunk_size=20)):
        if idx: content.append(PageBreak())
        content.extend([p(marked(e,'title',e.title or _('Titel unbekannt')),'Heading2'),p(marked(e,'date',e.display_date)+(f' - {marked(e,"end_date",e.end_date.strftime("%d.%m.%Y"))}' if e.end_date else '')+f' | {marked(e,"kind",e.get_kind_display() or _("Vorgangsart unbekannt"))} | {marked(e,"status",e.get_status_display() or _("Status unbekannt"))}','Caption'),Spacer(1,12)])
        rows=[[_('Angabe'),_('Bestätigter Stand')]]
        if e.meter is not None: rows.append([marked(e,'meter_name',e.meter_name),marked(e,'meter',e.meter)+' '+marked(e,'meter_unit',e.meter_unit)+(f' ({_("Zählertausch")})' if e.meter_replaced else '')])
        for label,key in [(_('Motoröl'),'oil'),(_('Menge in Litern'),'quantity_l'),(_('Filter'),'filter')]:
            if e.snapshot.get(key): rows.append([label,marked(e,key,e.snapshot[key])])
        rows.append([_('Kosten') + f' ({curr})',_('Unbekannt') if e.cost_state=='unknown' and not e.allocations.exists() else (_('Kostenlos') if e.cost_state=='free' and not e.cost else eur(e.cost))])
        if e.budget is not None: rows.extend([[_('Budget'),eur(e.budget)],[_('Verbleibend'),eur(e.remaining)]])
        desc_blocks = blocks(marked(e,'description',e.description)) if (e.description or '').strip() != (e.title or '').strip() else []
        content.extend([table(rows,[145,350]),Spacer(1,14),*desc_blocks,Spacer(1,16)])
        for step in e.steps.iterator(chunk_size=20): content.extend([p(step.title,'Heading3'),p(step.date.strftime('%d.%m.%Y') if step.date else 'Datum unbekannt','Caption'),*blocks(step.text),Spacer(1,12)])
        for photo in e.photo_links:
            source=photo.asset.thumbnail or photo.asset.file
            try:
                from PIL import Image as PILImage,ImageOps
                image_path=media_path(source)
                if photo.rotation or not photo.asset.thumbnail:
                    image_path=image_folder/f'{photo.asset_id}-{photo.rotation}.jpg'
                    if not image_path.exists():
                        with PILImage.open(media_path(source)) as original:
                            ImageOps.exif_transpose(original).rotate(-photo.rotation,expand=True).convert('RGB').save(image_path,'JPEG',quality=85)
                # File-backed lazy images release decoded pixels after drawing.
                image=RLImage(str(image_path),lazy=2);scale=min(495/image.imageWidth,260/image.imageHeight)
                image.drawWidth=image.imageWidth*scale;image.drawHeight=image.imageHeight*scale
                content.append(KeepTogether([image,Spacer(1,5),p(f'{photo.caption}'+(f' | {photo.role}' if photo.role else ''),'Caption'),Spacer(1,14)]))
            except OSError: raise IntegrationError('Ein ausgewähltes Bild fehlt. Bericht nicht vollständig.')
        costs=[['Kostenquelle / Rechnungsdatum','Zuordnung']]
        for a in e.allocations.select_related('source'):
            costs.append([f'{a.source.label} {a.source.invoice_number}\nRechnungsdatum: {a.source.invoice_date or "unbekannt"}',format_money(a.amount, currency=a.source.currency, use_symbol=False)])
        if len(costs)>1: content.extend([p('Belege und Kosten','Heading3'),table(costs,[350,145])])
    def footer(canvas,doc):
        canvas.setFont('Akte',8);canvas.setFillColor(colors.HexColor('#67777c'))
        canvas.drawString(50,30,'Projektakte | '+settings.APP_VERSION)
        canvas.drawRightString(545,30,f'Seite {doc.page}')
    SimpleDocTemplate(str(destination),pagesize=(595.28,841.89),rightMargin=50,leftMargin=50,topMargin=45,bottomMargin=50,title='Projektakte - '+project.name,author='Projektakte').build(content,onFirstPage=footer,onLaterPages=footer)

@consistent_export
def portable_export(destination,owner,project=None,entry=None):
    if project: project=Project.objects.get(pk=project.pk)
    if entry: entry=Entry.objects.get(pk=entry.pk)
    if entry:
        check_owner(owner,entry)
        if project and project.pk!=entry.project_id: raise ValidationError('Vorgang gehört nicht zum Exportprojekt.')
        project=entry.project
    if project: check_owner(owner,project)
    scope='entry' if entry else ('project' if project else 'all')
    projects=Project.objects.filter(owner=owner)
    if project: projects=projects.filter(pk=project.pk)
    entries=Entry.objects.filter(project__in=projects)
    if entry: entries=entries.filter(pk=entry.pk)
    events=PartEvent.objects.filter(entry__in=entries,part__project__owner=owner)
    parts=Part.objects.filter(project__in=projects)
    if entry: parts=parts.filter(events__in=events).distinct()
    allocations=Allocation.objects.filter(source__owner=owner)
    if entry: allocations=allocations.filter(entry=entry)
    elif project: allocations=allocations.filter(project=project)
    historical_documents=set()
    for event in events:
        for pk in event.snapshot.get('documents',[]):
            try: historical_documents.add(uuid.UUID(str(pk)))
            except (ValueError,TypeError,AttributeError): continue
    docs=Document.objects.filter(owner=owner)
    if entry:
        docs=docs.filter(Q(entry=entry)|Q(links__entry=entry)|Q(source__allocations__in=allocations)|Q(pk__in=historical_documents)).distinct()
    elif project:
        docs=docs.filter(Q(project=project)|Q(links__project=project)|Q(entry__in=entries)|Q(source__allocations__in=allocations)|Q(parts__in=parts)|Q(pk__in=historical_documents)).distinct()
    sources=CostSource.objects.filter(owner=owner)
    if project: sources=sources.filter(Q(allocations__in=allocations)|Q(document__in=docs)).distinct()
    allocations=allocations.filter(source__in=sources)
    tasks=Task.objects.filter(entry=entry) if entry else Task.objects.filter(project__in=projects)
    drafts=Draft.objects.filter(owner=owner,entry=entry) if entry else Draft.objects.filter(owner=owner,project__in=projects)
    photos=PhotoLink.objects.filter(entry__in=entries)
    imports=ImportBatch.objects.filter(owner=owner,project__in=projects) if not entry else ImportBatch.objects.none()
    pages=DocumentPage.objects.filter(document__in=docs)
    document_links=DocumentLink.objects.filter(document__in=docs,project__in=projects)
    if entry: document_links=document_links.filter(entry=entry)
    import_assets={e.snapshot.get('source_asset') for e in entries if e.snapshot.get('source_asset')}
    asset_ids=import_assets|set(pages.values_list('asset_id',flat=True))|set(imports.exclude(source_asset=None).values_list('source_asset_id',flat=True))|set(photos.values_list('asset_id',flat=True))|set(docs.exclude(asset=None).values_list('asset_id',flat=True))
    assets=Asset.objects.filter(owner=owner)
    if project: assets=assets.filter(pk__in=asset_ids)
    maintenance_rules=MaintenanceRule.objects.filter(project__in=projects)
    completions=MaintenanceCompletion.objects.filter(rule__in=maintenance_rules,entry__in=entries)
    if entry:
        completions=completions.filter(entry=entry)
        maintenance_rules=maintenance_rules.filter(completions__in=completions).distinct()
    batches=[projects,entries,Step.objects.filter(entry__in=entries),assets,photos,docs,sources,allocations,parts,events,tasks,drafts,imports,document_links,pages,maintenance_rules,completions]
    categories=Category.objects.filter(owner=owner).filter(Q(pk__in=entries.values('category_id'))|Q(pk__in=tasks.values('category_id')))
    batches.append(categories)
    title_templates=TitleTemplate.objects.filter(pk__in=entries.values('title_template_id'),category__project__owner=owner)
    batches.extend([title_templates,TitleField.objects.filter(Q(template__in=title_templates)|Q(project__in=projects))])
    records=[]
    entry_ids={str(pk) for pk in entries.values_list('pk',flat=True)}
    project_ids={str(pk) for pk in projects.values_list('pk',flat=True)}
    doc_ids={str(pk) for pk in docs.values_list('pk',flat=True)}
    omitted_references=[]
    for qs in batches:
        for record in json.loads(serializers.serialize('json',qs)):
            fields=record['fields'];fields.pop('owner',None)
            # Shared invoices keep their original amount and positions, but never pull
            # unrelated entries or their files into an excerpt just to close a FK.
            if record['model']=='akte.document':
                for field,ids in [('entry',entry_ids),('project',project_ids)]:
                    if fields.get(field) and str(fields[field]) not in ids:
                        omitted_references.append({'model':record['model'],'pk':record['pk'],'field':field,'target':fields[field]})
                        fields[field]=None
            if record['model']=='akte.part':
                fields['documents']=[pk for pk in fields['documents'] if str(pk) in doc_ids]
                if fields.get('source_document') and str(fields['source_document']) not in doc_ids:
                    omitted_references.append({'model':record['model'],'pk':record['pk'],'field':'source_document','target':fields['source_document']})
                    fields['source_document']=None
            records.append(record)
    source_context=[]
    in_scope={str(row['source_id']):row['total'] for row in allocations.values('source_id').annotate(total=Sum('amount'))}
    for source in sources:
        included=in_scope.get(str(source.pk),Decimal(0))
        remainder=source.remainder
        source_context.append({'source_id':str(source.pk),'currency':source.currency,'amount':str(source.amount),'allocated_in_scope':str(included.quantize(Decimal('.01'))),'allocated_outside_scope':str((source.amount-remainder-included).quantize(Decimal('.01'))),'unallocated_total':str(remainder.quantize(Decimal('.01')))})
    data={'format':'projektakte-portable','schema_version':2,'app_version':settings.APP_VERSION,'exported_at':timezone.now().isoformat(),'synthetic':settings.DEMO_MODE,'scope':{'kind':scope,'project_id':str(project.pk) if project else None,'entry_id':str(entry.pk) if entry else None,'omitted_references':omitted_references},'source_context':source_context,'objects':records}
    checksums={}
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as z:
        def write(name,content):
            raw=content.encode('utf8') if isinstance(content,str) else content
            z.writestr(name,raw);checksums[name]=hashlib.sha256(raw).hexdigest()
        def writefile(name,path):
            z.write(path,name)
            with open(path,'rb') as f: checksums[name]=hashlib.file_digest(f,'sha256').hexdigest()
        write('daten.json',json.dumps(data,ensure_ascii=False,indent=2))
        write('LESEN.txt','Projektakte - portabler Export v2\nindex.html im Browser öffnen. daten.json enthält versionierte Beziehungen mit stabilen IDs. Keine Passwörter oder API-Schlüssel. Kein System-Restore. Ein Vorgangsausschnitt enthält nur zugehörige Vorgänge, Schritte, Bilder, Aufgaben, Entwürfe, Teilehistorie und benötigte Belege. Geteilte Rechnungen bleiben als vollständige Originalbelege mit Gesamtsumme/Positionen erhalten; source_context unterscheidet Zuordnung im Ausschnitt, Zuordnung außerhalb und tatsächlich offenen Rest. Fremde Zuordnungszeilen und unabhängige Belege werden nicht aufgenommen. Ausgelassene Dokumentverweise stehen unter scope.omitted_references.\n')
        from django.utils.translation import gettext as _, get_language
        from .i18n import effective_currency
        curr = effective_currency(owner)
        exp_lang = (get_language() or 'de').split('-', 1)[0]
        out=io.StringIO();writer=csv.writer(out,delimiter=';');writer.writerow(['ID',_('Projekt'),_('Titel'),_('Datum'),_('Genauigkeit'),_('Kostenstatus'),curr,_('KI-Interpretationen')])
        index=[f'<!doctype html><html lang="{exp_lang}"><meta charset="utf-8"><title>Projektakte Export</title><style>body{{max-width:960px;margin:40px auto;padding:20px;font:16px/1.6 system-ui;color:#172d34}}article{{border-top:1px solid #ccc;padding:24px 0}}img{{max-width:280px}}pre{{white-space:pre-wrap}}</style><h1>Projektakte - {_("Datenexport")}</h1>']
        for e in entries.select_related('project'):
            # Date is exported with precision; auxiliary first-of-month dates are not exact days.
            export_date=e.date.strftime('%Y-%m' if e.precision=='month' else '%Y') if e.date and e.precision in ('month','year') else (str(e.date) if e.date else '')
            writer.writerow([e.pk,e.project.name,e.title,export_date,e.precision,e.cost_state,e.cost,','.join(e.inferred_fields)])
            index.append(f'<article><h2>{html.escape(e.project.name)} / {html.escape(marked(e,"title",e.title or _("Titel unbekannt")))}</h2><p>{html.escape(marked(e,"date",e.display_date))} | {e.cost} {curr}</p><pre>{html.escape(marked(e,"description",e.description))}</pre><p>{_("KI-Interpretationen:")} {html.escape(", ".join(e.inferred_fields)) or _("keine")}. {_("* = von der KI interpretiert; kann falsch sein.")}</p>')
            for step in e.steps.all(): index.append(f'<h3>{html.escape(step.title)}</h3><pre>{html.escape(step.text)}</pre>')
            for link in e.photo_links: index.append(f'<figure><img src="medien/{link.asset.file.name}" alt="{html.escape(link.caption)}"><figcaption>{html.escape(link.caption)}</figcaption></figure>')
            index.append('</article>')
        for asset in assets:
            if not Path(media_path(asset.file)).is_file(): raise IntegrationError(f'Eine referenzierte Datei fehlt ({asset.name or asset.file.name}). Export abgebrochen.')
            writefile('medien/'+asset.file.name,media_path(asset.file))
            if asset.thumbnail: writefile('medien/'+asset.thumbnail.name,media_path(asset.thumbnail))
        index.append('<h2>Belegoriginale</h2>')
        for doc in docs:
            if not settings.DEMO_MODE and doc.paperless_id:
                from .documents import remote_signature
                before=paperless(doc).read(doc.paperless_id)
                if before.get('deleted_at'): raise IntegrationError('Paperless-Beleg liegt im Papierkorb. Vollständiger Export abgebrochen.')
                signature=remote_signature(before)
                if not doc.remote_signature or signature!=doc.remote_signature:
                    raise IntegrationError('Paperless-Prüfstand verändert oder noch nicht abgeglichen. Export abgebrochen.')
                raw=paperless(doc).original(doc.paperless_id)
                if remote_signature(paperless(doc).read(doc.paperless_id))!=signature:
                    raise IntegrationError('Paperless-Dokument während des Exports verändert. Export erneut prüfen.')
                name=f'belege/{doc.pk}.bin'
                if raw.startswith(b'%PDF-'): name=name[:-4]+'.pdf'
                elif raw.startswith(b'\xff\xd8\xff'): name=name[:-4]+'.jpg'
                elif raw.startswith(b'\x89PNG'): name=name[:-4]+'.png'
                elif raw[:4]==b'RIFF' and raw[8:12]==b'WEBP': name=name[:-4]+'.webp'
                write(name,raw)
            elif doc.asset:
                asset_path=media_path(doc.asset.file)
                if not Path(asset_path).is_file():
                    raise IntegrationError(f'Belegoriginal fehlt im lokalen Speicher ({doc.asset.name or doc.asset.file.name}, Beleg {doc.pk}). Export ist nicht vollständig und wird abgebrochen.')
                name=f'belege/{doc.pk}'+Path(doc.asset.file.name).suffix;writefile(name,asset_path)
            elif doc.capture_type in ('snap','voice') and not doc.paperless_id:
                if doc.capture_type == 'snap' and doc.entry_id and not doc.pages.exists():
                    continue
                capture_text=(doc.voice_data or {}).get('text') or doc.ocr or doc.scan_note or ''
                name=f'belege/{doc.pk}.txt';write(name,capture_text)
            elif settings.DEMO_MODE:
                name=f'belege/{doc.pk}.txt';write(name,doc.ocr or 'Synthetisches Bestandsdokument ohne Original im Testadapter.')
            else:
                label=doc.proposal.get('invoice_number') or doc.edits.get('invoice_number') or str(doc.pk)
                raise IntegrationError(f'Belegoriginal nicht erreichbar ({label}, Beleg {doc.pk}). Export ist nicht vollständig und wird abgebrochen.')
            index.append(f'<p><a href="{name}">{html.escape(str(doc))}</a> | {html.escape("Im Papierkorb · "+doc.get_status_display() if doc.deleted_at else doc.get_status_display())}</p>')
        index.append('<h2>Kosten im Exportumfang</h2><p>Geteilte Rechnungen sind als ganze Belege enthalten. Außerhalb zugeordnete Beträge sind keine offenen Kosten dieses Ausschnitts.</p>')
        for context in source_context:
            index.append('<p>'+html.escape(f'Quelle {context["source_id"]}: {context["amount"]} {context["currency"]} gesamt · {context["allocated_in_scope"]} im Ausschnitt · {context["allocated_outside_scope"]} außerhalb zugeordnet · {context["unallocated_total"]} insgesamt offen')+'</p>')
        write('chronik.csv',out.getvalue());write('index.html','\n'.join(index));write('manifest.json',json.dumps({'schema_version':1,'sha256':checksums},indent=2))
    verify_archive(destination)

def verify_archive(path, *, backup=False, on_progress=None):
    with zipfile.ZipFile(path) as z:
        entries=z.infolist()
        if any(item.flag_bits&1 or ((item.external_attr>>16)&0o170000)==0o120000 for item in entries):raise ValueError('Verschlüsselte oder verknüpfte Archiveinträge ausgeschlossen.')
        if len(entries)>(250000 if backup else 10000) or len({item.filename for item in entries})!=len(entries):raise ValueError('Archiv enthält zu viele oder doppelte Einträge.')
        if sum(item.file_size for item in entries)>8*1024**3 or any(item.file_size>2*1024**3 or (item.file_size>1024**2 and item.file_size>max(1,item.compress_size)*2000) for item in entries):raise ValueError('Archiv überschreitet Entpackgrenzen.')
        if z.getinfo('manifest.json').file_size>(64 if backup else 2)*1024**2:raise ValueError('Manifest zu groß.')
        manifest=json.loads(z.read('manifest.json'))
        names=set(z.namelist())
        if names!=set(manifest['sha256'])|{'manifest.json'}: raise ValueError('Manifest und Dateiliste stimmen nicht überein.')
        sizes={item.filename:item.file_size for item in entries}
        total_bytes=sum(sizes.get(name,0) for name in manifest['sha256'])
        total_files=len(manifest['sha256'])
        done_bytes=0
        for idx,(name,digest) in enumerate(manifest['sha256'].items(),1):
            p=Path(name)
            if p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name: raise ValueError('Unsicherer Archivpfad.')
            with z.open(name) as f:
                if hashlib.file_digest(f,'sha256').hexdigest()!=digest: raise ValueError('Prüfsumme stimmt nicht: '+name)
            done_bytes+=sizes.get(name,0)
            if on_progress is not None:
                on_progress(done_bytes,total_bytes,idx,total_files)
    return manifest

def perform_export(export,job=None):
    target=settings.DATA_DIR/'exports'
    target.mkdir(exist_ok=True)
    final=target/(str(export.pk)+('.pdf' if export.kind=='wartung' else '.'+export.kind))
    attempt=str(job.lease_token if job else uuid.uuid4())
    temporary=final.with_name(final.name+'.'+attempt+'.tmp')
    try:
        if export.kind=='wartung':
            from .maintenance_pdf import maintenance_pdf
            maintenance_pdf(temporary,export.project,export.entry)
        elif export.kind=='pdf':
            entries=Entry.objects.filter(project=export.project)
            if export.entry_id:
                entries=entries.filter(pk=export.entry_id)
            else:
                entries=entries.filter(deleted_at__isnull=True)
            report_pdf(temporary,export.project,entries)
        else:
            portable_export(temporary,export.owner,export.project,export.entry)
        with operation_gate(),transaction.atomic():
            if job and not Job.objects.filter(pk=job.pk,status='running',lease_token=job.lease_token,
                                              leased_until__gt=timezone.now()).exists():
                return False
            durable_replace(temporary,final)
            export.path=str(final.relative_to(settings.DATA_DIR))
            export.status='done'
            export.error=''
            export.save(update_fields=['path','status','error'])
            if job:
                Job.objects.filter(pk=job.pk,lease_token=job.lease_token).update(
                    status='done',leased_until=None,error='',updated=timezone.now())
        return True
    finally:
        temporary.unlink(missing_ok=True)
