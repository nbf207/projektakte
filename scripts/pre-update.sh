#!/bin/sh
# Called by the existing update guard AFTER web/worker and Paperless writes stop.
# Does not update or restart anything itself. A failing backup returns nonzero.
set -eu
: "${BACKUP_TARGET:?Independent backup destination required}"
: "${PAPERLESS_BUNDLE:?Paperless backup from the same offline window required}"
test -f "$PAPERLESS_BUNDLE"
python manage.py backup "$BACKUP_TARGET" --offline --paperless-bundle "$PAPERLESS_BUNDLE"
python scripts/verify-archive.py "$BACKUP_TARGET"
