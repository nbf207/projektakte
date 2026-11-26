"""Actual authenticated GET timings; credentials only through environment variables."""
import argparse
import http.cookiejar
import json
import math
import os
import statistics
import time
import urllib.parse
import urllib.request
from pathlib import Path


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--base',default='http://127.0.0.1:8010')
    p.add_argument('--path',action='append',default=[])
    p.add_argument('--samples',type=int,default=30)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if not 3<=args.samples<=1000: p.error('3–1000 Messungen je Seite verwenden.')
    username=os.environ.get('PROJEKTAKTE_BENCH_USER')
    password=os.environ.get('PROJEKTAKTE_BENCH_PASSWORD')
    if not username or not password: p.error('PROJEKTAKTE_BENCH_USER/PASSWORD lokal setzen, nicht protokollieren.')
    jar=http.cookiejar.CookieJar()
    opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    login=args.base.rstrip('/')+'/anmelden/'
    with opener.open(login,timeout=30) as response: response.read()
    csrf=next(cookie.value for cookie in jar if cookie.name=='csrftoken')
    body=urllib.parse.urlencode({'username':username,'password':password,'csrfmiddlewaretoken':csrf}).encode()
    request=urllib.request.Request(login,data=body,headers={'Referer':login})
    with opener.open(request,timeout=30) as response:
        if '/anmelden/' in response.url: raise RuntimeError('Anmeldung fehlgeschlagen.')
        response.read()
    results={}
    for relative in args.path or ['/']:
        if not relative.startswith('/') or relative.startswith('//'): p.error('Nur Pfade desselben Hosts verwenden.')
        samples=[]
        for unused in range(args.samples):
            start=time.perf_counter()
            with opener.open(args.base.rstrip('/')+relative,timeout=30) as response:
                if '/anmelden/' in response.url: raise RuntimeError('Sitzung abgelaufen.')
                response.read()
            samples.append((time.perf_counter()-start)*1000)
        results[relative]={'samples':len(samples),'p50_ms':round(statistics.median(samples),1),
                           'p95_ms':round(sorted(samples)[math.ceil(.95*len(samples))-1],1),
                           'max_ms':round(max(samples),1)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf8') as out:
        json.dump({'format':'projektakte-http-timings-v1','timings':results},out,indent=2)


if __name__=='__main__': main()
