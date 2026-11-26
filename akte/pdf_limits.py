"""Resource-limited wrappers for every untrusted PDF parser path."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError


def run_pdf(action, path, *sources):
    command = [sys.executable, str(Path(__file__).with_name('pdf_sandbox.py')), action, str(path), *map(str,sources)]
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=18, check=True)
        if len(result.stdout) > 2 * 1024 * 1024:raise ValueError()
        return json.loads(result.stdout)
    except (subprocess.SubprocessError, OSError, ValueError):
        raise ValidationError('PDF beschädigt, verschlüsselt oder zu komplex. Grenze: 100 Seiten, 256 MB Verarbeitungsspeicher und kurze Verarbeitungszeit. Datei bleibt bei bestätigten Belegen erhalten.') from None


def validate_pdf(path):
    return run_pdf('validate', path)


def pdf_pages(raw, *, layout=False):
    with tempfile.NamedTemporaryFile(dir=settings.FILE_UPLOAD_TEMP_DIR,suffix='.pdf',delete=False) as stream:
        stream.write(raw);path=Path(stream.name)
    try:return run_pdf('layout' if layout else 'text',path)['pages']
    finally:path.unlink(missing_ok=True)


def merge_pdf_files(assets):
    with tempfile.NamedTemporaryFile(dir=settings.FILE_UPLOAD_TEMP_DIR,suffix='.pdf',delete=False) as stream:path=Path(stream.name)
    try:
        run_pdf('merge',path,*(asset.file.path for asset in assets))
        with path.open('rb') as source:raw=source.read(settings.MAX_DOCUMENT_BYTES+1)
        if len(raw)>settings.MAX_DOCUMENT_BYTES:raise ValidationError('Zusammengeführte Rechnung zu groß.')
        return raw
    finally:path.unlink(missing_ok=True)
