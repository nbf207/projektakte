import os,sys,time
from pathlib import Path
data=Path(os.environ.get('DATA_DIR','data'))
beat=data/'worker.heartbeat'
sys.exit(0 if beat.is_file() and time.time()-beat.stat().st_mtime<240 else 1)
