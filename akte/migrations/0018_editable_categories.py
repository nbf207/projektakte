from importlib import import_module
import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def remove_search(apps, schema_editor):
    import_module('akte.migrations.0017_voice_capture').remove_search(apps, schema_editor)


def install_search(apps, schema_editor):
    import_module('akte.migrations.0017_voice_capture').install_search(apps, schema_editor)


def initial_categories(apps, schema_editor):
    Category = apps.get_model('akte', 'Category')
    Project = apps.get_model('akte', 'Project')
    Entry = apps.get_model('akte', 'Entry')
    Task = apps.get_model('akte', 'Task')
    defaults = [('Wartung', 'wartung'), ('Umbau', 'umbau'), ('Reparatur', 'reparatur'),
                ('Notizen', 'notiz'), ('Pflege', ''), ('Teile & Zubehör', '')]
    for owner_id in Project.objects.values_list('owner_id', flat=True).distinct():
        for name, kind in defaults:
            category = Category.objects.create(owner_id=owner_id, name=name, name_key=name.casefold(), default_kind=kind)
            if kind:
                Entry.objects.filter(project__owner_id=owner_id, kind=kind).update(category=category)
        for task in Task.objects.filter(project__owner_id=owner_id, entry__isnull=False).select_related('entry'):
            if task.entry.category_id:
                Task.objects.filter(pk=task.pk).update(category_id=task.entry.category_id)


class Migration(migrations.Migration):
    dependencies = [('akte', '0017_voice_capture'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.RunPython(remove_search, install_search),
        migrations.CreateModel(name='Category', fields=[
            ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ('created', models.DateTimeField(default=django.utils.timezone.now)),
            ('name', models.CharField(max_length=80)),
            ('name_key', models.CharField(max_length=180, editable=False)),
            ('default_kind', models.CharField(max_length=20, blank=True, editable=False)),
            ('version', models.PositiveIntegerField(default=1)),
            ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ], options={'ordering': ['name', 'id'], 'constraints': [models.UniqueConstraint(fields=('owner', 'name_key'), name='category_owner_name')]}),
        migrations.AddField(model_name='entry', name='category', field=models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL, related_name='entries', to='akte.category')),
        migrations.AddField(model_name='task', name='category', field=models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL, related_name='tasks', to='akte.category')),
        migrations.RunPython(initial_categories, migrations.RunPython.noop),
        migrations.RunPython(install_search, remove_search),
    ]
