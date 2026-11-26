"""Optional exact invoice quantities; preserve stated net/gross price basis."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.core.exceptions import ValidationError


def item_decimal(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValidationError('Menge/Einzelpreis als Dezimaltext angeben.')
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValidationError('Ungültige Menge oder ungültiger Einzelpreis.') from None
    if not result.is_finite() or abs(result) > Decimal('999999999') or result.as_tuple().exponent < -6:
        raise ValidationError('Menge/Einzelpreis: höchstens sechs Nachkommastellen und endlicher Wert.')
    return result


def validate_line_details(item, *, check_totals=True):
    values = {}
    for key in ('quantity', 'unit_price'):
        if item.get(key) not in (None, ''):
            values[key] = item_decimal(item[key])
    if item.get('unit') is not None and (not isinstance(item['unit'], str) or len(item['unit']) > 30):
        raise ValidationError('Einheit muss ein kurzer Text sein.')
    basis = item.get('price_basis') or 'unknown'
    if basis not in ('unknown', 'net', 'gross'):
        raise ValidationError('Einzelpreisbasis muss netto, brutto oder unbekannt sein.')
    if check_totals and basis == 'gross' and len(values) == 2 and item.get('amount') is not None:
        product = (values['quantity'] * values['unit_price']).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        if product != Decimal(str(item['amount'])):
            raise ValidationError('Menge × Brutto-Einzelpreis stimmt nicht mit dem Positionsbetrag überein. Rabatt gesondert erfassen oder Preisbasis prüfen.')
