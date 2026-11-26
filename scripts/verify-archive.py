import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from serve import load_env
load_env()
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from akte.exports import verify_archive
verify_archive(sys.argv[1]);print('Archiv und Prüfsummen gültig.')
