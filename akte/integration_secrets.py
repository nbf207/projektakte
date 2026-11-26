"""Private server-side credential override, shared by web and worker."""
import os
import re
import tempfile
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError
from .operations import operation_gate

def gemini_key():
    path = settings.DATA_DIR / 'secrets' / 'gemini-api-key'
    if path.is_symlink():
        raise ValidationError('Ungültige Schlüsseldatei.')
    if path.exists():
        return path.read_text(encoding='ascii').strip()
    return settings.GEMINI_API_KEY

def save_gemini_key(value):
    if not re.fullmatch(r'AIza[0-9A-Za-z_-]{30,100}', value):
        raise ValidationError('Bitte einen gültigen Google-API-Schlüssel eingeben.')
    with operation_gate():
        folder = settings.DATA_DIR / 'secrets'
        if folder.is_symlink():
            raise ValidationError('Ungültiger Geheimnisordner.')
        folder.mkdir(mode=0o700, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=folder, prefix='.gemini-')
        try:
            try:
                if hasattr(os, 'fchmod'):
                    os.fchmod(fd, 0o600)
            except BaseException:
                os.close(fd)
                raise
            with os.fdopen(fd, 'w', encoding='ascii') as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, folder / 'gemini-api-key')
        finally:
            Path(name).unlink(missing_ok=True)
