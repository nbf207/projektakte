"""Header-stage limits before Waitress reads a body; authenticated uploads retain their limit."""
import datetime
import hmac
import re
import sqlite3
from contextlib import closing
from http.cookies import CookieError, SimpleCookie
from pathlib import Path

from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.utils.crypto import salted_hmac
from django.utils.dateparse import parse_datetime
from waitress.channel import HTTPChannel
from waitress.parser import HTTPRequestParser
from waitress.utilities import BadRequest, RequestEntityTooLarge

PREAUTH_LIMIT = 64 * 1024
HARD_BODY_LIMIT = 55 * 1024 * 1024


def authenticated_cookie(cookie_header, *, demo=False):
    """Read-only, fail-fast validation: expiry, active user, password hash and logout epoch.

    This runs only for large/chunked bodies, on the server header boundary. Never
    trust cookie presence alone or cache positive results past a password/logout.
    """
    if not cookie_header or len(cookie_header) > 16384:
        return False
    try:
        cookie = SimpleCookie()
        cookie.load(cookie_header)
        item = cookie.get('projektakte_demo' if demo else settings.SESSION_COOKIE_NAME)
        key = item.value if item else ''
        if not re.fullmatch(r'[a-z0-9]{32}', key):
            return False
        uri = Path(settings.DATABASES['demo' if demo else 'default']['NAME']).resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as database:
            row = database.execute('SELECT session_data,expire_date FROM django_session WHERE session_key=?', (key,)).fetchone()
            if not row:
                return False
            expires = parse_datetime(row[1])
            if expires is None:
                return False
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=datetime.timezone.utc)
            if expires <= datetime.datetime.now(datetime.timezone.utc):
                return False
            data = SessionStore().decode(row[0])
            if not isinstance(data,dict):return False
            if data.get('_auth_user_backend') != 'django.contrib.auth.backends.ModelBackend':
                return False
            user_id = int(data.get('_auth_user_id', 0))
            row = database.execute('SELECT u.password,u.is_active,COALESCE(p.auth_epoch,0) FROM auth_user u LEFT JOIN akte_preferences p ON p.owner_id=u.id WHERE u.id=?', (user_id,)).fetchone()
            if not row or not row[1] or data.get('_account_epoch', 0) != row[2]:
                return False
            salt = 'django.contrib.auth.models.AbstractBaseUser.get_session_auth_hash'
            return any(hmac.compare_digest(str(data.get('_auth_user_hash','')), salted_hmac(salt, row[0], secret=secret, algorithm='sha256').hexdigest())
                       for secret in (settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS))
    except (CookieError, ValueError, TypeError, sqlite3.Error):
        return False


class PreAuthRequestParser(HTTPRequestParser):
    def parse_header(self, header_plus):
        super().parse_header(header_plus)
        # Overwrite any spoofed marker; Waitress normally discards Transfer-Encoding.
        self.headers['X_PROJEKTAKTE_CHUNKED'] = '1' if self.chunked else '0'
        if self.content_length > min(HARD_BODY_LIMIT,self.adj.max_request_body_size):
            self.error=RequestEntityTooLarge('Request exceeds hard body limit.')
            self.completed=True
            self.expect_continue=False
            if self.body_rcv is not None:self.body_rcv.getfile().close()
            self.body_rcv=None
            return
        is_demo = self.path.startswith('/demo/')
        if is_demo and (self.chunked or self.content_length > 2 * 1024 * 1024):
            self.error = RequestEntityTooLarge('Demo limit: 2 MB, no chunked uploads.')
            self.completed = True
            self.expect_continue = False
            if self.body_rcv is not None: self.body_rcv.getfile().close()
            self.body_rcv = None
            return
        if self.content_length > PREAUTH_LIMIT or self.chunked:
            if not authenticated_cookie(self.headers.get('COOKIE', ''), demo=is_demo):
                self.error = BadRequest('Chunked requires authentication.') if self.chunked else RequestEntityTooLarge('Anonymous request exceeds 64 KB.')
                self.completed = True
                self.expect_continue = False
                if self.body_rcv is not None:
                    self.body_rcv.getfile().close()
                self.body_rcv = None


def install_request_limits():
    # Pinned Waitress 3.0.2 exposes parser_class on its channel; covered by the
    # live raw-HTTP probe, including Expect: 100-continue and body-less rejection.
    HTTPChannel.parser_class = PreAuthRequestParser
