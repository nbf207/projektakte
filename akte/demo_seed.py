"""Synthetic product story. All mutations explicitly target the demo database."""
from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from .demo_boundary import demo_scope
from .models import (Allocation, Category, CostSource, Document, Entry, MaintenanceRule,
                     Part, Project, Task, TitleField, TitleTemplate)


def clear_story(user):
    # Delete in dependency order; never discover/delete files or live objects.
    projects = Project.objects.filter(owner=user)
    Allocation.objects.filter(project__in=projects).delete()
    CostSource.objects.filter(owner=user).delete()
    Part.objects.filter(project__in=projects).delete()
    Document.objects.filter(owner=user).delete()
    Task.objects.filter(project__in=projects).delete()
    Entry.objects.filter(project__in=projects).delete()
    projects.delete()


def seed_story(user):
    with demo_scope(), transaction.atomic(using='demo'):
        clear_story(user)
        today = timezone.localdate()
        project = Project.objects.create(owner=user, name='Nordlicht · Unser Reisekombi', kind='auto',
            description='Ein zuverlässiger Begleiter für Alltag und Wochenendtouren. Alle Angaben sind erfunden.',
            ai_profile='Reisekombi, Baujahr 2016, Dachträger, Bremsen, Inspektion und Innenausbau.',
            fields={'Baujahr': {'type': 'number', 'value': 2016}, 'Kilometerstand': {'type': 'number', 'value': 128450}})
        categories = {kind: Category.objects.create(owner=user, project=project, name=name, default_kind=kind)
                      for kind, name in [('wartung','Wartung'),('reparatur','Reparatur'),('umbau','Umbau'),('notiz','Pflege & Notizen')]}
        oil = TitleTemplate.objects.create(category=categories['wartung'], name='Ölwechsel', interval_months=12, interval_meter=15000)
        field = TitleField.objects.create(template=oil, label='Motoröl', field_type='text', placeholder='z. B. 5W-30')
        quantity = TitleField.objects.create(template=oil, label='Ölmenge', field_type='number', unit='Liter')
        story = [
            ('notiz','Frisch in die Saison',160,119800,'Innenraum gereinigt, Lack versiegelt und Türdichtungen gepflegt. Bereit für den nächsten Ausflug.','29.90'),
            ('reparatur','Bremsen vorne erneuert',110,122100,'Scheiben und Beläge erneuert. Nach 200 km kontrolliert: ruhiger Lauf, keine Geräusche.','248.00'),
            ('wartung','Ölwechsel · vor der Reise',65,125200,'Öl und Filter gewechselt. Altöl fachgerecht abgegeben, Dichtheit geprüft.','89.50'),
            ('umbau','Dachträger für Wochenendtouren',20,127900,'Träger montiert und Position dokumentiert. Drehmoment nach der ersten Fahrt kontrollieren.','179.00'),
        ]
        for kind, title, days, meter, description, amount in story:
            entry = Entry.objects.create(project=project, category=categories[kind], title=title, kind=kind,
                date=today-timedelta(days=days), meter=meter, description=description,
                status='arbeit' if kind=='umbau' else 'erledigt',
                title_template=oil if kind=='wartung' else None,
                snapshot={'demo_fields': {'Motoröl':'5W-30 · Herstellerfreigabe Beispiel', 'Ölmenge':'4,5 Liter'}} if kind=='wartung' else {})
            document = Document.objects.create(owner=user, project=project, entry=entry, status='accepted',
                instance='public-demo', scan_note='Anonymisierter Musterbeleg', ai_enabled=False,
                proposal={'invoice_number':'MUSTER-'+str(days), 'gross_total':amount, 'vendor':'Beispielwerkstatt', 'currency':'EUR'})
            cost = CostSource.objects.create(owner=user, document=document, label=title, amount=Decimal(amount))
            Allocation.objects.create(source=cost, project=project, entry=entry, amount=Decimal(amount))
        Document.objects.create(owner=user, project=project, status='review', instance='public-demo', ai_enabled=False,
            scan_note='Neue Fußmatten · Vorschlag noch prüfen', proposal={'invoice_number':'MUSTER-NEU', 'gross_total':'49.90', 'vendor':'Beispielzubehör', 'currency':'EUR'})
        Part.objects.create(project=project, name='Dachträger-Set', number='MUSTER-TR-42', manufacturer='Beispielzubehör', state='eingebaut')
        Part.objects.create(project=project, name='Ölfilter', number='MUSTER-OF-10', state='eingebaut')
        Task.objects.create(project=project, title='Dachträger: Befestigung nach erster Fahrt kontrollieren', due=today+timedelta(days=3))
        Task.objects.create(project=project, title='Winterreifen: Profiltiefe dokumentieren', due=today+timedelta(days=14))
        MaintenanceRule.objects.create(project=project, title='Ölwechsel', title_template=oil,
            interval_months=12, interval_km=15000, base_date=today-timedelta(days=65), base_meter=125200)
        MaintenanceRule.objects.create(project=project, title='Reifendruck kontrollieren', interval_days=30,
            base_date=today-timedelta(days=27))
        return project


def expire_visitors():
    from django.contrib.sessions.models import Session
    with demo_scope(), transaction.atomic(using='demo'):
        users = get_user_model().objects.filter(username__startswith='demo_', date_joined__lt=timezone.now()-timedelta(hours=2))
        for user in list(users[:50]):
            clear_story(user)
            user.delete(using='demo')
        Session.objects.filter(expire_date__lt=timezone.now()).delete()
