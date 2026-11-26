import logging


class SafeErrorHandler(logging.Handler):
    def emit(self,record):
        try:
            from .account_events import SafeErrorHandler as ReadyHandler
            ReadyHandler.emit(self,record)
        except Exception:
            pass
