from importlib import import_module
from django.db import migrations, models


def remove_search(apps, schema_editor):
    import_module('akte.migrations.0014_scan_routing_and_trash').remove_search(apps, schema_editor)


def install_search(apps, schema_editor):
    import_module('akte.migrations.0014_scan_routing_and_trash').install_filtered_search(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [('akte', '0014_scan_routing_and_trash')]
    operations = [
        migrations.RunPython(remove_search, install_search),
        migrations.AddField(model_name='project', name='is_scan_inbox', field=models.BooleanField(default=False, editable=False)),
        migrations.AddField(model_name='entry', name='is_provisional', field=models.BooleanField(default=False, editable=False)),
        migrations.AddConstraint(model_name='project', constraint=models.UniqueConstraint(fields=('owner',), condition=models.Q(is_scan_inbox=True), name='one_scan_inbox_per_owner')),
        migrations.RunPython(install_search, remove_search),
    ]
