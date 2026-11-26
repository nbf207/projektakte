from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from .maintenance import MaintenanceForm, complete_maintenance, next_due, rule_state
from .models import Entry, MaintenanceRule, Project
from .operations import operation_gate
from .rules import audit


@login_required
def maintenance(request,project_id=None,pk=None):
    project=get_object_or_404(Project,pk=project_id,owner=request.user) if project_id else None
    rule=get_object_or_404(MaintenanceRule,pk=pk,project__owner=request.user) if pk else None
    if rule:
        project=rule.project
        if rule.title_template_id:return redirect(reverse('settings')+'?project='+str(project.pk))
    form=MaintenanceForm(request.POST if request.method=='POST' and request.POST.get('action')!='complete' else None,instance=rule,initial={'version':rule.version if rule else 0})
    if request.method=='POST':
        try:
            with operation_gate(),transaction.atomic():
                if request.POST.get('action')=='complete':
                    current=get_object_or_404(MaintenanceRule,pk=request.POST.get('rule'),project__owner=request.user)
                    entry=get_object_or_404(Entry,pk=request.POST.get('entry'),project__owner=request.user)
                    complete_maintenance(request.user,current,entry,int(request.POST.get('version','-1')))
                    messages.success(request,'Wartungsplan fortgeschrieben. Keine neuen Kosten gebucht.')
                    return redirect('maintenance')
                if form.is_valid():
                    obj=form.save(commit=False)
                    obj.project=project or get_object_or_404(Project,pk=request.POST.get('project'),owner=request.user)
                    if rule and MaintenanceRule.objects.get(pk=rule.pk).version!=form.cleaned_data['version']:
                        raise ValidationError('Wartungsplan wurde geändert. Neu laden.')
                    next_due(obj)
                    obj.version+=1;obj.save()
                    audit(request.user,obj,'Wartungsplan gespeichert',{'title':obj.title,'active':obj.active})
                    return redirect('maintenance')
        except (ValidationError,ValueError) as exc:
            message='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Ungültige Auswahl.'
            if request.POST.get('action')=='complete':
                messages.error(request,message)
                return redirect('maintenance')
            form.add_error(None,message)
    rules=MaintenanceRule.objects.filter(project__owner=request.user).select_related('project').order_by('project__name','title')
    if project:
        rules=rules.filter(project=project)
    states=[]
    for item in rules:
        state=rule_state(item)
        state['entries']=Entry.objects.filter(project=item.project,deleted_at__isnull=True,status='erledigt').order_by('-date')[:100]
        states.append(state)
    return render(request,'maintenance.html',{'form':form,'project':project,'projects':Project.objects.filter(owner=request.user),'states':states})
