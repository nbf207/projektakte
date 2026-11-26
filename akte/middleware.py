from django.conf import settings
from django.http import HttpResponse
from .operations import OperationBusy, operation_gate


class MaintenanceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method in ('GET', 'HEAD', 'OPTIONS') or request.path_info in ('/login/', '/anmelden/'):
            return self.get_response(request)
        if (settings.DATA_DIR / '.maintenance').exists():
            return self.busy()
        # Synchronous external Gemini guidance acquires @guarded only for its short
        # budget reservation/recording writes so slow AI calls never lock the app.
        if request.path_info.startswith('/wissen/bibliothek/') and request.path_info.endswith('/suchhilfe/'):
            return self.get_response(request)
        try:
            # Includes session/auth writes and all in-flight POSTs. Backups use
            # exactly the same process-safe lock, rather than trusting a flag.
            with operation_gate():
                return self.get_response(request)
        except OperationBusy:
            return self.busy()

    @staticmethod
    def busy():
        response = HttpResponse('Sicherung oder Schreibvorgang läuft. Eingaben bitte behalten und erneut senden.', status=503)
        response['Retry-After'] = '5'
        return response
