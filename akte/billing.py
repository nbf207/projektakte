"""Exact accounting, validated allocations and once-only invoice acceptance."""
import re
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import Allocation, CostSource, Document, Job
from .operations import guarded
from .line_items import validate_line_details
from .rules import Conflict, audit, check_owner, exact_money

@guarded
@transaction.atomic
def allocate(owner, source, rows):
    source=CostSource.objects.get(pk=source.pk)
    check_owner(owner,source)
    normalized=[]
    position_totals={}
    for row in rows:
        project=row['project'];entry=row.get('entry');step=row.get('step')
        check_owner(owner,project)
        if project.is_scan_inbox: raise ValidationError('Vor Kostenbuchung das tatsächliche Projekt auswählen.')
        if entry and (entry.project_id!=project.pk or entry.deleted_at): raise ValidationError('Vorgang gehört nicht zum Zielprojekt oder liegt im Papierkorb.')
        if step and (not entry or step.entry_id!=entry.pk): raise ValidationError('Arbeitsschritt gehört nicht zum Vorgang.')
        n=exact_money(row['amount'])
        if not row.get('position_ref') and n and (n>0)!=(source.amount>0): raise ValidationError('Zuordnung muss dasselbe Vorzeichen wie die Quelle haben.')
        ref=str(row.get('position_ref','')).strip()
        if ref:
            if not ref.isdecimal() or not 1<=int(ref)<=len(source.positions):
                raise ValidationError('Positionsbezug muss eine vorhandene Positionsnummer (ab 1) sein.')
            ref=str(int(ref))
            position=exact_money(source.positions[int(ref)-1]['amount'])
            if n and (n>0)!=(position>0):
                raise ValidationError('Zuordnung passt nicht zum Vorzeichen der Position.')
            position_totals[ref]=position_totals.get(ref,Decimal(0))+n
            if abs(position_totals[ref])>abs(position):
                raise ValidationError('Rechnungsposition ist überzugeordnet.')
        normalized.append((project,entry,step,n,ref))
    total=sum((r[3] for r in normalized),Decimal('0'))
    if total and (total>0)!=(source.amount>0): raise ValidationError('Gesamtzuordnung passt nicht zum Vorzeichen der Quelle.')
    if abs(total)>abs(source.amount): raise ValidationError(f'Überzuordnung um {abs(total)-abs(source.amount):.2f} {source.currency}.')
    source.allocations.all().delete()
    Allocation.objects.bulk_create([Allocation(source=source,project=p,entry=e,step=s,amount=n,position_ref=ref) for p,e,s,n,ref in normalized if n])
    return source.amount-total

def validate_proposal(p, *, final=False, check_totals=True):
    if not isinstance(p,dict): raise ValidationError('KI-Antwort ist kein Datenobjekt.')
    allowed={'invoice_number','invoice_date','service_date','odometer_km','gross_total','net_total','tax_total','currency','positions','vendor','entry','inferred_fields','project_assignment','definition'}
    if set(p)-allowed: raise ValidationError('KI-Antwort enthält unbekannte Felder.')
    from .proposal_schema import validate_metadata
    validate_metadata(p)
    for key in ('invoice_number','vendor'):
        if p.get(key) is not None and (not isinstance(p[key],str) or len(p[key])>200): raise ValidationError('Ungültiges Textfeld im Vorschlag.')
    for key in ('invoice_date','service_date'):
        if p.get(key):
            try: date.fromisoformat(p[key])
            except (ValueError,TypeError): raise ValidationError('Vorschlag enthält ein ungültiges Datum.')
    if p.get('odometer_km') is not None:
        try: n=Decimal(str(p['odometer_km']))
        except InvalidOperation: raise ValidationError('Ungültiger Zählerstand.')
        if not n.is_finite() or n<0: raise ValidationError('Ungültiger Zählerstand.')
    for key in ('gross_total','net_total','tax_total'):
        if p.get(key) is not None: exact_money(p[key])
    if p.get('currency') is not None and (not isinstance(p['currency'],str) or not re.fullmatch('[A-Z]{3}',p['currency'])): raise ValidationError('Währung braucht einen ISO-Code.')
    if final and (p.get('gross_total') is None or not p.get('currency')): raise ValidationError('Gesamtbetrag und Währung fehlen.')
    positions=p.get('positions',[])
    if not isinstance(positions,list) or len(positions)>300: raise ValidationError('Ungültige Positionsliste.')
    for item in positions:
        if not isinstance(item,dict) or not isinstance(item.get('description'),str) or len(item['description'])>500: raise ValidationError('Ungültige Rechnungsposition.')
        if set(item)-{'description','amount','part_number','quantity','unit','unit_price','price_basis'}: raise ValidationError('Unbekanntes Feld in Rechnungsposition.')
        if item.get('part_number') is not None and (not isinstance(item['part_number'],str) or len(item['part_number'])>150): raise ValidationError('Ungültige Teilenummer.')
        if item.get('amount') is not None: exact_money(item['amount'])
        elif final: raise ValidationError('Betrag einer Rechnungsposition fehlt. Bitte vor Kostenübernahme ergänzen.')
        validate_line_details(item, check_totals=check_totals)
    if check_totals:
        warnings = proposal_warnings(p)
        if warnings:
            raise ValidationError(warnings)
    return p

def proposal_warnings(proposal):
    """Accounting inconsistencies are reviewable; they never permit booking."""
    warnings = []
    positions = proposal.get('positions', [])
    for index, item in enumerate(positions, 1):
        try:
            validate_line_details(item)
        except ValidationError as exc:
            warnings.extend(f'Position {index}: {message}' for message in exc.messages)
    if proposal.get('gross_total') is not None:
        total = exact_money(proposal['gross_total'])
        if positions and all(item.get('amount') is not None for item in positions):
            if sum((exact_money(item['amount']) for item in positions), Decimal(0)) != total:
                warnings.append('Positionssumme stimmt nicht mit dem Gesamtbetrag überein. Bitte korrigieren.')
        if proposal.get('net_total') is not None and proposal.get('tax_total') is not None:
            if exact_money(proposal['net_total']) + exact_money(proposal['tax_total']) != total:
                warnings.append('Netto plus Steuer stimmt nicht mit Brutto überein.')
    return warnings

@guarded
@transaction.atomic
def accept_document(owner,doc,proposal,rows,*,expected_version,connect=None,allow_similar=False,chronicle=None):
    doc=Document.objects.get(pk=doc.pk);check_owner(owner,doc)
    if doc.deleted_at: raise ValidationError('Beleg liegt im Papierkorb. Zuerst wiederherstellen.')
    if doc.status=='accepted': return doc.source
    if doc.version!=expected_version: raise Conflict('Die Rechnung wurde zwischenzeitlich geändert. Neu laden; deine gespeicherten Korrekturen bleiben erhalten.')
    local_only = doc.instance == 'local' and bool(doc.asset_id) and not doc.paperless_id
    if not local_only:
        if not doc.paperless_id or not doc.verified_at: raise ValidationError('Paperless-Dokument noch nicht bestätigt. Erst Übergabe bzw. Abgleich abschließen.')
        if doc.remote_state in ('changed','missing'): raise ValidationError('Paperless-Dokument verändert oder nicht verfügbar. Zuerst abgleichen und Prüfstand aktualisieren.')
    validate_proposal(proposal,final=True)
    amount=exact_money(proposal['gross_total'])
    similar=CostSource.objects.filter(owner=owner,invoice_number=proposal.get('invoice_number') or '-',amount=amount,document__isnull=False).exclude(document=doc)
    if similar.exists() and not allow_similar: raise ValidationError('Ähnliche Rechnungsnummer und Summe vorhanden. Bitte prüfen und „Eigenständige Rechnung“ ausdrücklich bestätigen.')
    if connect:
        connect=CostSource.objects.get(pk=connect.pk);check_owner(owner,connect)
        if connect.document_id or connect.amount!=amount or connect.currency!=proposal['currency']: raise ValidationError('Vorhandene Ausgabe passt nicht zu Betrag/Währung oder hat bereits einen Beleg.')
        source=connect
    else: source=CostSource(owner=owner,amount=amount,currency=proposal['currency'])
    source.document=doc;source.label=proposal.get('vendor') or 'Rechnung'
    source.invoice_number=proposal.get('invoice_number') or ''
    source.invoice_date=proposal.get('invoice_date') or None
    source.service_date=proposal.get('service_date') or None
    source.positions=proposal.get('positions',[])
    source.version+=1
    source.save()
    if connect and not rows:
        rows=[{'project':a.project,'entry':a.entry,'step':a.step,'amount':a.amount,'position_ref':a.position_ref} for a in source.allocations.select_related('project','entry','step')]
    allocate(owner,source,rows)
    if chronicle is not None:
        from .invoice_chronicle import attach_chronicle
        attach_chronicle(owner,doc,proposal,chronicle,source)
    doc.edits=proposal;doc.review_draft={};doc.status='accepted';doc.error='';doc.version+=1;doc.save()
    # Invalidate in-flight network responses in the same transaction as acceptance.
    Job.objects.filter(document=doc).update(status='done',lease_token=uuid.uuid4(),leased_until=None,error='')
    audit(owner,doc,'Rechnung übernommen',proposal)
    return source



def budget_month_start():
    """The app budget uses calendar months in UTC, in both display and reservation."""
    return timezone.now().replace(day=1,hour=0,minute=0,second=0,microsecond=0)
