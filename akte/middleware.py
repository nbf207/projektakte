from django.conf import settings
from django.http import HttpResponse, JsonResponse
from .operations import OperationBusy, operation_gate


class MaintenanceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method in ('GET', 'HEAD', 'OPTIONS') or request.path_info in ('/login/', '/anmelden/'):
            try:
                return self.get_response(request)
            except OperationBusy:
                return self.busy(request)
        if (settings.DATA_DIR / '.maintenance').exists():
            return self.busy(request)
        # Synchronous external Gemini guidance acquires @guarded only for its short
        # budget reservation/recording writes so slow AI calls never lock the app.
        if request.path_info.startswith('/wissen/bibliothek/') and request.path_info.endswith('/suchhilfe/'):
            try:
                return self.get_response(request)
            except OperationBusy:
                return self.busy(request)
        try:
            # Includes session/auth writes and all in-flight POSTs. Backups use
            # the short snapshot lock, rather than trusting a flag.
            with operation_gate():
                return self.get_response(request)
        except OperationBusy:
            return self.busy(request)

    @staticmethod
    def busy(request=None):
        msg = 'Sicherung oder Schreibvorgang läuft. Eingaben bitte behalten und erneut senden.'
        accept = request.META.get('HTTP_ACCEPT', '') if request is not None else ''
        is_json = 'application/json' in accept or (request is not None and request.headers.get('X-Requested-With') == 'XMLHttpRequest')
        response = JsonResponse({'error': msg}, status=503) if is_json else HttpResponse(msg, status=503)
        response['Retry-After'] = '5'
        return response
