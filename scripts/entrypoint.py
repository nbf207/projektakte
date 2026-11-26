"""Container startup entrypoint: atomic secret key, DB safety checks, auto-migrate, then Waitress."""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def main():
    from serve import load_env
    load_env()

    data_dir = Path(os.environ.get('DATA_DIR', BASE_DIR / 'data')).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    # Atomically initialize /data/.secret-key BEFORE Django loads settings or healthcheck passes,
    # so web and worker containers always read the exact same key.
    demo_mode = os.environ.get('DEMO_MODE', '0') == '1'
    if not demo_mode and not os.environ.get('SECRET_KEY') and not os.environ.get('SECRET_KEY_FILE'):
        from akte.updates import ensure_secret_key
        ensure_secret_key(data_dir)

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()

    from django.conf import settings
    from akte.updates import StartupError, prepare_startup

    if not settings.DEMO_MODE and not settings.SESSION_COOKIE_SECURE:
        print(
            'WARNING: Running with ALLOW_INSECURE_HTTP=1 (HTTPS=0). '
            'Use only on a private LAN/VPN; terminate TLS at a reverse proxy for public access.',
            file=sys.stderr,
            flush=True,
        )

    keep = int(os.environ.get('PRE_MIGRATE_KEEP', '5'))
    try:
        snapshot = prepare_startup(data_dir=settings.DATA_DIR, app_version=settings.APP_VERSION, keep=keep)
        if snapshot:
            print(f'Pre-migration SQLite snapshot saved: {snapshot}', flush=True)
    except StartupError as exc:
        print(f'ERROR: Startup aborted for database safety: {exc}', file=sys.stderr, flush=True)
        sys.exit(1)

    args = sys.argv[1:] or ['--host', '0.0.0.0', '--port', '8000']
    serve_script = str(BASE_DIR / 'serve.py')
    os.execv(sys.executable, [sys.executable, serve_script, *args])


if __name__ == '__main__':
    main()
