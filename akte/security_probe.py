"""Small explicit security probe; synthetic account only, never the standard test suite."""
import argparse
import http.client
import io
import json
import os
import socket
import sys
import time
import urllib.parse
from http.cookies import SimpleCookie

checks=[]

def check(label, condition):
    checks.append(bool(condition))
    print(('PASS ' if condition else 'FAIL ')+label,flush=True)


def network(state):
    host=state['host'];domain=state.get('domain') or host
    def call(path,method='GET',body=None,jar=None,ip='198.51.100.111',extra=None):
        headers={'Host':domain,'X-Forwarded-Proto':'https','CF-Connecting-IP':ip,'User-Agent':'Projektakte-Security-Probe'}
        if jar:headers['Cookie']='; '.join(k+'='+v for k,v in jar.items() if v)
        if body is not None:headers['Content-Type']='application/x-www-form-urlencoded'
        if extra:headers.update(extra)
        client=http.client.HTTPConnection(host,8000,timeout=12)
        try:
            client.request(method,path,body=body,headers=headers)
            response=client.getresponse();data=response.read(1024*1024)
            result=(response.status,dict(response.getheaders()),data)
            if jar is not None:
                for key,value in response.getheaders():
                    if key.lower()=='set-cookie':
                        cookie=SimpleCookie();cookie.load(value)
                        for name,morsel in cookie.items():jar[name]=morsel.value
            return result
        finally:client.close()
    def raw(path,headers,payload=b''):
        with socket.create_connection((host,8000),timeout=5) as stream:
            stream.settimeout(5)
            text='POST '+path+' HTTP/1.1\r\nHost: '+domain+'\r\nConnection: close\r\n'+headers+'\r\n'
            stream.sendall(text.encode()+payload)
            data=b''
            while b'\r\n' not in data:
                chunk=stream.recv(4096)
                if not chunk:break
                data+=chunk
            return int(data.split(b' ',2)[1])
    def submit(path,values,jar,ip='198.51.100.111'):
        values={**values,'csrfmiddlewaretoken':jar.get('csrftoken','')}
        return call(path,'POST',urllib.parse.urlencode(values),jar,ip,{'Origin':'https://'+domain})
    protected=['/','/eingang/','/einstellungen/status/','/suche/','/health/',
      '/media/test.jpg','/backups/projektakte.zip','/feedback/test.json',
      '/datei/'+state['asset']+'/', '/belege/'+state['document']+'/original/',
      '/einstellungen/sicherungen/00000000-0000-0000-0000-000000000001/download/']
    for path in protected:
        status,headers,_=call(path)
        check('private route '+path.split('/')[1],status==302 and headers.get('Location','').startswith('/login/'))
    check('admin hidden',call('/admin/')[0]==404 and call('/admin/login/')[0]==404)
    for path in ('/static/../../data/db.sqlite3','/static/%2e%2e/data/db.sqlite3','/datei/../../backups/test.zip','/teilen/not-a-token/media/'):
        status,_,body=call(path)
        check('traversal / token isolation',status in (302,404) and not body.startswith(b'SQLite format'))
    status,headers,body=call('/login/')
    check('login + security headers',status==200 and headers.get('X-Robots-Tag')=='noindex, nofollow' and headers.get('X-Content-Type-Options')=='nosniff' and str(state.get('private_username') or 'example-owner').encode() not in body)
    for path in ('/manifest.json','/sw.js','/static/style.css'):
        status,headers,body=call(path)
        check('public PWA asset '+path,status==200 and headers.get('X-Robots-Tag')=='noindex, nofollow')
    for path in ('/','/unbekannt/','/api/anywhere/','/static/style.css'):
        check('early 64 KB body limit '+path,raw(path,'Content-Length: 65537\r\nExpect: 100-continue\r\n')==413)
    check('forged session cannot bypass body limit',raw('/unbekannt/','Content-Length: 65537\r\nCookie: sessionid='+'a'*32+'\r\n')==413)
    check('anonymous chunked rejected before body',raw('/unbekannt/','Transfer-Encoding: chunked\r\nExpect: 100-continue\r\n')==400)
    jar={'sessionid':state['sessions'][0]}
    body=b'x'*70000
    check('authenticated large body allowed',raw('/security-probe/not-found/','Content-Length: 70000\r\nCookie: sessionid='+jar['sessionid']+'\r\nX-Forwarded-Proto: https\r\n',body)==404)
    chunk=hex(len(body))[2:].encode()+b'\r\n'+body+b'\r\n0\r\n\r\n'
    check('authenticated chunked body allowed',raw('/security-probe/not-found/','Transfer-Encoding: chunked\r\nCookie: sessionid='+jar['sessionid']+'\r\nX-Forwarded-Proto: https\r\n',chunk)==404)
    check('hard body limit also for sessions',raw('/unbekannt/','Content-Length: 57671681\r\nCookie: sessionid='+jar['sessionid']+'\r\nExpect: 100-continue\r\n')==413)
    check('public token POST denied',call('/teilen/not-a-token/','POST',b'x')[0]==405)
    # Same username: source A is throttled, source B must still log in.
    rate={};ip='198.51.100.177'
    call('/login/',jar=rate,ip=ip)
    statuses=[]
    for i in range(8):
        statuses.append(submit('/login/',{'username':state['username'],'password':'incorrect-probe-password'},rate,ip)[0]);time.sleep(.55)
    check('eight failed logins allowed; ninth blocked',all(code==200 for code in statuses) and submit('/login/',{'username':state['username'],'password':'incorrect-probe-password'},rate,ip)[0]==429)
    active={};call('/login/',jar=active)
    result=submit('/login/?next=https://evil.example/',{'username':state['username'],'password':state['password']},active)
    check('per-IP limit; no account lock + safe next',result[0]==302 and result[1].get('Location')=='/')
    check('CSRF enforced',call('/abmelden/','POST',b'action=logout',active)[0]==403)
    call('/einstellungen/konto/',jar=active)
    new_password=state['password']+'-changed'
    result=submit('/einstellungen/konto/',{'action':'password','old_password':state['password'],'new_password1':new_password,'new_password2':new_password},active)
    check('password changes keep current session',result[0]==302 and call('/',jar=active)[0]==200)
    check('password invalidates other sessions',call('/',jar={'sessionid':state['sessions'][1]})[0]==302)
    other={};call('/login/',jar=other)
    submit('/login/',{'username':state['username'],'password':new_password},other)
    result=submit('/einstellungen/konto/',{'action':'sessions'},active)
    check('other-device logout immediately effective',result[0]==302 and call('/',jar=other)[0]==302 and call('/',jar=active)[0]==200)
    check('logout GET forbidden',call('/abmelden/',jar=active)[0]==405)
    old=active.copy();result=submit('/abmelden/',{},active)
    check('logout POST invalidates session',result[0]==302 and call('/',jar=old)[0]==302)
    check('revoked session cannot send large bodies',raw('/unbekannt/','Content-Length: 65537\r\nCookie: sessionid='+old['sessionid']+'\r\n')==413)


def internals(state):
    os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
    import django;django.setup()
    import tempfile,zlib,struct,hashlib,zipfile
    from pathlib import Path
    from unittest.mock import patch
    from django.conf import settings
    from django.core.exceptions import ValidationError
    from django.contrib.auth import get_user_model
    from django.core.files.uploadedfile import SimpleUploadedFile
    from .request_limits import authenticated_cookie
    from .proxy_boundary import ProxyHeaderBoundary
    from .mail_ingest import BoundedIMAP,MAX_MAIL_BYTES
    from .pdf_limits import validate_pdf
    from .proposal_schema import validate_ai_content
    from .billing import validate_proposal
    from .http_limits import read_bounded
    from .templatetags.akte import rich
    from .exports import verify_archive
    from PIL import Image
    def rejects(function,*args):
        try:function(*args)
        except (ValidationError,ValueError):return True
        return False
    check('read-only session recognition',authenticated_cookie('sessionid='+state['sessions'][0]))
    boundary=ProxyHeaderBoundary(lambda e,s: [])
    boundary.proxy='synthetic-tunnel'
    with patch('socket.getaddrinfo',return_value=[(None,None,None,None,('192.0.2.1',0))]):check('proxy initial DNS trust',boundary.trusts('192.0.2.1'))
    with patch('socket.getaddrinfo',return_value=[(None,None,None,None,('192.0.2.2',0))]):check('proxy new IP refresh without restart',boundary.trusts('192.0.2.2') and not boundary.trusts('192.0.2.1'))
    boundary.expires=0
    with patch('socket.getaddrinfo',side_effect=OSError()):check('proxy DNS failure closes trust',not boundary.trusts('192.0.2.2'))
    import imaplib
    try:BoundedIMAP.__new__(BoundedIMAP).read(MAX_MAIL_BYTES+1)
    except imaplib.IMAP4.abort:check('IMAP oversized literal before read',True)
    else:check('IMAP oversized literal before read',False)
    for content in ('<script>alert(1)</script>','[x](javascript:alert(1))','[x](data:text/html,x)'):
        check('AI active content rejected',rejects(validate_ai_content,{'description':content}))
    from .capture_ai import validate_capture
    from .adapters import demo_proposal
    proposal=demo_proposal('synthetic')
    proposal['project_assignment']={'project_id':'00000000-0000-0000-0000-000000000002','confidence':'high','reason':'synthetic'}
    result={'transcript':'','category':'notiz','is_receipt':False,'receipt_files':[],'open_entry':{'id':None,'confidence':'none','reason':None},'proposal':proposal}
    check('AI cannot select a foreign project',rejects(validate_capture,result,{'routing_projects':[]}))
    check('AI cannot change model configuration',rejects(validate_proposal,{'model':'different-model'}))
    rendered=str(rich('<script>alert(1)</script>[x](javascript:alert(1))'))
    check('Markdown defense remains active','<script' not in rendered and 'href="javascript:' not in rendered)
    import httpx
    for headers in ({'Content-Length':'999999999'},{'Content-Encoding':'gzip'}):
        response=httpx.Response(200,headers=headers,stream=httpx.ByteStream(b''))
        check('outbound response resource cap',rejects(read_bounded,response,65536))
    with tempfile.TemporaryDirectory(dir=settings.FILE_UPLOAD_TEMP_DIR,prefix='security-probe-') as folder:
        directory=Path(folder)
        from .demo import synthetic_pdf
        good=directory/'valid.pdf';good.write_bytes(synthetic_pdf('Synthetischer Sicherheitstest'))
        check('bounded PDF normal path',validate_pdf(good)['count']==1)
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject,NameObject,TextStringObject,DecodedStreamObject
        writer=PdfWriter();writer.add_blank_page(100,100)
        writer._root_object[NameObject('/AcroForm')]=DictionaryObject({NameObject('/XFA'):TextStringObject('synthetic')})
        xfa=directory/'xfa.pdf';writer.write(xfa)
        check('XFA resource-risk path rejected',rejects(validate_pdf,xfa))
        writer=PdfWriter();page=writer.add_blank_page(100,100)
        stream=DecodedStreamObject();stream.set_data(b' '* (9*1024*1024))
        page[NameObject('/Contents')]=writer._add_object(stream.flate_encode())
        bomb=directory/'compressed.pdf';writer.write(bomb)
        check('PDF stream decompression limit',rejects(validate_pdf,bomb))
        def png_chunk(kind,value):return struct.pack('>I',len(value))+kind+value+struct.pack('>I',zlib.crc32(kind+value)&0xffffffff)
        png=b'\x89PNG\r\n\x1a\n'+png_chunk(b'IHDR',struct.pack('>IIBBBBB',5000,5000,8,2,0,0,0))+png_chunk(b'IEND',b'')
        try:Image.open(io.BytesIO(png),formats=('PNG',))
        except (Image.DecompressionBombError,Image.DecompressionBombWarning):check('Pillow pixel bomb before decode',True)
        else:check('Pillow pixel bomb before decode',False)
        archive=directory/'traversal.zip'
        payload=b'synthetic'
        with zipfile.ZipFile(archive,'w') as out:
            out.writestr('../escape',payload);out.writestr('manifest.json',json.dumps({'sha256':{'../escape':hashlib.sha256(payload).hexdigest()}}))
        check('archive traversal blocked',rejects(verify_archive,archive))
    # Direct socket peer cannot become the trusted tunnel by supplying headers.
    client=http.client.HTTPConnection('127.0.0.1',8000,timeout=5)
    try:
        client.request('GET','/login/',headers={'Host':settings.PUBLIC_HOST,'X-Forwarded-Proto':'https','CF-Connecting-IP':'198.51.100.200','X-Forwarded-For':'198.51.100.201'})
        response=client.getresponse()
        check('untrusted CF/proto headers cannot spoof tunnel',response.status==301)
    finally:client.close()
    from .adapters import paperless
    from .models import Document
    document=Document.objects.filter(paperless_id__isnull=False).first()
    if document:
        check('Paperless bounded live metadata read',paperless(document).read(document.paperless_id,document).get('id')==document.paperless_id)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--network',action='store_true');parser.add_argument('--internals',action='store_true');args=parser.parse_args()
    state=json.load(sys.stdin)
    try:
        if args.network:network(state)
        elif args.internals:internals(state)
        else:raise ValueError('Choose a probe mode.')
    except Exception as exc:
        check('probe completed ('+type(exc).__name__+')',False)
    print(f'{sum(checks)}/{len(checks)} checks passed.',flush=True)
    raise SystemExit(0 if checks and all(checks) else 1)


if __name__=='__main__':main()
