"""Install beside the existing Pi guard; reuses its global lock and backup budget."""
import argparse
import json
import os
from pathlib import Path
import re
import server

APP=os.environ.get('PROJEKTAKTE_GUARD_WEB','projektakte-web-1')
WORKER=os.environ.get('PROJEKTAKTE_GUARD_WORKER','projektakte-worker-1')
PROJECT=os.environ.get('PROJEKTAKTE_GUARD_PROJECT','projektakte')
ARCHIVE_SERVICES=[name.strip() for name in os.environ.get('PROJEKTAKTE_GUARD_ARCHIVE_SERVICES','').split(',') if name.strip()]


def mark_failed(archive,reason):
    meta=archive.with_suffix('').with_suffix('.json')
    value=json.loads(meta.read_text())
    value.update(protected=True,update_result='failed',error=reason)
    server.atomic_json(meta,value)


def backup():
    with server.process_lock():
        archive,meta=server.backup(APP)
        server.check_health(list(reversed(server.POLICIES[APP]['group'])),timeout=600)
        server.status(phase='Bereit',last_result='Gemeinsame Sicherung geprüft')
        return {'archive':str(archive),'sha256':meta['sha256'],'bytes':meta['bytes'],'verified':meta['verified']}


def deploy(image):
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',image):
        raise RuntimeError('Geprüfte lokale Image-ID erforderlich; keine fremden Downloads.')
    candidate=json.loads(server.run(['docker','image','inspect',image]).stdout)[0]
    if candidate['Architecture']!='arm64' or candidate['Config'].get('Labels',{}).get('org.projektakte.application')!='projektakte':
        raise RuntimeError('Kein gekennzeichnetes ARM64-Projektakte-Image.')
    configured_compose=os.environ.get('PROJEKTAKTE_GUARD_COMPOSE_FILE')
    if not configured_compose:
        raise RuntimeError('PROJEKTAKTE_GUARD_COMPOSE_FILE konfigurieren.')
    compose=Path(configured_compose).resolve(strict=True)
    with server.process_lock():
        archive,meta=server.backup(APP,resume=False)
        cfg=compose;previous=cfg.read_text();changed=False
        try:
            doc=json.loads(previous)
            for name in ['web','worker']:
                doc['services'][name]['image']=image
            cfg.write_text(json.dumps(doc,indent=2)+'\n');changed=True
            args=['docker','compose','-p',PROJECT,'-f',str(cfg)]
            server.run(args+['config','--quiet'])
            server.run(args+['run','--rm','--no-deps','web','python','manage.py','migrate','--noinput'],timeout=600)
            server.run(args+['run','--rm','--no-deps','web','python','manage.py','check'],timeout=120)
            # Paperless/broker keep their exact previous containers and images.
            for name in ARCHIVE_SERVICES:
                server.run(['docker','start',name])
            server.run(args+['up','-d','--no-deps','--pull','never','web','worker'],timeout=180)
            server.check_health([*ARCHIVE_SERVICES,WORKER,APP],timeout=600)
            server.status(phase='Bereit',last_result='Projektakte lokal migriert und geprüft')
            return {'archive':str(archive),'image':image,'result':'deployed'}
        except Exception:
            if changed:
                cfg.write_text(previous)
            mark_failed(archive,'Projektakte-Deployment fehlgeschlagen; DB-Restore prüfen. Keine automatische Rückmigration.')
            # Resume the existing archive independently; app remains stopped for review.
            for name in ARCHIVE_SERVICES:
                server.run(['docker','start',name],check=False)
            server.status(phase='Fehler',last_result='Projektakte gestoppt; geschützte Sicherung prüfen')
            raise


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['backup','deploy'])
    parser.add_argument('--image')
    args=parser.parse_args()
    try:
        result=backup() if args.action=='backup' else deploy(args.image or '')
        print(json.dumps(result))
    except Exception:
        # Do not leak provider response bodies or configuration values.
        print(json.dumps({'result':'failed','action':args.action,'message':'Betriebsschritt abgebrochen; geschützten Serverstatus prüfen.'}))
        raise SystemExit(1)

if __name__=='__main__':main()
