"""Decimal API with scaled integer storage: SQLite never sums binary floats for money."""
from decimal import Decimal
from django.db import models
from django.core.exceptions import ValidationError

class ExactDecimalField(models.DecimalField):
    def get_internal_type(self): return 'BigIntegerField'
    def db_type(self,connection): return models.BigIntegerField().db_type(connection)
    def get_db_prep_value(self,value,connection,prepared=False):
        if value is None: return None
        n=self.to_python(value);scaled=n*(10**self.decimal_places)
        if not n.is_finite() or scaled!=scaled.to_integral_value(): raise ValidationError('Nicht exakt darstellbarer Dezimalbetrag.')
        return int(scaled)
    def get_db_prep_save(self,value,connection):
        if hasattr(value,'as_sql'): return value
        return self.get_db_prep_value(value,connection)
    def from_db_value(self,value,expression,connection):
        if value is None: return None
        return (Decimal(value)/Decimal(10**self.decimal_places)).quantize(Decimal(1).scaleb(-self.decimal_places))
