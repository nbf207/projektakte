import uuid
from decimal import Decimal
from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone
from .fields import ExactDecimalField

class Base(models.Model):
    id = models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    created = models.DateTimeField(default=timezone.now)
    class Meta: abstract = True

class Owned(Base):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    class Meta: abstract = True

class Project(Owned):
    from django.utils.translation import gettext_lazy as _l
    TYPES = [('auto',_l('Auto')),('aquarium',_l('Aquarium')),('drohne',_l('Drohne')),('frei',_l('Freies Projekt'))]
    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=20,choices=TYPES,default='frei')
    description = models.TextField(blank=True)
    fields = models.JSONField(default=dict)  # snapshot of typed field definitions and values
    tags = models.CharField(max_length=250,blank=True)
    favorite = models.BooleanField(default=False)
    archived = models.BooleanField(default=False)
    is_scan_inbox = models.BooleanField(default=False,editable=False)
    position = models.IntegerField(default=0)
    color = models.CharField(max_length=7, default='#087f80')
    icon = models.CharField(max_length=12, blank=True)
    ai_profile = models.TextField(blank=True, max_length=8000)
    hidden_tabs = models.JSONField(default=list, blank=True)
    version = models.PositiveIntegerField(default=1)
    class Meta:
        ordering = ['position','name','id']
        constraints = [models.UniqueConstraint(fields=['owner'],condition=models.Q(is_scan_inbox=True),name='one_scan_inbox_per_owner')]
    def __str__(self): return self.name
    @property
    def cost(self):
        if hasattr(self, '_annotated_cost'):
            return (Decimal(self._annotated_cost) if self._annotated_cost is not None else Decimal('0')).quantize(Decimal('.01'))
        from .i18n import effective_currency
        curr = effective_currency(self.owner) if self.owner_id else settings.DEFAULT_CURRENCY
        return (self.allocations.filter(source__currency=curr).aggregate(s=Sum('amount'))['s'] or Decimal('0')).quantize(Decimal('.01'))

class Category(Owned):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='categories')
    name = models.CharField(max_length=80)
    name_key = models.CharField(max_length=180, editable=False)
    default_kind = models.CharField(max_length=20, blank=True, editable=False)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['name', 'id']
        constraints = [models.UniqueConstraint(fields=['project', 'name_key'], name='category_project_name')]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = ' '.join(self.name.split())
        self.name_key = self.name.casefold()
        return super().save(*args, **kwargs)


class TitleTemplate(Base):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='title_templates')
    name = models.CharField(max_length=120)
    version = models.PositiveIntegerField(default=1)
    interval_days = models.PositiveIntegerField(null=True, blank=True)
    interval_months = models.PositiveIntegerField(null=True, blank=True)
    interval_meter = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    meter_unit = models.CharField(max_length=20, default='km', blank=True)

    class Meta:
        ordering = ['name', 'id']
        constraints = [models.UniqueConstraint(fields=['category', 'name'], name='template_category_name')]

    def __str__(self):
        return self.name


class TitleField(Base):
    template = models.ForeignKey(TitleTemplate, null=True, blank=True, on_delete=models.CASCADE, related_name='extra_fields')
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.CASCADE, related_name='entry_fields')
    label = models.CharField(max_length=80)
    field_type = models.CharField(max_length=10, choices=[('text', 'Text'), ('number', 'Zahl')], default='text')
    unit = models.CharField(max_length=20, blank=True)
    placeholder = models.CharField(max_length=120, blank=True)
    legacy_key = models.CharField(max_length=20, blank=True, editable=False)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['created', 'id']
        constraints = [models.UniqueConstraint(fields=['template', 'label'], name='field_template_label'),
                       models.UniqueConstraint(fields=['project', 'label'], name='field_project_label'),
                       models.CheckConstraint(condition=(models.Q(template__isnull=False, project__isnull=True) | models.Q(template__isnull=True, project__isnull=False)), name='field_one_scope')]


class Entry(Base):
    from django.utils.translation import gettext_lazy as _l
    KINDS = [('wartung',_l('Wartung')),('reparatur',_l('Reparatur')),('notiz',_l('Notiz')),('umbau',_l('Umbau'))]
    STATES = [('geplant',_l('Geplant')),('arbeit',_l('In Arbeit')),('pausiert',_l('Pausiert')),('erledigt',_l('Erledigt'))]
    project = models.ForeignKey(Project,on_delete=models.PROTECT,related_name='entries')
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name='entries')
    title_template = models.ForeignKey(TitleTemplate, null=True, blank=True, on_delete=models.SET_NULL, related_name='entries')
    title_suffix = models.CharField(max_length=100, blank=True)
    title = models.CharField(max_length=180,blank=True)
    kind = models.CharField(max_length=20,choices=KINDS,blank=True)
    status = models.CharField(max_length=20,choices=STATES,default='erledigt',blank=True)
    date = models.DateField(null=True,blank=True,db_index=True)
    precision = models.CharField(max_length=10,default='day')
    end_date = models.DateField(null=True,blank=True)
    description = models.TextField(blank=True)
    snapshot = models.JSONField(default=dict, blank=True)
    meter = models.DecimalField(max_digits=14,decimal_places=3,null=True,blank=True)
    meter_unit = models.CharField(max_length=20,default='km',blank=True)
    meter_name = models.CharField(max_length=100,default='Kilometerzähler',blank=True)
    meter_replaced = models.BooleanField(default=False)
    cost_state = models.CharField(max_length=10,default='unknown')
    budget = ExactDecimalField(max_digits=14,decimal_places=2,null=True,blank=True)
    tags = models.CharField(max_length=250,blank=True)
    request_key = models.UUIDField(default=uuid.uuid4,unique=True)
    version = models.PositiveIntegerField(default=1)
    deleted_at = models.DateTimeField(null=True,blank=True)
    is_provisional = models.BooleanField(default=False,editable=False)
    class Meta:
        indexes = [models.Index(fields=['project','deleted_at','date'])]
        ordering = [models.F('date').desc(nulls_last=True),'-id']
    def __str__(self): return self.title or 'Titel unbekannt'
    @property
    def inferred_fields(self): return self.snapshot.get('_inferred_fields',[])
    @property
    def cost(self):
        if hasattr(self, '_annotated_cost'):
            return (Decimal(self._annotated_cost) if self._annotated_cost is not None else Decimal('0')).quantize(Decimal('.01'))
        from .i18n import effective_currency
        curr = effective_currency(self.project.owner) if self.project_id else settings.DEFAULT_CURRENCY
        return (self.allocations.filter(source__currency=curr).aggregate(s=Sum('amount'))['s'] or Decimal('0')).quantize(Decimal('.01'))
    @property
    def remaining(self): return self.budget-self.cost if self.budget is not None else None
    @property
    def display_date(self):
        if not self.date: return 'Datum unbekannt'
        return self.date.strftime({'month':'%m/%Y','year':'%Y'}.get(self.precision,'%d.%m.%Y'))
    @property
    def linked_documents(self):
        from django.db.models import Q
        return Document.objects.filter(owner_id=self.project.owner_id,deleted_at__isnull=True).filter(Q(entry=self)|Q(links__entry=self)|Q(source__allocations__entry=self)).distinct()
    @property
    def photo_links(self): return self.photos.filter(deleted_at__isnull=True).select_related('asset').order_by('position','id')

class Draft(Owned):
    token = models.UUIDField(default=uuid.uuid4,unique=True)
    project = models.ForeignKey(Project,on_delete=models.CASCADE)
    entry = models.ForeignKey(Entry,null=True,blank=True,on_delete=models.CASCADE)
    payload = models.JSONField(default=dict)
    version = models.PositiveIntegerField(default=1)
    updated = models.DateTimeField(auto_now=True)


class DiscardedDraft(Owned):
    token = models.UUIDField(unique=True)

class Revision(Base):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    entity = models.UUIDField(db_index=True)
    version = models.PositiveIntegerField()
    data = models.JSONField(default=dict)
    action = models.CharField(max_length=60)

class Step(Base):
    entry = models.ForeignKey(Entry,on_delete=models.CASCADE,related_name='steps')
    title = models.CharField(max_length=180)
    text = models.TextField(blank=True)
    date = models.DateField(null=True,blank=True)
    position = models.IntegerField(default=0)
    class Meta: ordering = ['position','id']

class Asset(Owned):
    file = models.FileField(upload_to='originals/')
    thumbnail = models.FileField(upload_to='thumbs/',blank=True)
    sha256 = models.CharField(max_length=64)
    name = models.CharField(max_length=250)
    mime = models.CharField(max_length=100)
    size = models.BigIntegerField()
    error = models.CharField(max_length=250,blank=True)
    class Meta: constraints = [models.UniqueConstraint(fields=['owner','sha256'],name='asset_owner_hash')]

class PhotoLink(Base):
    request_key = models.UUIDField(null=True,blank=True,unique=True)
    version = models.PositiveIntegerField(default=1)
    asset = models.ForeignKey(Asset,on_delete=models.PROTECT,related_name='links')
    entry = models.ForeignKey(Entry,on_delete=models.CASCADE,related_name='photos')
    step = models.ForeignKey(Step,null=True,blank=True,on_delete=models.SET_NULL,related_name='photos')
    caption = models.CharField(max_length=300,blank=True)
    role = models.CharField(max_length=10,blank=True)
    position = models.IntegerField(default=0)
    rotation = models.PositiveSmallIntegerField(default=0)
    taken_on = models.DateField(null=True,blank=True)
    deleted_at = models.DateTimeField(null=True,blank=True)

class Document(Owned):
    from django.utils.translation import gettext_lazy as _l
    STATUS = [('uploaded',_l('Hochgeladen')),('paperless',_l('Wartet auf Paperless')),('ocr',_l('OCR läuft')),('ai',_l('Wartet auf KI')),('review',_l('Prüfung erforderlich')),('accepted',_l('Übernommen')),('failed',_l('Fehlgeschlagen')),('uncertain',_l('Upload unklar · Abgleich erforderlich'))]
    asset = models.ForeignKey(Asset,null=True,blank=True,on_delete=models.PROTECT)
    project = models.ForeignKey(Project,null=True,blank=True,on_delete=models.PROTECT)
    entry = models.ForeignKey(Entry,null=True,blank=True,on_delete=models.SET_NULL,related_name='documents')
    status = models.CharField(max_length=20,choices=STATUS,default='uploaded')
    instance = models.CharField(max_length=200,default='demo')
    paperless_id = models.PositiveIntegerField(null=True,blank=True)
    task_id = models.CharField(max_length=100,blank=True)
    submission_key = models.UUIDField(default=uuid.uuid4,unique=True)
    submitted_at = models.DateTimeField(null=True,blank=True)
    verified_at = models.DateTimeField(null=True,blank=True)
    remote_signature = models.CharField(max_length=64,blank=True)
    remote_state = models.CharField(max_length=20,default='unverified')
    remote_checked_at = models.DateTimeField(null=True,blank=True)
    ocr = models.TextField(blank=True)
    proposal = models.JSONField(default=dict)
    edits = models.JSONField(default=dict)
    review_draft = models.JSONField(default=dict)
    review_version = models.PositiveIntegerField(default=0)
    version = models.PositiveIntegerField(default=1)
    error = models.CharField(max_length=500,blank=True)
    capture_type = models.CharField(max_length=12,default='invoice')
    keep_audio = models.BooleanField(default=False)
    voice_data = models.JSONField(default=dict,blank=True)
    ai_enabled = models.BooleanField(default=True)
    ai_with_original = models.BooleanField(default=False)
    scan_note = models.TextField(blank=True,default='',max_length=1000)
    auto_route = models.BooleanField(default=False)
    routing = models.JSONField(default=dict,blank=True)
    deleted_at = models.DateTimeField(null=True,blank=True)
    trash_state = models.JSONField(default=dict,blank=True)
    class Meta:
        indexes = [models.Index(fields=['owner','deleted_at','created'],name='doc_owner_trash_created')]
        constraints = [models.UniqueConstraint(fields=['owner','instance','paperless_id'],name='document_identity'),models.UniqueConstraint(fields=['owner','asset'],name='document_asset')]

class CostSource(Owned):
    document = models.OneToOneField(Document,null=True,blank=True,on_delete=models.PROTECT,related_name='source')
    label = models.CharField(max_length=200)
    amount = ExactDecimalField(max_digits=14,decimal_places=2)
    version = models.PositiveIntegerField(default=1)
    currency = models.CharField(max_length=3,default='EUR')
    invoice_number = models.CharField(max_length=150,blank=True)
    invoice_date = models.DateField(null=True,blank=True)
    service_date = models.DateField(null=True,blank=True)
    positions = models.JSONField(default=list)
    request_key = models.UUIDField(default=uuid.uuid4,unique=True)
    @property
    def remainder(self): return self.amount-(self.allocations.aggregate(s=Sum('amount'))['s'] or Decimal('0'))

class Allocation(Base):
    source = models.ForeignKey(CostSource,on_delete=models.CASCADE,related_name='allocations')
    project = models.ForeignKey(Project,on_delete=models.PROTECT,related_name='allocations')
    entry = models.ForeignKey(Entry,null=True,blank=True,on_delete=models.PROTECT,related_name='allocations')
    step = models.ForeignKey(Step,null=True,blank=True,on_delete=models.SET_NULL)
    position_ref = models.CharField(max_length=50,blank=True)
    amount = ExactDecimalField(max_digits=14,decimal_places=2)

class Part(Base):
    source_document = models.ForeignKey(Document,null=True,blank=True,on_delete=models.PROTECT)
    source_position = models.PositiveIntegerField(null=True,blank=True)
    version = models.PositiveIntegerField(default=1)
    project = models.ForeignKey(Project,on_delete=models.CASCADE,related_name='parts')
    name = models.CharField(max_length=150)
    number = models.CharField(max_length=100,blank=True)
    manufacturer = models.CharField(max_length=100,blank=True)
    state = models.CharField(max_length=20,default='vorhanden')
    notes = models.TextField(blank=True)
    documents = models.ManyToManyField(Document,blank=True,related_name='parts')

class PartEvent(Base):
    part = models.ForeignKey(Part,on_delete=models.PROTECT,related_name='events')
    entry = models.ForeignKey(Entry,on_delete=models.PROTECT,related_name='part_events')
    action = models.CharField(max_length=20)
    date = models.DateField()
    snapshot = models.JSONField(default=dict)

class Task(Base):
    version = models.PositiveIntegerField(default=1)
    project = models.ForeignKey(Project,on_delete=models.CASCADE,related_name='tasks')
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name='tasks')
    entry = models.ForeignKey(Entry,null=True,blank=True,on_delete=models.SET_NULL)
    title = models.CharField(max_length=200)
    due = models.DateField(null=True,blank=True)
    done = models.BooleanField(default=False)
    @property
    def overdue(self): return not self.done and self.due and self.due<timezone.localdate()

class Job(Base):
    control = models.OneToOneField('ControlTask', null=True, blank=True, on_delete=models.CASCADE, related_name='job')
    document = models.OneToOneField(Document,null=True,blank=True,on_delete=models.CASCADE,related_name='job')
    asset = models.OneToOneField(Asset,null=True,blank=True,on_delete=models.CASCADE,related_name='job')
    kind = models.CharField(max_length=20,default='document')
    stage = models.CharField(max_length=20,default='upload')
    status = models.CharField(max_length=20,default='pending',db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    available = models.DateTimeField(default=timezone.now)
    leased_until = models.DateTimeField(null=True,blank=True)
    lease_token = models.UUIDField(null=True,blank=True)
    error = models.CharField(max_length=500,blank=True)
    updated = models.DateTimeField(auto_now=True)
    export = models.OneToOneField('ExportRequest',null=True,blank=True,on_delete=models.CASCADE,related_name='job')

class AIUsage(Base):
    input_rate = models.DecimalField(max_digits=12, decimal_places=6, null=True)
    output_rate = models.DecimalField(max_digits=12, decimal_places=6, null=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    document = models.ForeignKey(Document,null=True,blank=True,on_delete=models.SET_NULL)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    estimated_usd = ExactDecimalField(max_digits=12,decimal_places=6)
    state = models.CharField(max_length=20,default='reserved')
    model = models.CharField(max_length=100)

class ImportBatch(Owned):
    proposed_fields = models.JSONField(default=dict)
    warnings = models.JSONField(default=list)
    source_asset = models.ForeignKey(Asset,null=True,blank=True,on_delete=models.PROTECT)
    sha256 = models.CharField(max_length=64)
    source_name = models.CharField(max_length=250)
    rows = models.JSONField(default=list)
    project = models.ForeignKey(Project,on_delete=models.PROTECT)
    confirmed = models.BooleanField(default=False)
    class Meta: constraints = [models.UniqueConstraint(fields=['owner','sha256'],name='import_identity')]

class ExportRequest(Owned):
    kind = models.CharField(max_length=10,default='zip')
    project = models.ForeignKey(Project,null=True,blank=True,on_delete=models.CASCADE)
    entry = models.ForeignKey(Entry,null=True,blank=True,on_delete=models.CASCADE)
    status = models.CharField(max_length=20,default='pending')
    path = models.CharField(max_length=250,blank=True)
    error = models.CharField(max_length=500,blank=True)


class DocumentLink(Base):
    document = models.ForeignKey(Document,on_delete=models.CASCADE,related_name='links')
    project = models.ForeignKey(Project,on_delete=models.PROTECT)
    entry = models.ForeignKey(Entry,null=True,blank=True,on_delete=models.CASCADE)
    identity = models.UUIDField(unique=True)


class DocumentPage(Base):
    document = models.ForeignKey(Document,on_delete=models.CASCADE,related_name='pages')
    asset = models.ForeignKey(Asset,on_delete=models.PROTECT,related_name='document_pages')
    position = models.PositiveIntegerField()
    class Meta:
        ordering = ['position']
        constraints = [models.UniqueConstraint(fields=['document','position'],name='document_page_position')]


class MaintenanceRule(Base):
    project = models.ForeignKey(Project,on_delete=models.CASCADE,related_name='maintenance_rules')
    title = models.CharField(max_length=180)
    interval_months = models.PositiveIntegerField(null=True,blank=True)
    interval_km = models.DecimalField(max_digits=14,decimal_places=3,null=True,blank=True)
    base_date = models.DateField(null=True,blank=True)
    base_meter = models.DecimalField(max_digits=14,decimal_places=3,null=True,blank=True)
    active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    title_template = models.OneToOneField(TitleTemplate, null=True, blank=True, on_delete=models.SET_NULL, related_name='maintenance_rule')
    interval_days = models.PositiveIntegerField(null=True, blank=True)
    meter_unit = models.CharField(max_length=20, default='km', blank=True)


class MaintenanceCompletion(Base):
    rule = models.ForeignKey(MaintenanceRule,on_delete=models.CASCADE,related_name='completions')
    entry = models.ForeignKey(Entry,on_delete=models.PROTECT)
    snapshot = models.JSONField(default=dict)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['rule','entry'],name='maintenance_completion_once')]


class OwnerInvitation(Base):
    user = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=64,unique=True)
    expires = models.DateTimeField()
    used = models.DateTimeField(null=True,blank=True)


class ArchiveReference(Owned):
    """Only the identity needed to safely reuse a receipt kept in Paperless."""
    sha256 = models.CharField(max_length=64)
    instance = models.CharField(max_length=200)
    paperless_id = models.PositiveIntegerField(null=True,blank=True)
    submission_key = models.UUIDField()
    task_id = models.CharField(max_length=100,blank=True)
    submitted_at = models.DateTimeField(null=True,blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['owner','instance','sha256'],name='archive_reference_identity')]


from .control_models import Preferences, SessionDevice, LoginAttempt, ControlTask, ManagedBackup, ErrorEvent, SystemBudget


class KnowledgeItem(Owned):
    repair_library = models.ForeignKey('RepairLibrary', null=True, blank=True, on_delete=models.SET_NULL, related_name='contributions')
    repair_document_id = models.CharField(max_length=24, blank=True)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name='knowledge_items')
    entry = models.ForeignKey(Entry, null=True, blank=True, on_delete=models.SET_NULL, related_name='knowledge_items')
    source_document = models.ForeignKey(Document, null=True, blank=True, on_delete=models.SET_NULL)
    title = models.CharField(max_length=180)
    subject = models.CharField(max_length=200, blank=True)
    content = models.TextField(max_length=16000)
    value = models.CharField(max_length=100, blank=True)
    unit = models.CharField(max_length=30, blank=True)
    kind = models.CharField(max_length=20, choices=[('observation','Dokumentierte Durchführung'),('reference','Sollwert / Referenz'),('note','Wissensnotiz')], default='note')
    source_quote = models.TextField(max_length=4000, blank=True)
    source_reference = models.CharField(max_length=1000, blank=True)
    source_url = models.URLField(max_length=2000, blank=True)
    youtube_id = models.CharField(max_length=11, blank=True)
    source_fingerprint = models.CharField(max_length=64, blank=True)
    state = models.CharField(max_length=20, choices=[('review','Prüfung offen'),('approved','Geprüft'),('rejected','Verworfen')], default='review')
    version = models.PositiveIntegerField(default=1)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    updated = models.DateTimeField(default=timezone.now)
    class Meta:
        indexes = [models.Index(fields=['owner','project','state'], name='knowledge_owner_project_state')]
        constraints = [models.UniqueConstraint(fields=['owner','source_fingerprint'], condition=~models.Q(source_fingerprint=''), name='knowledge_source_once')]

class KnowledgeVideo(Owned):
    entry = models.ForeignKey(Entry, on_delete=models.PROTECT, related_name='knowledge_videos')
    name = models.CharField(max_length=200)
    size = models.PositiveBigIntegerField()
    offset = models.PositiveBigIntegerField(default=0)
    state = models.CharField(max_length=20, default='uploading')
    error = models.CharField(max_length=300, blank=True)
    duration = models.FloatField(null=True)
    updated = models.DateTimeField(default=timezone.now)


class RepairLibrary(Owned):
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name='repair_libraries')
    title = models.CharField(max_length=180)
    vehicle = models.CharField(max_length=200)
    documents = models.PositiveIntegerField(default=0)
