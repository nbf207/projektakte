"""Keep existing links consistent on edits without creating new financial/work events."""
from django.core.exceptions import ValidationError

from django.db.models import F
from .models import Document, MaintenanceCompletion, Task


def sync_after_edit(entry, before):
    if before is None:return
    if entry.category_id!=before.category_id:
        Task.objects.filter(entry=entry,category_id=before.category_id).update(category_id=entry.category_id,version=F('version')+1)
    import uuid
    for document in Document.objects.filter(entry=entry,capture_type__in=['snap','voice']):
        task=Task.objects.filter(pk=uuid.uuid5(document.pk,'capture-task' if document.capture_type=='snap' else 'voice-task')).first()
        if not task:continue
        changes={}
        if task.title==before.title and entry.title!=before.title:changes['title']=entry.title or task.title
        if task.due==before.date and entry.date!=before.date:changes['due']=entry.date if entry.precision=='day' else None
        if task.done==(before.status=='erledigt') and entry.status!=before.status:changes['done']=entry.status=='erledigt'
        if changes:Task.objects.filter(pk=task.pk).update(**changes,version=F('version')+1)
    completions=MaintenanceCompletion.objects.filter(entry=entry).select_related('rule')
    changed=any(getattr(entry,key)!=getattr(before,key) for key in ('date','precision','meter','meter_unit','meter_replaced','status'))
    for completion in completions:
        rule=completion.rule
        if rule.title_template_id:continue
        if changed:
            if entry.status!='erledigt' or (rule.interval_months and (not entry.date or entry.precision!='day')):
                raise ValidationError('Dieser Eintrag hat einen Wartungsplan fortgeschrieben. Er muss erledigt bleiben und ein genaues Arbeitsdatum behalten.')
            if rule.interval_km is not None and (entry.meter is None or entry.meter_unit!='km' or entry.meter_replaced):
                raise ValidationError('Der verknüpfte Wartungsplan benötigt einen gültigen km-Stand ohne Zählertausch.')
            current=(not rule.interval_months or rule.base_date==before.date) and (rule.interval_km is None or rule.base_meter==before.meter)
            if rule.base_date and before.date and rule.base_date!=before.date:current=False
            if current:
                previous_date=completion.snapshot.get('previous_date')
                previous_meter=completion.snapshot.get('previous_meter')
                if previous_date and entry.date and entry.date.isoformat()<previous_date:
                    raise ValidationError('Wartungsdatum liegt vor dem vorherigen bestätigten Abschluss.')
                from decimal import Decimal
                if rule.interval_km is not None and previous_meter is not None and entry.meter<Decimal(previous_meter):
                    raise ValidationError('km-Stand liegt vor dem vorherigen bestätigten Abschluss.')
                if entry.date:rule.base_date=entry.date
                if rule.interval_km is not None:rule.base_meter=entry.meter
                rule.version+=1;rule.save()
            elif entry.date and rule.base_date and entry.date>rule.base_date:
                raise ValidationError('Dieser ältere Abschluss würde die Wartungsreihenfolge ändern. Bezugsdaten zuerst im Wartungsplan prüfen.')
        completion.snapshot={**completion.snapshot,'title':entry.title,'date':entry.date.isoformat() if entry.date else None,
                             'meter':str(entry.meter) if entry.meter is not None else None}
        completion.save(update_fields=['snapshot'])
