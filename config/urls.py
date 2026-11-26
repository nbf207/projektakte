from django.contrib.auth import views as auth
from django.urls import path
from akte import views as v
from akte.security import SecureLoginView
from akte import capture_views, share_views
from akte.purge_views import purge
from akte.maintenance_views import maintenance
from akte.categories import manage_categories
from akte.settings_views import settings_view
from akte.feedback import save_feedback
from akte.draft_deletion import delete_draft, delete_document_draft
from akte import control_views
from akte.handover import handover
from akte import knowledge_views, knowledge_video, repair_library, repair_advice
urlpatterns=[
    path('wissen/bibliothek/<uuid:pk>/suchhilfe/',repair_advice.guidance,name='repair_advice'),
    path('wissen/bibliothek/<uuid:pk>/',repair_library.catalogue,name='repair_library'),
    path('wissen/bibliothek/<uuid:pk>/dokument/<str:identity>/',repair_library.document,name='repair_document'),
    path('wissen/bibliothek/<uuid:pk>/quelle/<str:identity>/',repair_library.resource_detail,name='repair_resource'),
    path('wissen/bibliothek/<uuid:pk>/datei/<str:identity>/',repair_library.file_download,name='repair_file'),
    path('wissen/',knowledge_views.catalogue,name='knowledge'),
    path('wissen/neu/',knowledge_views.edit,name='knowledge_new'),
    path('wissen/<uuid:pk>/',knowledge_views.detail,name='knowledge_detail'),
    path('wissen/<uuid:pk>/bearbeiten/',knowledge_views.edit,name='knowledge_edit'),
    path('vorgaenge/<uuid:entry_id>/video/',knowledge_video.start,name='knowledge_video_start'),
    path('wissen/video/<uuid:pk>/chunk/',knowledge_video.chunk,name='knowledge_video_chunk'),
    path('wissen/video/<uuid:pk>/status/',knowledge_video.status,name='knowledge_video_status'),
    path('wissen/video/<uuid:pk>/abbrechen/',knowledge_video.cancel,name='knowledge_video_cancel'),
    path('wissen/video/<uuid:pk>/datei/',knowledge_video.download,name='knowledge_video_download'),
    path('einstellungen/',settings_view,name='settings'),
    path('einstellungen/aktion/',control_views.control_action,name='control_action'),
    path('einstellungen/status/',control_views.settings_status,name='settings_status'),
    path('einstellungen/sicherungen/<uuid:pk>/download/',control_views.backup_download,name='backup_download'),
    path('einstellungen/feedback/<str:key>/<str:identity>/bild/',control_views.feedback_image,name='feedback_image'),
    path('einstellungen/uebergabe/',handover,name='handover'),
    path('einstellungen/<str:section>/',control_views.settings_center,name='settings_center'),
    path('feedback/',save_feedback,name='feedback'),
    path('entwuerfe/<uuid:pk>/loeschen/',delete_draft,name='draft_delete'),
    path('belege/<uuid:pk>/entwurf/loeschen/',delete_document_draft,name='document_draft_delete'),
    path('kategorien/',manage_categories,name='categories'),
    path('manifest.json',share_views.manifest,name='manifest'),
    path('sw.js',share_views.service_worker,name='service_worker'),
    path('teilen/',share_views.inbox,name='share'),
    path('teilen/speichern/',share_views.save,name='share_save'),
    path('erfassen/',capture_views.upload,name='capture_upload'),
    path('erfassen/<uuid:pk>/',capture_views.detail,name='capture_detail'),
    path('papierkorb/<str:kind>/<uuid:pk>/endgueltig/',purge,name='purge'),
    path('wartungsplan/',maintenance,name='maintenance'),path('wartungsplan/<uuid:pk>/',maintenance,name='maintenance_edit'),path('projekte/<uuid:project_id>/wartungsplan/',maintenance,name='project_maintenance'),
    path('',v.home,name='home'),path('health/',v.health,name='health'),
    path('login/',SecureLoginView.as_view(template_name='login.html'),name='login'),path('anmelden/',SecureLoginView.as_view(template_name='login.html'),name='legacy_login'),path('abmelden/',auth.LogoutView.as_view(),name='logout'),
    path('projekte/neu/',v.project_edit,name='project_new'),path('projekte/<uuid:pk>/',v.project_view,name='project'),path('projekte/<uuid:pk>/bearbeiten/',v.project_edit,name='project_edit'),
    path('projekte/<uuid:project_id>/eintrag/',v.entry_edit,name='entry_new'),path('projekte/<uuid:project_id>/entwurf/',v.draft_save,name='draft_save'),
    path('vorgaenge/<uuid:pk>/',v.entry_detail,name='entry'),path('vorgaenge/<uuid:pk>/bearbeiten/',v.entry_edit,name='entry_edit'),path('vorgaenge/<uuid:pk>/verbinden/',v.entry_merge,name='entry_merge'),path('vorgaenge/<uuid:pk>/papierkorb/',v.entry_delete,name='entry_delete'),path('papierkorb/',v.trash,name='trash'),
    path('vorgaenge/<uuid:entry_id>/schritt/',v.step_edit,name='step_new'),path('vorgaenge/<uuid:entry_id>/schritt/<uuid:pk>/',v.step_edit,name='step_edit'),path('vorgaenge/<uuid:entry_id>/fotos/',v.photo_action,name='photo_action'),path('datei/<uuid:pk>/',v.media_file,name='media'),
    path('projekte/<uuid:project_id>/teil/',v.part_edit,name='part_new'),path('projekte/<uuid:project_id>/teil/<uuid:pk>/',v.part_edit,name='part_edit'),path('vorgaenge/<uuid:entry_id>/einbau/',v.part_event,name='part_event'),
    path('aufgaben/',v.tasks,name='tasks'),path('aufgaben/<uuid:pk>/',v.tasks,name='task_edit'),path('projekte/<uuid:project_id>/aufgabe/',v.tasks,name='task_new'),
    path('eingang/',v.inbox,name='inbox'),path('beleg/neu/',v.document_upload,name='document_upload'),path('projekte/<uuid:project_id>/beleg/',v.document_upload,name='project_upload'),path('vorgaenge/<uuid:entry_id>/beleg/',v.document_upload,name='entry_upload'),
    path('belege/<uuid:pk>/',v.review,name='review'),path('belege/<uuid:pk>/aktion/',v.document_action,name='document_action'),path('belege/<uuid:pk>/status/',v.document_status,name='document_status'),
    path('belege/<uuid:pk>/papierkorb/',v.document_delete,name='document_delete'),path('belege/<uuid:pk>/projekt/',v.document_route,name='document_route'),
    path('kosten/<uuid:pk>/',v.cost_edit,name='cost'),path('projekte/<uuid:project_id>/ausgabe/',v.cost_edit,name='cost_new'),path('suche/',v.search,name='search'),path('system/',v.system_view,name='system'),
    path('projekte/<uuid:project_id>/import/',v.import_view,name='import'),path('projekte/<uuid:project_id>/import/<uuid:pk>/',v.import_view,name='import_batch'),
    path('belege/<uuid:pk>/original/',v.document_original,name='document_original'),path('export/',v.export_request,name='export'),path('projekte/<uuid:project_id>/export/',v.export_request,name='project_export'),path('vorgaenge/<uuid:entry_id>/export/',v.export_request,name='entry_export'),path('export/<uuid:pk>/download/',v.export_download,name='export_download'),
    path('belege/<uuid:pk>/entwurf/',v.review_draft_save,name='review_draft'),
]
