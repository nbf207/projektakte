"""Per-account choices; shared settings remain restricted to system administrators."""
from datetime import date
from decimal import Decimal
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from .models import Preferences, SystemBudget


def preferences(owner):
    return Preferences.objects.filter(owner=owner).first() or Preferences(owner=owner)


def system_access(owner):
    if owner._state.db == 'demo': return False
    return owner.is_superuser or not get_user_model().objects.filter(is_active=True).exclude(pk=owner.pk).exists()


def require_system_access(owner):
    if not system_access(owner):
        raise PermissionDenied('Systemweite Sicherungen und gemeinsame Schnittstellen benötigen das Administratorkonto.')


def monthly_budget():
    if settings.DEMO_MODE:
        return settings.AI_MONTHLY_LIMIT
    configured = SystemBudget.objects.filter(pk=1).values_list('monthly_usd', flat=True).first()
    return min(configured, settings.AI_MONTHLY_LIMIT) if configured is not None else settings.AI_MONTHLY_LIMIT


def model_spec(owner):
    allowed = getattr(settings, 'ALLOWED_GEMINI_MODELS', ('gemini-3.5-flash-lite', 'gemini-3.8-flash'))
    default_model = settings.GEMINI_MODEL if settings.GEMINI_MODEL in allowed else allowed[0]
    if preferences(owner).ai_mode == 'complex' and 'gemini-3.8-flash' in allowed:
        # Published standard rates; the announced 2027 increase is included.
        future = date.today() >= date(2027, 1, 1)
        return 'gemini-3.8-flash', Decimal('1.50' if future else '.75'), Decimal('7.50' if future else '3.75')
    return default_model, settings.AI_INPUT_USD_PER_M, settings.AI_OUTPUT_USD_PER_M
