"""Derive one reminder from the latest completed entry of a title template."""
from .models import Entry, MaintenanceRule


def sync_template(template):
    if template is None:
        return
    enabled = any((template.interval_days, template.interval_months, template.interval_meter))
    rule = MaintenanceRule.objects.filter(title_template=template).first()
    if not enabled and rule is None:
        return
    candidates = Entry.objects.filter(title_template=template, deleted_at__isnull=True,
        is_provisional=False, status='erledigt', date__isnull=False, precision='day').order_by('-date', '-created')
    # An automatic capture date alone does not confirm a completed maintenance date.
    latest = next((item for item in candidates if item.snapshot.get('_date_source') != 'capture'), None)
    rule = rule or MaintenanceRule(project=template.category.project, title_template=template)
    rule.title = template.name
    rule.interval_days = template.interval_days
    rule.interval_months = template.interval_months
    rule.interval_km = template.interval_meter
    rule.meter_unit = template.meter_unit
    rule.active = enabled
    rule.base_date = latest.date if latest else None
    # Do not combine an old meter reading with a newer maintenance date.
    rule.base_meter = latest.meter if latest and latest.meter_unit == template.meter_unit and not latest.meter_replaced else None
    rule.version += 1
    rule.save()


def sync_entry(entry, before=None):
    templates = {entry.title_template_id: entry.title_template}
    if before and before.title_template_id:
        templates[before.title_template_id] = before.title_template
    for template in templates.values():
        sync_template(template)
