"""Reversible document removal; accounting and external archives remain intact."""
import uuid

from django.db import transaction
from django.utils import timezone

from .models import Document, Job
from .operations import guarded
from .rules import Conflict, audit, check_owner


@guarded
@transaction.atomic
def change_document_deleted(owner, document, version, *, restore=False):
    doc=Document.objects.get(pk=document.pk)
    check_owner(owner,doc)
    if bool(doc.deleted_at)==(not restore):
        return doc
    if doc.version!=version:
        raise Conflict('Beleg inzwischen geändert. Bitte neu laden und prüfen.')
    job=Job.objects.filter(document=doc).first()
    if restore:
        previous=doc.trash_state
        doc.deleted_at=None
        doc.error=previous.get('error','')
        if job:
            prior=previous.get('job_status','done')
            job.status='done' if doc.status=='accepted' else ('pending' if prior in ('running','pending') else prior)
            job.available=timezone.now()
            job.error=previous.get('job_error','')
        doc.trash_state={}
    else:
        doc.trash_state={'error':doc.error,'job_status':job.status if job else 'done',
                         'job_error':job.error if job else ''}
        doc.deleted_at=timezone.now()
        if job:
            job.status='done' if doc.status=='accepted' else 'blocked'
            job.error='Beleg liegt im Papierkorb.'
    if job:
        job.lease_token=uuid.uuid4()
        job.leased_until=None
        job.save(update_fields=['status','available','error','lease_token','leased_until','updated'])
    doc.version+=1
    doc.save(update_fields=['deleted_at','trash_state','error','version'])
    audit(owner,doc,'Beleg wiederhergestellt' if restore else 'Beleg im Papierkorb',
          {'accounting_retained':True,'paperless_unchanged':True})
    return doc
