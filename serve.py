"""Single low-memory web process. No auto-migrate on startup."""
import argparse,os
from pathlib import Path

def load_env():
    env=Path(__file__).resolve().parent/'.env'
    if env.exists():
        for line in env.read_text(encoding='utf8').splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key,value=line.split('=',1);os.environ.setdefault(key.strip(),value.strip().strip('"').strip("'"))

def proxy_options():
    # DNS/IP trust belongs to the refreshing WSGI boundary. Static trusted_proxy
    # addresses in Waitress would drop headers after a tunnel IP change.
    return {'clear_untrusted_proxy_headers': False}


if __name__=='__main__':
    load_env()
    parser=argparse.ArgumentParser();parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8010);args=parser.parse_args()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
    from config.wsgi import application
    from waitress import serve
    from akte.request_limits import HARD_BODY_LIMIT, install_request_limits
    install_request_limits()
    print(f'Projektakte: http://{args.host}:{args.port}',flush=True)
    serve(application,host=args.host,port=args.port,threads=2,max_request_body_size=HARD_BODY_LIMIT,max_request_header_size=16384,channel_timeout=120,connection_limit=32,inbuf_overflow=64*1024,expose_tracebacks=False,**proxy_options())
