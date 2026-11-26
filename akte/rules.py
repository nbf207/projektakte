"""Shared input parsing, ownership, optimistic conflict and audit rules."""
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError, PermissionDenied
from .models import Revision

class Conflict(ValidationError): pass

def decimal_input(value, *, money=False):
    value=str(value or '').strip().replace('\u00a0','')
    if not value: return None
    # User input is German. Canonical JSON values use Decimal directly.
    if ',' in value:
        if not re.fullmatch(r'-?(?:\d+|\d{1,3}(?:\.\d{3})+),\d+',value):
            raise ValidationError('Widersprüchliche Zahlentrenner. Beispiel: 1.400,00.')
        value=value.replace('.','').replace(',','.')
    elif '.' in value:
        if re.fullmatch(r'-?\d{1,3}(?:\.\d{3})+',value): value=value.replace('.','')
        elif not re.fullmatch(r'-?\d+\.\d{1,2}',value):
            raise ValidationError('Zahl nicht eindeutig. Dezimalstellen bitte mit Komma angeben.')
    try: result=Decimal(value)
    except InvalidOperation: raise ValidationError('Bitte eine gültige Zahl eingeben.')
    if not result.is_finite() or abs(result)>Decimal('99999999999'):
        raise ValidationError('Zahl außerhalb des zulässigen Bereichs.')
    if money and result!=result.quantize(Decimal('.01')): raise ValidationError('Geldbeträge brauchen höchstens zwei Nachkommastellen.')
    return result

def exact_money(value):
    try: n=Decimal(str(value))
    except (InvalidOperation,TypeError): raise ValidationError('Ungültiger Geldbetrag.')
    if not n.is_finite() or abs(n)>Decimal('99999999999') or n!=n.quantize(Decimal('.01')):
        raise ValidationError('Geldbetrag muss endlich und centgenau sein.')
    return n

def parse_date(value):
    value=(value or '').strip()
    if not value: return None,'unknown'
    try:
        if re.fullmatch(r'\d{4}',value): return date(int(value),1,1),'year'
        if re.fullmatch(r'\d{4}-\d{2}',value): return date.fromisoformat(value+'-01'),'month'
        if re.fullmatch(r'\d{2}/\d{4}',value): return date(int(value[3:]),int(value[:2]),1),'month'
        if re.fullmatch(r'\d{2}\.\d{2}\.\d{4}',value): return date(int(value[6:]),int(value[3:5]),int(value[:2])),'day'
        return date.fromisoformat(value),'day'
    except ValueError: raise ValidationError('Datum bitte als JJJJ-MM-TT, MM/JJJJ oder JJJJ eingeben.')

def check_owner(owner, obj):
    actual = obj.owner_id if hasattr(obj,'owner_id') else obj.project.owner_id
    if actual != owner.pk: raise PermissionDenied

def audit(owner,obj,action,data):
    Revision.objects.create(owner=owner,entity=obj.pk,version=getattr(obj,'version',1),action=action,data=data)

