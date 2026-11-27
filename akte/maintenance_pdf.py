"""Compact, paginated maintenance history from the same consistent export snapshot."""
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape
from django.conf import settings
from django.core.exceptions import ValidationError
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle
import reportlab
from .adapters import IntegrationError
from .models import Entry, Project
from .snapshots import export_snapshot
from .invoice_chronicle import marked


def maintenance_pdf(destination,project,entry=None):
    with export_snapshot(project.owner):
        project=Project.objects.get(pk=project.pk)
        entries=Entry.objects.filter(project=project,deleted_at__isnull=True,status='erledigt').exclude(kind='notiz').order_by('date','created')
        if entry:
            if entry.project_id!=project.pk:
                raise ValidationError('Vorgang gehört nicht zum Projekt.')
            entries=entries.filter(pk=entry.pk)
        if entries.count()>settings.PDF_MAX_ENTRIES:
            raise IntegrationError('Wartungstabelle überschreitet die konfigurierte Vorgangsgrenze. ZIP wählen.')
        fonts=Path(reportlab.__file__).parent/'fonts'
        if 'Akte' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('Akte',str(fonts/'Vera.ttf')))
        if 'AkteBold' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('AkteBold',str(fonts/'VeraBd.ttf')))
        style=ParagraphStyle('maintenance',fontName='Akte',fontSize=8,leading=11)
        heading=ParagraphStyle('maintenance-heading',fontName='AkteBold',fontSize=17,leading=22,spaceAfter=9)
        used=0
        def text(value):
            nonlocal used
            value=str(value or '')
            used+=len(value)
            if used>settings.PDF_MAX_TEXT_CHARS:
                raise IntegrationError('Wartungstabelle überschreitet die konfigurierte Textgrenze.')
            return Paragraph(escape(value).replace('\n','<br/>'),style)
        from django.utils.translation import gettext as _
        from .i18n import effective_currency, format_number
        curr = effective_currency(project.owner)
        def number(value,*,money=False):
            return format_number(f'{value:.2f}' if money else value)
        rows=[[text(x) for x in [_('Datum'),_('Zähler'),_('Wartung / Reparatur / Umbau'),_('Kosten') + f' ({curr})']]]
        total=Decimal('0'); unknown=0
        for item in entries:
            amount=item.cost;total+=amount
            unknown_label = _('Unbekannt')
            cost=unknown_label if item.cost_state=='unknown' and not item.allocations.exists() else (_('Kostenlos') if item.cost_state=='free' and amount==0 else number(amount,money=True))
            if cost==unknown_label:unknown+=1
            foreign=list(item.allocations.exclude(source__currency=curr).values_list('amount','source__currency'))
            if foreign:
                cost+='\n'+'; '.join(f'{number(n,money=True)} {currency}' for n,currency in foreign)+f' ({_("separat")})'
            description=marked(item,'title',item.title or _('Titel unbekannt'))
            if item.description and item.description.strip() != (item.title or '').strip():
                description+='\n'+marked(item,'description',item.description)
            details=[f'{label}: {marked(item,key,item.snapshot[key])}' for label,key in [('Öl','oil'),('Liter','quantity_l'),('Filter','filter')] if item.snapshot.get(key)]
            if details: description+='\n'+' · '.join(details)
            rows.append([text(marked(item,'date',item.display_date)),text(marked(item,'meter',number(item.meter))+' '+marked(item,'meter_unit',item.meter_unit) if item.meter is not None else 'Unbekannt'),text(description),text(cost)])
        table=LongTable(rows,colWidths=[66,69,287,73],repeatRows=1,hAlign='LEFT',splitByRow=1,splitInRow=1)
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#dcece6')),('VALIGN',(0,0),(-1,-1),'TOP'),('BOX',(0,0),(-1,-1),.5,colors.HexColor('#99b2a8')),('INNERGRID',(0,0),(-1,-1),.3,colors.HexColor('#ccd9d3')),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
        content=[Paragraph('Wartungshistorie · '+escape(project.name),heading),text('Erledigte Wartungen, Reparaturen und Umbauten. Arbeitsdatum und zugeordnete Kosten; Rechnungsdaten bleiben an den Belegen. Keine Fotos. * = von der KI interpretiert; kann falsch sein.'),Spacer(1,12),table,Spacer(1,10),text(f'Bekannte zugeordnete EUR-Beträge: {number(total,money=True)} EUR. {unknown} Vorgänge ohne Kostenangabe. Keine Währungsumrechnung.')]
        def footer(canvas,doc):
            canvas.setFont('Akte',8);canvas.drawString(50,28,'Projektakte '+settings.APP_VERSION);canvas.drawRightString(545,28,f'Seite {doc.page}')
        SimpleDocTemplate(str(destination),pagesize=(595.28,841.89),leftMargin=50,rightMargin=50,topMargin=40,bottomMargin=45,title='Wartungshistorie - '+project.name).build(content,onFirstPage=footer,onLaterPages=footer)
