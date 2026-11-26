import os,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parent.parent
os.chdir(root)
from sys import executable
for args in (['manage.py','check'],['manage.py','migrate','--check'],['manage.py','collectstatic','--noinput']):
    subprocess.run([executable,*args],check=True)
worker=subprocess.Popen([executable,'manage.py','worker'],creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
web=None
try:
    print('Projektakte: http://127.0.0.1:8010\nBeenden mit Strg+C.',flush=True)
    web=subprocess.Popen([executable,'serve.py'])
    web.wait()
except KeyboardInterrupt: pass
finally:
    for child in (web,worker):
        if child and child.poll() is None:
            child.terminate()
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired: child.kill()
