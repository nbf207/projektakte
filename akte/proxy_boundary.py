"""Refresh tunnel DNS on TTL expiry and immediately on a new peer address."""
import os
import socket
import threading
import time


class ProxyHeaderBoundary:
    def __init__(self, application):
        self.application = application
        self.proxy = os.environ.get('TRUSTED_PROXY', '').strip()
        self.trusted = set()
        self.expires = 0
        self.negative = {}
        self.lock = threading.RLock()

    def trusts(self, peer):
        if not self.proxy:
            return False
        now = time.monotonic()
        with self.lock:
            if now >= self.expires or (peer not in self.trusted and now >= self.negative.get(peer, 0)):
                try:
                    addresses = {item[4][0] for item in socket.getaddrinfo(self.proxy, None)}
                except OSError:
                    # Fail closed; retry quickly after a transient Docker DNS outage.
                    self.trusted = set()
                    self.expires = now + 1
                else:
                    self.trusted = addresses
                    self.expires = now + 30
                self.negative = {ip:until for ip,until in self.negative.items() if until > now}
                if peer not in self.trusted:
                    if len(self.negative) >= 256:
                        self.negative.clear()
                    self.negative[peer] = now + 1
            return peer in self.trusted

    def __call__(self, environ, start_response):
        trusted = self.trusts(environ.get('REMOTE_ADDR', ''))
        environ['projektakte.trusted_proxy'] = trusted
        # Waitress leaves proxy headers untouched; this is the only trust boundary.
        for header in ('HTTP_X_FORWARDED_FOR','HTTP_X_FORWARDED_HOST','HTTP_X_FORWARDED_PORT',
                       'HTTP_X_FORWARDED_PREFIX','HTTP_FORWARDED'):
            environ.pop(header, None)
        proto = environ.get('HTTP_X_FORWARDED_PROTO')
        if trusted and proto in ('http', 'https'):
            environ['wsgi.url_scheme'] = proto
        else:
            environ.pop('HTTP_X_FORWARDED_PROTO', None)
            environ['wsgi.url_scheme'] = 'http'
        if not trusted:
            environ.pop('HTTP_CF_CONNECTING_IP', None)
        return self.application(environ, start_response)
