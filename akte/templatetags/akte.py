from decimal import Decimal
import markdown, nh3
from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _
from akte.i18n import format_money, format_number
register=template.Library()
_RICH_TAGS={'p','br','strong','em','ul','ol','li','h2','h3','h4','table','thead','tbody','tr','td','th','a','code','pre','blockquote','hr'}
_RICH_ATTRS={'a':{'href','title'}}
_RICH_SCHEMES={'http','https','mailto'}
@register.filter
def money(value, currency=None):
    return format_money(value, currency=currency)
@register.filter
def eur(value, currency=None):
    return format_money(value, currency=currency)
@register.filter
def de_number(value):
    return format_number(value)
@register.filter
def rich(value):
    return mark_safe(nh3.clean(markdown.markdown(value or '',extensions=['tables','fenced_code']),tags=_RICH_TAGS,attributes=_RICH_ATTRS,url_schemes=_RICH_SCHEMES))
@register.filter
def lookup(value,key): return value.get(key,'') if isinstance(value,dict) else ''


def interpretation_star():
    tip = escape(_('Von der KI interpretiert; bitte prüfen'))
    label = escape(_('Von der KI interpretiert'))
    return mark_safe(f'<span class="ai-inferred" title="{tip}" aria-label="{label}">*</span>')


@register.filter
def ai_mark(entry,field):
    return interpretation_star() if field in entry.inferred_fields else ''


@register.simple_tag
def ai_proposal_mark(proposal,field):
    return interpretation_star() if isinstance(proposal,dict) and field in proposal.get('inferred_fields',[]) else ''


@register.simple_tag
def ai_position_mark(proposal,index,field):
    return ai_proposal_mark(proposal,f'positions.{index}.{field}')
