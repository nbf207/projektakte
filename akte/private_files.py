"""Resolve every private download inside its fixed storage root."""
from pathlib import Path
from django.conf import settings
from django.http import Http404


def private_path(folder, relative):
    data = settings.DATA_DIR.resolve()
    root = data / folder
    name = Path(relative)
    if name.is_absolute() or not name.parts or '..' in name.parts or '\\' in str(relative):
        raise Http404
    path = root / name
    if root.is_symlink() or path.is_symlink():
        raise Http404
    if any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(data) and parent != data):
        raise Http404
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise Http404
    return resolved
