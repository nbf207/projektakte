"""Bound decoded network responses and never follow untrusted redirects."""
from urllib.parse import urlsplit
import httpx

MAX_JSON_BYTES=4*1024*1024


def checked_base(url, *, https=False):
    parts=urlsplit(url)
    if parts.scheme not in (('https',) if https else ('http','https')) or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('Ungültige serverseitige Dienstadresse.')
    return url.rstrip('/')


def read_bounded(response, limit):
    if response.headers.get('Content-Encoding','identity').casefold() not in ('','identity'):
        raise ValueError('Komprimierte Dienstantwort abgelehnt.')
    length=response.headers.get('Content-Length')
    if length and (not length.isdecimal() or int(length)>limit):raise ValueError('Dienstantwort zu groß.')
    raw=bytearray()
    for chunk in response.iter_bytes(chunk_size=65536):
        if len(raw)+len(chunk)>limit:raise ValueError('Dienstantwort zu groß.')
        raw.extend(chunk)
    return bytes(raw)


def bounded_request(client,method,url,*,limit=MAX_JSON_BYTES,**kwargs):
    with client.stream(method,url,**kwargs) as response:
        raw=read_bounded(response,limit)
        return httpx.Response(response.status_code,headers=response.headers,content=raw,request=response.request)
