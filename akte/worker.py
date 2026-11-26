"""Serial persistent jobs; network results may only commit under their live lease."""
import uuid
import json
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .adapters import AIRequestRejected, AIResponseInvalid, AIOutcomeUncertain, IntegrationError, Pending, UploadUncertain, UploadRejected, ai, original_for_ai, paperless
from .billing import budget_month_start
from .models import AIUsage, Asset, Document, ExportRequest, Job
from .operations import guarded
from .services import thumbnail, validate_proposal


def message(exc):
    return '; '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc)[:480]


@guarded
@transaction.atomic
def reserve():
    now = timezone.now()
    # Recover older jobs interrupted after publishing a completed export.
    Job.objects.filter(kind='export',export__status='done').exclude(status='done').update(
        status='done',leased_until=None,error='',updated=now)
    if Job.objects.filter(status='running', leased_until__gt=now).exists():
        return None
    job = Job.objects.filter(
        Q(status='pending', available__lte=now) |
        Q(status='running', leased_until__lte=now) |
        Q(status='running', leased_until__isnull=True)
    ).order_by('available', 'id').first()
    if job:
        job.status = 'running'
        job.lease_token = uuid.uuid4()
        duration=60 if job.kind=='export' or (job.kind=='control' and job.control.kind=='backup') else (5 if job.kind in ('capture','voice') or job.stage=='ai' else 3)
        job.leased_until = now + timedelta(minutes=duration)
        job.save(update_fields=['status', 'lease_token', 'leased_until', 'updated'])
    return job


@guarded
@transaction.atomic
def commit_job(job, document_values=None, *, project_assignment=None, routing_version=None, **values):
    """One fenced transition for BOTH job and document, including error paths."""
    current = Job.objects.filter(pk=job.pk, status='running', lease_token=job.lease_token,
                                 leased_until__gt=timezone.now()).first()
    if not current:
        return False
    if job.document_id:
        doc = Document.objects.get(pk=job.document_id)
        if doc.status == 'accepted' or doc.deleted_at:
            values = {'status': 'done', 'leased_until': None, 'error': ''}
        elif document_values:
            if project_assignment is not None:
                from .project_routing import assignment_updates
                document_values={**document_values,**assignment_updates(doc,project_assignment,routing_version)}
            Document.objects.filter(pk=doc.pk).update(**document_values)
            if 'proposal' in document_values:
                from .scan_entries import apply_scan_proposal
                apply_scan_proposal(Document.objects.get(pk=doc.pk),document_values['proposal'])
    values['updated'] = timezone.now()
    Job.objects.filter(pk=job.pk, lease_token=job.lease_token).update(**values)
    return True


@guarded
@transaction.atomic
def reserve_ai(doc,*,context=None):
    current = Document.objects.get(pk=doc.pk)
    if current.status == 'accepted' or current.deleted_at or not current.ai_enabled:
        raise IntegrationError('KI-Auftrag wurde inzwischen beendet.')
    start = budget_month_start()
    spent = AIUsage.objects.filter(created__gte=start).aggregate(s=Sum('estimated_usd'))['s'] or Decimal(0)
    if not settings.AI_ENABLED:
        raise IntegrationError('KI deaktiviert. Bitte manuell prüfen.')
    if settings.DEMO_MODE:
        return None
    from .preferences import model_spec
    model,input_rate,output_rate=model_spec(doc.owner)
    input_reserve = len(doc.ocr[:60000].encode('utf8')) + len(json.dumps(context or {},ensure_ascii=False).encode('utf8')) + 12000
    if doc.ai_with_original:
        input_reserve = max(input_reserve, settings.AI_VISION_INPUT_RESERVE)
    ceiling = (Decimal(input_reserve) * max(input_rate, Decimal(0)) + Decimal(settings.AI_MAX_OUTPUT) * max(output_rate, Decimal(0))) / Decimal(1_000_000)
    ceiling = max(ceiling, settings.AI_REQUEST_RESERVE).quantize(Decimal('.000001'), rounding=ROUND_CEILING)
    from .preferences import monthly_budget
    if spent + ceiling > monthly_budget():
        raise IntegrationError('Appinternes KI-Monatsbudget erreicht. OCR bleibt für manuelle Prüfung verfügbar.')
    return AIUsage.objects.create(owner=doc.owner, document=doc, estimated_usd=ceiling, model=model,input_rate=input_rate,output_rate=output_rate)


@guarded
def record_usage(usage, meta):
    if not usage:
        return
    # Missing, negative or incomplete accounting must NEVER release a reservation.
    prompt = meta.get('promptTokenCount')
    total = meta.get('totalTokenCount')
    if type(prompt) is int and type(total) is int and 0 <= prompt <= total and total > 0:
        usage.input_tokens = prompt
        usage.output_tokens = total - prompt
        actual = (Decimal(prompt) * (usage.input_rate if usage.input_rate is not None else settings.AI_INPUT_USD_PER_M) + Decimal(total-prompt) * (usage.output_rate if usage.output_rate is not None else settings.AI_OUTPUT_USD_PER_M)) / Decimal(1_000_000)
        usage.estimated_usd = actual.quantize(Decimal('.000001'), rounding=ROUND_CEILING)
        usage.state = 'complete'
        AIUsage.objects.filter(pk=usage.pk).update(input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,estimated_usd=usage.estimated_usd,state=usage.state)


def process_document(job, doc):
    if job.stage == 'reuse':
        adapter = paperless(doc)
        from .documents import remote_signature
        document_id=doc.paperless_id or adapter.reconcile(doc,pending_if_missing=True)
        before=paperless(doc).read(document_id,doc)
        if int(before.get('id',0))!=document_id or before.get('deleted_at'):
            raise UploadUncertain('Früheres Archivdokument fehlt oder liegt im Paperless-Papierkorb. Zuerst abgleichen.')
        if not settings.DEMO_MODE and paperless(doc).original_sha256(document_id)!=doc.asset.sha256:
            raise UploadUncertain('Früheres Archivoriginal wurde verändert. Identität manuell prüfen; kein automatischer Neu-Upload.')
        after=paperless(doc).read(document_id,doc)
        if remote_signature(before)!=remote_signature(after):
            raise UploadUncertain('Archivdokument während der Prüfung geändert. Erneut abgleichen.')
        commit_job(job,{'paperless_id':document_id,'ocr':after.get('content') or '',
            'verified_at':timezone.now(),'remote_signature':remote_signature(after),
            'remote_state':'current','error':'','status':'ai' if doc.ai_enabled else 'review'},
            stage='ai',status='pending' if doc.ai_enabled else 'done',attempts=0,leased_until=None)
    elif job.stage == 'upload':
        adapter = paperless(doc)
        if doc.paperless_id:
            commit_job(job, stage='ocr', status='pending', attempts=0)
        elif doc.submitted_at and not doc.task_id:
            document_id = adapter.reconcile(doc)
            commit_job(job, {'paperless_id': document_id, 'status': 'ocr'}, stage='ocr', status='pending', attempts=0)
        else:
            if not doc.task_id:
                if not commit_job(job, {'submitted_at': timezone.now(), 'status': 'paperless'}):
                    return
                task = adapter.upload(doc)
                commit_job(job, {'task_id': task, 'status': 'ocr'}, stage='poll', status='pending', attempts=0,
                           available=timezone.now()+timedelta(seconds=1), leased_until=None)
            else:
                commit_job(job, stage='poll', status='pending', attempts=0, leased_until=None)
    elif job.stage == 'poll':
        adapter = paperless(doc)
        document_id = adapter.poll(doc)
        commit_job(job, {'paperless_id': document_id, 'status': 'ocr'}, stage='ocr', status='pending', attempts=0)
    elif job.stage == 'ocr':
        adapter = paperless(doc)
        result = adapter.read(doc.paperless_id, doc)
        if int(result.get('id', 0)) != doc.paperless_id:
            raise IntegrationError('Paperless-Dokumentidentität stimmt nicht überein.')
        if result.get('deleted_at'):
            commit_job(job,{'remote_state':'missing','error':'Dokument liegt im Paperless-Papierkorb. Original bleibt lokal erhalten.','status':'review' if doc.verified_at else 'failed'},status='blocked',leased_until=None)
            return
        from .documents import remote_signature
        commit_job(job, {'ocr': result.get('content') or '', 'remote_signature': remote_signature(result),
                         'remote_state': 'current', 'verified_at': timezone.now(),
                         'status': 'ai' if doc.ai_enabled else 'review', 'error': ''},
                   stage='ai', status='pending' if doc.ai_enabled else 'done', attempts=0, leased_until=None)
    elif job.stage == 'ai':
        if not doc.ai_enabled:
            commit_job(job, {'status': 'review'}, status='done', leased_until=None)
            return
        from .project_routing import ai_context
        context=ai_context(doc)
        original, mime = original_for_ai(doc) if doc.ai_with_original else (None, None)
        if not doc.ocr and original is None and not doc.paperless_id and not settings.DEMO_MODE:
            from django.utils.translation import gettext as _
            commit_job(job, {'status': 'review', 'error': _('Datei überschreitet das direkte KI-Limit ohne OCR. Bitte manuell prüfen.')},
                       status='done', attempts=0, error='', leased_until=None)
            return
        usage = reserve_ai(doc,context=context)
        try:
            if doc.ai_with_original:
                proposal, meta = ai(usage.model if usage else None).extract(doc.ocr, original=original, mime_type=mime,context=context)
            else:
                proposal, meta = ai(usage.model if usage else None).extract(doc.ocr,context=context)
        except AIRequestRejected:
            reject_usage(usage)
            raise
        except AIResponseInvalid as exc:
            record_usage(usage,exc.usage)
            raise
        # Record real charges even when the lease expired or validation rejects output.
        record_usage(usage, meta)
        validate_proposal(proposal,check_totals=False)
        from .billing import proposal_warnings
        warning='; '.join(proposal_warnings(proposal))[:500]
        commit_job(job, {'proposal': proposal, 'status': 'review', 'error': warning},
                   project_assignment=proposal.get('project_assignment'),routing_version=doc.version,
                   status='done', attempts=0, error='', leased_until=None)


def fail(job, exc):
    count = job.attempts + 1
    text = message(exc)
    from .account_events import error_event
    owner=(job.document.owner if job.document_id else job.asset.owner if job.asset_id else job.export.owner if job.export_id else job.control.owner if job.control_id else None)
    error_event('Worker',text,owner)
    if isinstance(exc,AIOutcomeUncertain) or (isinstance(exc,AIRequestRejected) and not exc.retryable):
        commit_job(job,{'status':'review','error':text},status='blocked',attempts=count,error=text,leased_until=None)
    elif isinstance(exc, UploadUncertain):
        commit_job(job, {'status': 'uncertain', 'error': text}, status='blocked', error=text, leased_until=None)
    elif isinstance(exc, Pending):
        values = {'status': 'uncertain', 'error': 'Paperless-Auftrag lange nicht bestätigt. Bitte manuell abgleichen.'} if count >= 60 else None
        commit_job(job, values, status='blocked' if count >= 60 else 'pending', attempts=count,
                   available=timezone.now()+timedelta(seconds=15), leased_until=None, error=text)
    else:
        document_values = None
        if job.document_id:
            doc = Document.objects.filter(pk=job.document_id).first()
            if doc is None:
                return
            document_values = {'error': text, 'status': 'review' if (doc.verified_at or job.stage == 'ai') else 'failed'}
            if isinstance(exc,UploadRejected) and job.stage=='upload' and not doc.task_id:
                document_values['submitted_at']=None
        if commit_job(job, document_values, status='failed' if count >= 3 else 'pending', attempts=count,
                      available=timezone.now()+timedelta(seconds=min(300, 15*2**count)), error=text, leased_until=None):
            if job.asset_id:
                with_asset_error(job.asset_id)


@guarded
def reject_usage(usage):
    # Only explicit client-side HTTP refusals release this request's reservation.
    # Timeouts, malformed successful output and server failures stay conservative.
    if usage:
        AIUsage.objects.filter(pk=usage.pk,state='reserved').update(estimated_usd=Decimal(0),state='rejected')


@guarded
def with_asset_error(asset_id):
    Asset.objects.filter(pk=asset_id).update(error='Vorschau fehlgeschlagen. Original bleibt erhalten.')


def run_one():
    if (settings.DATA_DIR / '.maintenance').exists():
        return False
    job = reserve()
    if not job:
        return False
    from django.utils import translation
    from .i18n import effective_language
    job_owner = (job.document.owner if job.document_id else job.asset.owner if job.asset_id else job.export.owner if job.export_id else job.control.owner if job.control_id else None)
    job_lang = effective_language(job_owner)
    with translation.override(job_lang):
        try:
            if job.kind == 'control':
                from .control_tasks import process_control
                process_control(job)
            elif job.kind == 'capture':
                from .capture import process_capture
                process_capture(job)
            elif job.kind == 'voice':
                from .voice import process_voice
                process_voice(job)
            elif job.kind == 'export':
                from .exports import perform_export
                perform_export(job.export,job=job)
            elif job.kind == 'thumbnail':
                thumbnail(job.asset)
                commit_job(job, status='done', leased_until=None, error='')
            else:
                doc = Document.objects.get(pk=job.document_id)
                if doc.status == 'accepted' or doc.deleted_at:
                    commit_job(job, status='done', leased_until=None)
                else:
                    process_document(job, doc)
        except (IntegrationError, ValidationError, OSError) as exc:
            fail(job, exc)
        except Exception:
            from .account_events import error_event
            error_event('Worker','Unerwarteter Verarbeitungsfehler; Eingaben bleiben gespeichert.',job_owner)
            # Never persist invoice bodies, vendor responses, tokens or credentials in errors.
            commit_job(job, {'status': 'failed', 'error': 'Verarbeitung fehlgeschlagen. Original bleibt gespeichert.'},
                       status='failed', error='Unerwarteter Verarbeitungsfehler. Original bleibt gespeichert; Konfiguration prüfen.', leased_until=None)
        if job.export_id:
            with_export_error(job)
    return True


@guarded
def with_export_error(job):
    latest = Job.objects.filter(pk=job.pk).first()
    if latest is None:
        return
    if latest.status == 'failed':
        ExportRequest.objects.filter(pk=job.export_id).update(status='failed', error=latest.error)
