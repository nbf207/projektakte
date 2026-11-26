"""Delete unbooked drafts and their unused originals under the shared write gate."""
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import CostSource, DiscardedDraft, Document, Draft, Job, PhotoLink
from .operations import operation_gate
from .purging import purge_item
from .rules import audit


@login_required
@require_POST
def delete_draft(request, pk):
    with operation_gate(),transaction.atomic():
        draft=get_object_or_404(Draft,pk=pk,owner=request.user)
        try:version=int(request.POST.get('version','-1'))
        except ValueError:version=-1
        if version!=draft.version:return JsonResponse({'error':'Entwurf wurde geändert. Bitte neu laden.'},status=409)
        DiscardedDraft.objects.get_or_create(owner=request.user,token=draft.token)
        audit(request.user,draft,'Entwurf gelöscht',{'project':str(draft.project_id)})
        draft.delete()
    messages.success(request,'Entwurf gelöscht.');return redirect('home')


@login_required
@require_POST
def delete_document_draft(request, pk):
    try:
        with operation_gate(),transaction.atomic():
            doc=get_object_or_404(Document,pk=pk,owner=request.user)
            if doc.status=='accepted' or CostSource.objects.filter(document=doc).exists():
                raise ValidationError('Dieser Beleg wurde bereits übernommen. Bitte den normalen Papierkorb verwenden.')
            if int(request.POST.get('version','-1'))!=doc.version:
                raise ValidationError('Entwurf wurde geändert. Bitte neu laden.')
            entry=doc.entry
            wrappers=list(Document.objects.filter(owner=request.user,entry=entry,capture_type='snap',voice_data__receipt_id=str(doc.pk))) if entry else []
            if CostSource.objects.filter(document__in=wrappers).exists():raise ValidationError('Erfassung ist bereits mit bestätigten Kosten verbunden.')
            related_ids=[doc.pk,*[wrapper.pk for wrapper in wrappers]]
            automatic_ids=[]
            for capture in [doc,*wrappers]:
                if capture.capture_type!='snap':continue
                for asset_id in capture.pages.values_list('asset_id',flat=True):
                    automatic_ids.append(uuid.uuid5(capture.pk,str(asset_id)))
            automatic_photos=PhotoLink.objects.filter(entry=entry,request_key__in=automatic_ids,
                version=1,caption='',role='',step=None,rotation=0,taken_on=None,deleted_at=None)
            own=bool(entry and entry.is_provisional and entry.snapshot.get('_scan_document')==str(doc.pk))
            keep_work=bool(entry and (entry.photos.exclude(pk__in=automatic_photos.values('pk')).exists() or entry.steps.exists() or entry.allocations.exists()
                           or entry.part_events.exists() or entry.maintenancecompletion_set.exists()
                           or entry.task_set.exists() or entry.documents.exclude(pk__in=related_ids).exists()
                           or entry.documentlink_set.exclude(document_id__in=related_ids).exists()
                           or entry.snapshot.get('_definition_selected') or entry.snapshot.get('_category_selected')))
            if not keep_work:automatic_photos.delete()
            DiscardedDraft.objects.get_or_create(owner=request.user,token=doc.submission_key)
            Job.objects.filter(document=doc).update(status='done',lease_token=uuid.uuid4(),leased_until=None)
            doc.deleted_at=timezone.now();doc.version+=1;doc.save(update_fields=['deleted_at','version'])
            warnings=purge_item(request.user,doc,doc.version)
            for wrapper in wrappers:
                DiscardedDraft.objects.get_or_create(owner=request.user,token=wrapper.submission_key)
                Job.objects.filter(document=wrapper).update(status='done',lease_token=uuid.uuid4(),leased_until=None)
                wrapper.deleted_at=timezone.now();wrapper.version+=1;wrapper.save(update_fields=['deleted_at','version'])
                warnings.extend(purge_item(request.user,wrapper,wrapper.version))
            if own and not keep_work:
                entry.delete()
            for warning in warnings:messages.warning(request,warning)
        messages.success(request,'Entwurf und nicht mehr verwendete Upload-Dateien gelöscht. Vorhandene Arbeitsbilder und Paperless-Originale bleiben erhalten.')
        return redirect('inbox')
    except (ValidationError,ValueError) as exc:
        message='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Ungültige Entwurfsversion.'
        if request.headers.get('X-Requested-With')=='XMLHttpRequest':return JsonResponse({'error':message},status=409)
        messages.error(request,message);return redirect('review',pk=pk)
