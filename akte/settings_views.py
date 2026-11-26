"""Project-scoped category, title and field settings without global side effects."""
import uuid

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction, models
from django.db.models import F
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .definitions import category_hint
from .forms import ProjectForm
from .editors import ProjectFields, project_field_initial, project_fields_data
from .project_config import validate_intervals
from .models import Category, Entry, Project, TitleTemplate, TitleField
from .operations import operation_gate
from .rules import audit


class DefinitionForm(forms.ModelForm):
    object_id=forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)
    version=forms.IntegerField(widget=forms.HiddenInput,initial=0,min_value=0)

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        if self.instance.pk and not self.instance._state.adding:
            self.initial.update(object_id=self.instance.pk,version=self.instance.version)


class CategoryForm(DefinitionForm):
    class Meta:
        model=Category;fields=['name'];labels={'name':'Kategorie'}


class TemplateForm(DefinitionForm):
    class Meta:
        model=TitleTemplate;fields=['category','name','interval_days','interval_months','interval_meter','meter_unit']
        labels={'category':'Kategorie','name':'Titel-Vorlage','interval_days':'Intervall in Tagen (optional)','interval_months':'Intervall in Monaten (optional)','interval_meter':'Zählerintervall (optional)','meter_unit':'Zählereinheit'}

    def __init__(self,*args,project,**kwargs):
        super().__init__(*args,**kwargs);self.fields['category'].queryset=Category.objects.filter(project=project)
    def clean(self):
        data=super().clean()
        if not self.errors:validate_intervals(data)
        return data


class FieldForm(DefinitionForm):
    class Meta:
        model=TitleField;fields=['template','label','field_type','unit','placeholder']
        labels={'template':'Titel-Vorlage','label':'Feldbezeichnung','field_type':'Feldtyp','unit':'Einheit (optional)','placeholder':'Platzhalter (optional)'}

    def __init__(self,*args,project,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['template'].queryset=TitleTemplate.objects.filter(category__project=project).select_related('category')
        self.fields['template'].label_from_instance=lambda obj:obj.category.name+' · '+obj.name
        self.fields['template'].empty_label='Projektweit (alle Kategorien und freien Titel)'
        selected=self.data.get(self.add_prefix('template')) if self.is_bound else (self.instance.template_id or self.initial.get('template'))
        self.instance.project=project if not selected else None


class ProjectSettingsForm(ProjectForm):
    class Meta(ProjectForm.Meta):
        fields=['name','position','color','icon','archived','ai_profile','hidden_tabs']
        labels={'name':'Projektname','position':'Reihenfolge (klein zuerst)','color':'Farbe','icon':'Icon / Emoji',
                'archived':'Archiviert','ai_profile':'KI-Projekt-Steckbrief'}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for name in ('request_token','field_editor','field_json'):self.fields.pop(name)


@login_required
@require_http_methods(['GET','POST'])
def settings_view(request):
    projects=Project.objects.filter(owner=request.user,is_scan_inbox=False).order_by('position','name')
    project_id=request.POST.get('project') if request.method=='POST' else request.GET.get('project')
    project=get_object_or_404(projects,pk=project_id) if project_id else projects.first()
    if project is None:return render(request,'settings.html',{'projects':projects})
    error=''
    definitions={
        'category':(Category,CategoryForm,Category.objects.filter(project=project)),
        'template':(TitleTemplate,TemplateForm,TitleTemplate.objects.filter(category__project=project)),
        'field':(TitleField,FieldForm,TitleField.objects.filter(models.Q(template__category__project=project)|models.Q(project=project))),
    }
    if request.method=='POST':
        try:
            scope=request.POST.get('scope','category')
            if scope in ('project','master','auto_template'):
                with operation_gate(),transaction.atomic():
                    current=Project.objects.get(pk=project.pk)
                    if current.version!=int(request.POST.get('version','-1')):raise ValidationError('Projekt wurde geändert. Bitte neu laden.')
                    if scope=='project':
                        form=ProjectSettingsForm(request.POST,instance=current)
                        if not form.is_valid():raise ValidationError([str(e) for errors in form.errors.values() for e in errors])
                        current=form.save(commit=False)
                    elif scope=='master':
                        fieldset=ProjectFields(request.POST,prefix='fields')
                        if not fieldset.is_valid():raise ValidationError(str(fieldset.errors or fieldset.non_form_errors()))
                        current.fields=project_fields_data(fieldset,current.fields)
                    else:
                        from .project_templates import initial_fields
                        additions=initial_fields('auto')
                        if len(set(current.fields)|set(additions))>100:raise ValidationError('Höchstens 100 Stammdatenfelder möglich.')
                        current.fields={**current.fields,**{key:value for key,value in additions.items() if key not in current.fields}}
                    current.version+=1;current.save()
                    audit(request.user,current,'Projekteinstellungen gespeichert',{'scope':scope})
                messages.success(request,'Gespeichert. Bestehende Werte bleiben erhalten.')
                return redirect(reverse('settings')+'?project='+str(project.pk))
            if scope not in definitions:raise ValidationError('Ungültige Einstellung.')
            model,form_class,query=definitions[scope]
            identity=uuid.UUID(request.POST.get('object_id',''))
            version=int(request.POST.get('version','0'))
            with operation_gate(),transaction.atomic():
                obj=query.filter(pk=identity).first()
                if obj is None and model.objects.filter(pk=identity).exists():raise Http404
                if obj and obj.version!=version:raise ValidationError('Zwischenzeitlich geändert. Bitte neu laden.')
                if not obj and version:raise ValidationError('Dieser Eintrag wurde bereits entfernt.')
                action=request.POST.get('action')
                if action=='delete':
                    if obj:
                        from .models import MaintenanceRule
                        if scope=='template':MaintenanceRule.objects.filter(title_template=obj).update(active=False)
                        elif scope=='category':MaintenanceRule.objects.filter(title_template__category=obj).update(active=False)
                        audit(request.user,obj,'Definition entfernt',{'scope':scope,'project':str(project.pk)})
                        obj.delete()
                    messages.success(request,'Entfernt. Vorhandene Einträge, historische Feldwerte, Bilder und Kosten bleiben erhalten.')
                elif action=='save':
                    kwargs={'project':project} if scope!='category' else {}
                    obj=obj or model(pk=identity)
                    form=form_class(request.POST,instance=obj,**kwargs)
                    if not form.is_valid():raise ValidationError([str(e) for errors in form.errors.values() for e in errors])
                    obj=form.save(commit=False)
                    if scope=='category':
                        obj.owner=request.user;obj.project=project;obj.name=' '.join(obj.name.split())
                        if query.filter(name_key=obj.name.casefold()).exclude(pk=obj.pk).exists():raise ValidationError('Kategorie bereits vorhanden.')
                    if scope=='field' and not obj._state.adding and obj.template_id!=query.get(pk=identity).template_id:
                        raise ValidationError('Ein bestehendes Zusatzfeld bleibt bei seiner Titel-Vorlage. Neues Feld an der anderen Vorlage anlegen.')
                    if scope=='field':obj.project=project if not obj.template_id else None
                    obj.version+=1;obj.save()
                    if scope=='category':
                        hint=category_hint(obj)
                        if hint:Entry.objects.filter(category=obj).exclude(kind=hint).update(kind=hint,version=F('version')+1)
                    if scope=='template':
                        import copy
                        from .definitions import sync_classification
                        from .entry_links import sync_after_edit
                        for entry in Entry.objects.filter(title_template=obj).exclude(category=obj.category):
                            before=copy.copy(entry);entry.category=obj.category
                            sync_classification(entry);entry.version+=1
                            entry.save(update_fields=['category','kind','version'])
                            sync_after_edit(entry,before)
                        from .template_maintenance import sync_template
                        sync_template(obj)
                    audit(request.user,obj,'Definition gespeichert',{'scope':scope,'project':str(project.pk)})
                    messages.success(request,'Gespeichert.')
                else:raise ValidationError('Ungültige Aktion.')
            return redirect(reverse('settings')+'?project='+str(project.pk))
        except (ValidationError,ValueError,TypeError,IntegrityError) as exc:
            error='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Name bereits vorhanden oder ungültige Eingabe. Es wurde nichts geändert.'
    category_rows=[(obj,CategoryForm(instance=obj,auto_id='category_'+obj.pk.hex+'_%s')) for obj in project.categories.all()]
    template_rows=[(obj,TemplateForm(instance=obj,project=project,auto_id='template_'+obj.pk.hex+'_%s')) for obj in TitleTemplate.objects.filter(category__project=project).select_related('category')]
    field_rows=[(obj,FieldForm(instance=obj,project=project,auto_id='field_'+obj.pk.hex+'_%s')) for obj in TitleField.objects.filter(models.Q(template__category__project=project)|models.Q(project=project)).select_related('template__category')]
    tree=[]
    for category,category_form in category_rows:
        titles=[]
        for template,template_form in template_rows:
            if template.category_id!=category.pk:continue
            titles.append({'obj':template,'form':template_form,'fields':[(f,form) for f,form in field_rows if f.template_id==template.pk],
                'new_field':FieldForm(project=project,initial={'template':template.pk},auto_id='add_'+template.pk.hex+'_%s')})
        tree.append({'obj':category,'form':category_form,'titles':titles,
                     'new_template':TemplateForm(project=project,initial={'category':category.pk},auto_id='add_'+category.pk.hex+'_%s')})
    return render(request,'settings.html',{'projects':projects,'project':project,'error':error,
        'tree':tree,'project_fields':[(f,form) for f,form in field_rows if f.project_id],
        'project_form':ProjectSettingsForm(instance=project,initial={'version':project.version}),
        'fieldset':ProjectFields(initial=project_field_initial(project.fields),prefix='fields'),
        'category_form':CategoryForm(auto_id='new_category_%s'),
        'template_form':TemplateForm(project=project,auto_id='new_template_%s'),
        'field_form':FieldForm(project=project,auto_id='new_field_%s')})
