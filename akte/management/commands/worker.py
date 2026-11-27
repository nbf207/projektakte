import threading
import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.conf import settings
from akte.backup import cleanup_stale_backups
from akte.worker import reserve, run_job, run_one
from akte.mail_ingest import MailPoller
from akte.operations import OperationBusy
class Command(BaseCommand):
    help='Ein serieller Worker mit separatem Backup-Thread. --once verarbeitet einen fälligen Schritt.'
    def add_arguments(self,parser): parser.add_argument('--once',action='store_true')
    def handle(self,*args,**options):
        lock=(settings.DATA_DIR/'worker.lock').open('a+b')
        try:
            import os
            if os.name=='nt':
                import msvcrt
                lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            from django.core.management.base import CommandError
            raise CommandError('Ein serieller Worker läuft bereits.')
        cleanup_stale_backups()
        mail=MailPoller()
        next_demo_cleanup=time.monotonic()+60
        next_housekeeping=time.monotonic()+60
        next_media_usage=time.monotonic()+5
        backup_thread=None

        def run_backup_thread(job):
            try:
                run_job(job)
            finally:
                close_old_connections()

        while True:
            (settings.DATA_DIR/'worker.heartbeat').touch()
            close_old_connections()
            if not options['once']: mail.tick()
            if not options['once'] and time.monotonic()>=next_housekeeping and not (settings.DATA_DIR/'.maintenance').exists():
                from akte.control_tasks import periodic_housekeeping
                try:periodic_housekeeping()
                except Exception:
                    from akte.account_events import error_event
                    error_event('Wartung','Automatische Papierkorb-Bereinigung abgebrochen; Daten bleiben erhalten.')
                next_housekeeping=time.monotonic()+3600
            if not options['once'] and time.monotonic()>=next_media_usage and not (settings.DATA_DIR/'.maintenance').exists():
                from akte.control_tasks import refresh_media_usage
                try:refresh_media_usage()
                except Exception:pass
                next_media_usage=time.monotonic()+3600
            if not options['once'] and time.monotonic() >= next_demo_cleanup:
                from akte.demo_seed import expire_visitors
                try: expire_visitors()
                except Exception: pass  # Demo housekeeping must not block live jobs.
                next_demo_cleanup=time.monotonic()+300
            try:
                if not options['once'] and (backup_thread is None or not backup_thread.is_alive()) and not (settings.DATA_DIR/'.maintenance').exists():
                    bjob = reserve(only_backup=True)
                    if bjob is not None:
                        backup_thread = threading.Thread(target=run_backup_thread, args=(bjob,), daemon=True, name='projektakte-backup')
                        backup_thread.start()
                worked = run_one(allow_backup=bool(options['once']))
                if options['once']:
                    return
                if not worked:
                    from akte.knowledge_video import process_video
                    worked = process_video()
            except OperationBusy:
                # A concurrent archive holds the write lock. Leave jobs queued.
                if options['once']:
                    return
                worked = False
            if not worked:
                time.sleep(2)
