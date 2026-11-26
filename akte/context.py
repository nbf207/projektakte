from django.conf import settings
def common(request):
    route = getattr(getattr(request, 'resolver_match', None), 'url_name', '')
    groups = {
        'knowledge': {'knowledge','knowledge_new','knowledge_edit','knowledge_detail','repair_library','repair_document','repair_resource','repair_file'},
        'inbox': {'inbox', 'document_upload', 'project_upload', 'entry_upload', 'review', 'document_action', 'document_route', 'document_original'},
        'maintenance': {'maintenance', 'maintenance_edit', 'project_maintenance'},
        'tasks': {'tasks', 'task_edit', 'task_new'},
        'settings': {'settings','categories','settings_center','control_action','backup_download','feedback_image','handover'},
        'search': {'search'},
        'system': {'system', 'export', 'project_export', 'entry_export', 'export_download'},
        'trash': {'trash', 'purge', 'document_delete', 'entry_delete'},
    }
    nav_section = next((section for section, routes in groups.items() if route in routes), 'projects')
    keep_audio=False
    if getattr(request,'user',None) and request.user.is_authenticated:
        from .preferences import preferences
        keep_audio=preferences(request.user).keep_audio
    return {'keep_audio_default':keep_audio,'nav_section':nav_section,'demo_mode':settings.DEMO_MODE,'app_version':settings.APP_VERSION,'ai_original_limit_mb':settings.GEMINI_ORIGINAL_MAX_BYTES//(1024*1024),'feedback_widget_enabled':settings.FEEDBACK_WIDGET_ENABLED}
