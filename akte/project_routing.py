"""Only owned active projects can receive a reversible, fenced AI assignment."""
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Document, DocumentLink, Project
from .operations import guarded
from .rules import Conflict, audit, check_owner

MAX_ROUTING_PROJECTS=100


def ai_context(doc):
    context={}
    if doc.project_id:
        context['project']={'id':str(doc.project_id),'name':doc.project.name,'kind':doc.project.kind,
                            'description':doc.project.description,'fields':doc.project.fields,'tags':doc.project.tags,'ai_profile':doc.project.ai_profile}
    if doc.scan_note:
        context['user_note']=doc.scan_note
    projects=list(Project.objects.filter(owner=doc.owner,archived=False,is_scan_inbox=False).order_by('name','id')[:MAX_ROUTING_PROJECTS+1]) if doc.auto_route and not doc.project_id else []
    context['routing_enabled']=bool(doc.auto_route and not doc.project_id and len(projects)<=MAX_ROUTING_PROJECTS)
    context['routing_projects']=[{'id':str(project.pk),'name':project.name,'kind':project.kind,
        'description':project.description[:300],'tags':project.tags[:150], 'ai_profile':project.ai_profile[:4000], 'fields':project.fields} for project in projects] if context['routing_enabled'] else []
    from .definitions import catalog
    selected=[doc.project] if doc.project_id else projects
    context['selected_project']=str(doc.project_id) if doc.project_id else None
    context['entry_definitions']=catalog(selected)
    context['project_catalog']=[{'id':str(p.pk),'name':p.name,'kind':p.kind,'fields':p.fields,
                                 'description':p.description,'tags':p.tags,'ai_profile':p.ai_profile} for p in selected]
    import json
    if len(json.dumps(context,ensure_ascii=False).encode())>500_000:
        raise ValidationError('Projektkontext zu groß für die gemeinsame KI-Auswertung. Bitte ein einzelnes Projekt auswählen.')
    return context


def assignment_updates(doc, assignment, expected_version):
    """Called under the job lease/write transaction; never overwrite a human target."""
    if (doc.deleted_at or doc.status=='accepted' or doc.project_id or not doc.auto_route
            or doc.version!=expected_version):
        return {}
    if not isinstance(assignment,dict): assignment={}
    if not assignment.get('project_id'):
        suggested=suggested_project(doc)
        if suggested:
            assignment={'project_id':str(suggested.pk),'confidence':'high','reason':'Eindeutiger Projektname oder einziges Fahrzeugprojekt mit KFZ-Begriffen; bitte prüfen.'}
    project=Project.objects.filter(pk=assignment.get('project_id'),owner=doc.owner,archived=False,is_scan_inbox=False).first() if assignment.get('project_id') else None
    routing={**assignment,'status':'unassigned','applied':False}
    updates={'routing':routing}
    if project and assignment.get('confidence')=='high' and assignment.get('reason'):
        if doc.entry_id and doc.entry.deleted_at:
            return updates
        from .scan_entries import move_scan_entry
        move_scan_entry(doc,project)
        identity=uuid.uuid5(doc.pk,str(project.pk)+':')
        link,_=DocumentLink.objects.get_or_create(identity=identity,defaults={'document':doc,'project':project})
        routing.update(status='automatic',applied=True,link_id=str(link.pk))
        updates.update(project_id=project.pk,version=doc.version+1)
        audit(doc.owner,doc,'KI-Projektzuordnung',{'project':str(project.pk),'inferred':True,
              'reason':assignment.get('reason')})
    return updates


@guarded
@transaction.atomic
def assign_project(owner, document, project, version):
    doc=Document.objects.get(pk=document.pk)
    check_owner(owner,doc)
    check_owner(owner,project)
    if doc.deleted_at or doc.status=='accepted':
        raise ValidationError('Nur offene Belege außerhalb des Papierkorbs hier zuordnen.')
    if doc.version!=version:
        raise Conflict('Beleg inzwischen geändert. Bitte neu laden.')
    if project.archived or project.is_scan_inbox:
        raise ValidationError('Ein aktives Projekt auswählen.')
    from .scan_entries import move_scan_entry
    move_scan_entry(doc,project)
    if doc.routing.get('status')=='automatic' and doc.routing.get('link_id'):
        DocumentLink.objects.filter(pk=doc.routing['link_id'],document=doc,entry__isnull=True).delete()
    from .files import link_document
    doc.project=project
    doc.auto_route=False
    if doc.review_draft:
        draft=doc.review_draft.copy()
        fields={key:value for key,value in draft.get('fields',{}).items() if not key.startswith('chronicle_')}
        doc.review_draft={key:value for key,value in draft.items() if key not in ('fields','allocations','connect')}
        doc.review_draft['fields']=fields
    doc.routing={'status':'manual','applied':True,'project_id':str(project.pk)}
    doc.version+=1
    doc.save(update_fields=['project','auto_route','routing','review_draft','version'])
    link_document(doc,project,doc.entry)
    audit(owner,doc,'Projektzuordnung bestätigt',{'project':str(project.pk)})
    from .scan_entries import apply_scan_proposal
    if doc.proposal:apply_scan_proposal(doc,doc.edits or doc.proposal)
    return doc


def suggested_kind(doc):
    text = (doc.ocr+' '+doc.scan_note+' '+str(doc.proposal))[:100000].casefold()
    if any(word in text for word in ('motoröl','ölfilter','ölwechsel','inspektion')): return 'wartung'
    if any(word in text for word in ('bremse','bremsbel','airbag','reparatur')): return 'reparatur'
    if any(word in text for word in ('spoiler','frontlippe','dachträger','fahrwerk','tuning')): return 'umbau'
    return ''


def suggested_project(doc):
    """Conservative form suggestion only; never book costs or move a human assignment."""
    text = (doc.ocr+' '+doc.scan_note+' '+str(doc.proposal))[:100000].casefold()
    projects=list(Project.objects.filter(owner=doc.owner,archived=False,is_scan_inbox=False))
    named=[p for p in projects if len(p.name)>=3 and p.name.casefold() in text]
    if len(named)==1:return named[0]
    car_words=('autoteil','kfz','brems','motoröl','ölfilter','maxton','spoiler','frontlippe','fahrwerk','airbag')
    cars=[p for p in projects if p.kind=='auto']
    if len(cars)==1 and any(word in text for word in car_words):return cars[0]
    return None
