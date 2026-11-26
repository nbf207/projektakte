from importlib import import_module
import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def remove_search(apps, schema_editor):
    import_module('akte.migrations.0018_editable_categories').remove_search(apps, schema_editor)


def install_search(apps, schema_editor):
    import_module('akte.migrations.0018_editable_categories').install_search(apps, schema_editor)


def move_definitions(apps, schema_editor):
    Category=apps.get_model('akte','Category');Project=apps.get_model('akte','Project')
    Entry=apps.get_model('akte','Entry');Task=apps.get_model('akte','Task')
    Template=apps.get_model('akte','TitleTemplate');Field=apps.get_model('akte','TitleField')
    for original in list(Category.objects.all()):
        projects=list(Project.objects.filter(owner_id=original.owner_id).order_by('created','id'))
        if not projects:
            # Retain definitions even for a legacy account with no project.
            projects=[Project.objects.create(owner_id=original.owner_id,name='Meine Projekte',kind='frei')]
        for index, project in enumerate(projects):
            if index==0:
                category=original;category.project_id=project.pk;category.save(update_fields=['project'])
            else:
                category=Category.objects.create(owner_id=original.owner_id,project=project,name=original.name,
                    name_key=original.name_key,default_kind=original.default_kind,version=original.version)
            Entry.objects.filter(project=project,category_id=original.pk).update(category_id=category.pk)
            mapping={'umbau':'umbau','umbauten':'umbau','wartung':'wartung','reparatur':'reparatur','reparaturen':'reparatur','notizen':'notiz'}
            if category.name.casefold() in mapping:
                Entry.objects.filter(project=project,category=category).update(kind=mapping[category.name.casefold()])
            Task.objects.filter(project=project,category_id=original.pk).update(category_id=category.pk)
    for project in Project.objects.all():
        entries=Entry.objects.filter(project=project)
        oil_entries=[e for e in entries if e.title.casefold().startswith(('ölwechsel','oelwechsel'))]
        if project.kind!='auto' and not oil_entries:continue
        category=Category.objects.filter(project=project,default_kind='wartung').first()
        if not category:
            category,_=Category.objects.get_or_create(project=project,name_key='wartung',defaults={
                'owner_id':project.owner_id,'name':'Wartung','default_kind':'wartung'})
        categories={category.pk:category}
        for entry in oil_entries:
            if entry.category_id:categories[entry.category_id]=Category.objects.get(pk=entry.category_id)
        for target in categories.values():
            template=Template.objects.create(category=target,name='Ölwechsel')
            fields=[Field.objects.create(template=template,label=label,field_type=typ,unit=unit,legacy_key=key)
                    for label,typ,unit,key in [('Motoröl','text','','oil'),('Ölmenge','number','l','quantity_l'),('Filter / Teilenummer','text','','filter')]]
            for entry in oil_entries:
                if (entry.category_id or category.pk)!=target.pk:continue
                prefix=10 if entry.title.casefold().startswith('oelwechsel') else 9
                suffix=entry.title[prefix:].strip()
                if len(suffix)>100:continue
                values=[{'id':str(f.pk),'label':f.label,'type':f.field_type,'unit':f.unit,
                         'value':str(entry.snapshot.get(f.legacy_key,'')),'legacy_key':f.legacy_key}
                        for f in fields if entry.snapshot.get(f.legacy_key) not in (None,'')]
                snapshot={**entry.snapshot,'extra_fields':values,'title_template_name':template.name}
                Entry.objects.filter(pk=entry.pk).update(category=target,title_template=template,title_suffix=suffix,snapshot=snapshot)


class Migration(migrations.Migration):
    dependencies=[('akte','0018_editable_categories'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[
        migrations.RunPython(remove_search,install_search),
        migrations.RemoveConstraint(model_name='category',name='category_owner_name'),
        migrations.AddField(model_name='category',name='project',field=models.ForeignKey(null=True,on_delete=django.db.models.deletion.CASCADE,related_name='categories',to='akte.project')),
        migrations.CreateModel(name='TitleTemplate',fields=[
            ('id',models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False,serialize=False)),
            ('created',models.DateTimeField(default=django.utils.timezone.now)),
            ('name',models.CharField(max_length=120)),('version',models.PositiveIntegerField(default=1)),
            ('category',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='title_templates',to='akte.category')),
        ],options={'ordering':['name','id'],'constraints':[models.UniqueConstraint(fields=('category','name'),name='template_category_name')]}),
        migrations.CreateModel(name='TitleField',fields=[
            ('id',models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False,serialize=False)),
            ('created',models.DateTimeField(default=django.utils.timezone.now)),
            ('label',models.CharField(max_length=80)),('field_type',models.CharField(max_length=10,choices=[('text','Text'),('number','Zahl')],default='text')),
            ('unit',models.CharField(max_length=20,blank=True)),('placeholder',models.CharField(max_length=120,blank=True)),
            ('legacy_key',models.CharField(max_length=20,blank=True,editable=False)),('version',models.PositiveIntegerField(default=1)),
            ('template',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='extra_fields',to='akte.titletemplate')),
        ],options={'ordering':['created','id'],'constraints':[models.UniqueConstraint(fields=('template','label'),name='field_template_label')]}),
        migrations.AddField(model_name='entry',name='title_template',field=models.ForeignKey(null=True,blank=True,on_delete=django.db.models.deletion.SET_NULL,related_name='entries',to='akte.titletemplate')),
        migrations.AddField(model_name='entry',name='title_suffix',field=models.CharField(max_length=100,blank=True)),
        migrations.AlterField(model_name='entry',name='snapshot',field=models.JSONField(default=dict,blank=True)),
        migrations.CreateModel(name='DiscardedDraft',fields=[
            ('id',models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False,serialize=False)),
            ('created',models.DateTimeField(default=django.utils.timezone.now)),('token',models.UUIDField(unique=True)),
            ('owner',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.RunPython(move_definitions,migrations.RunPython.noop),
        migrations.AlterField(model_name='category',name='project',field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='categories',to='akte.project')),
        migrations.AddConstraint(model_name='category',constraint=models.UniqueConstraint(fields=('project','name_key'),name='category_project_name')),
        migrations.RunPython(install_search,remove_search),
    ]
