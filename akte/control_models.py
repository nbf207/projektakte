"""Persistent account preferences and small administrative jobs."""
from django.conf import settings
from django.db import models
from django.utils import timezone
from .models import Base, Owned


class Preferences(Base):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='preferences')
    version = models.PositiveIntegerField(default=1)
    session_days = models.PositiveIntegerField(default=30)
    auth_epoch = models.PositiveIntegerField(default=0)
    ai_mode = models.CharField(max_length=12, default='daily')
    keep_audio = models.BooleanField(default=False)
    paperless_tags = models.JSONField(default=list, blank=True)
    mail_senders = models.JSONField(default=list, blank=True)
    mail_override = models.BooleanField(default=False)
    photo_width = models.PositiveIntegerField(default=0)
    photo_quality = models.PositiveIntegerField(default=85)
    trash_days = models.PositiveIntegerField(null=True, blank=True)
    trash_costs = models.BooleanField(default=False)
    backup_keep = models.PositiveIntegerField(default=10)


class SessionDevice(Owned):
    session_key = models.CharField(max_length=40, unique=True)
    browser = models.CharField(max_length=100)
    last_seen = models.DateTimeField(default=timezone.now)


class LoginAttempt(Base):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.CASCADE)
    success = models.BooleanField()
    browser = models.CharField(max_length=100)


class ControlTask(Owned):
    kind = models.CharField(max_length=30)
    request_key = models.UUIDField(unique=True)
    payload = models.JSONField(default=dict)
    result = models.TextField(blank=True)
    @property
    def display_kind(self):
        return {'backup':'Sicherung','backup_discover':'Sicherungen einlesen','paperless_test':'Paperless-Verbindung','paperless_sync':'Paperless-Abgleich','mail':'E-Mail-Abruf'}.get(self.kind,self.kind)
    @property
    def display_state(self):
        return {'pending':'Vorgemerkt','running':'In Arbeit','done':'Erledigt','failed':'Fehlgeschlagen','blocked':'Prüfung nötig'}.get(self.job.status,self.job.status)


class ManagedBackup(Owned):
    path = models.CharField(max_length=400, unique=True)
    size = models.BigIntegerField(default=0)
    managed = models.BooleanField(default=False)


class ErrorEvent(Base):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.CASCADE)
    component = models.CharField(max_length=30)
    message = models.CharField(max_length=800)


class SystemBudget(models.Model):
    monthly_usd = models.DecimalField(max_digits=10, decimal_places=2)
