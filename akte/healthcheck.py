"""Container-local liveness without a public health exemption or login session."""
from contextlib import closing
import http.client
import os
import sqlite3
from pathlib import Path


def main():
    connection = http.client.HTTPConnection('127.0.0.1', 8000, timeout=5)
    try:
        connection.request('HEAD', '/login/', headers={'Host': '127.0.0.1'})
        response = connection.getresponse()
        secure_redirect = response.status == 301 and response.getheader('Location') == 'https://127.0.0.1/login/'
        setup_redirect = response.status == 302 and response.getheader('Location') == '/setup/'
        if response.status != 200 and not secure_redirect and not setup_redirect:
            raise SystemExit('Webprozess nicht bereit.')
    finally:
        connection.close()
    uri = (Path(os.environ.get('DATA_DIR', '/data')) / 'db.sqlite3').as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True, timeout=1)) as database:
        database.execute('SELECT 1').fetchone()


if __name__ == '__main__':
    main()
