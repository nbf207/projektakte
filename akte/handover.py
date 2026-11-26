"""A detached printable HTML report, rendered inside the export snapshot."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.template.loader import render_to_string
from django.http import HttpResponse
from django.views.decorators.http import require_POST
from .control_forms import HandoverForm
from .models import Entry, Project
from .snapshots import export_snapshot


@login_required
@require_POST
def handover(request):
    form=HandoverForm(request.POST,owner=request.user)
    if not form.is_valid():return HttpResponse('Gültiges eigenes Projekt auswählen.',status=400)
    selected=form.cleaned_data
    with export_snapshot(request.user):
        project=Project.objects.get(pk=selected['project'].pk,owner=request.user)
        rows=[]
        def permitted(label):
            label=label.casefold()
            if selected['hide_costs'] and any(key in label for key in ('preis','kosten','betrag','budget','price','cost','amount')):return False
            if selected['hide_dealers'] and any(key in label for key in ('händler','haendler','lieferant','vendor','dealer','shop')):return False
            if selected['hide_notes'] and any(key in label for key in ('notiz','privat','kommentar','note','private','comment')):return False
            return True
        for entry in Entry.objects.filter(project=project,deleted_at__isnull=True,is_provisional=False).select_related('category').order_by('date','created'):
            rows.append({'title':entry.title,'date':entry.display_date,'category':entry.category.name if entry.category_id else '',
                'meter':str(entry.meter)+' '+entry.meter_unit if entry.meter is not None else '',
                'status':entry.get_status_display(),
                'costs':[(str(a.amount),a.source.currency) for a in entry.allocations.select_related('source')] if not selected['hide_costs'] else [],
                'description':entry.description if not any(selected[key] for key in ('hide_notes','hide_costs','hide_dealers')) else '',
                'fields':[row for row in entry.snapshot.get('extra_fields',[]) if permitted(row['label'])],
                'dealers':list(entry.linked_documents.values_list('proposal__vendor',flat=True)) if not selected['hide_dealers'] else []})
        fields=[{'name':key,'value':field.get('value',''),'unit':field.get('unit','')} for key,field in project.fields.items() if field.get('active',True) and permitted(key)]
        content=render_to_string('handover.html',{'name':project.name,'rows':rows,'fields':fields,'choices':selected},request=request)
    return HttpResponse(content)
