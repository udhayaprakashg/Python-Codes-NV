# scheduler_app/management/commands/watch_folders.py
import asyncio
import signal
import sys
import os
import threading
from pathlib import Path
from django.core.management.base import BaseCommand
from django.utils import timezone
from scheduler_app.models import ScheduledJob
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from django_q.tasks import async_task

_batch = {}
_timers = {}
_lock = threading.Lock()
DEBOUNCE = 2


async def _trigger_batch_async(job_id):
    with _lock:
        files = list(_batch.pop(job_id, set()))
        timer = _timers.pop(job_id, None)
        if timer:
            timer.cancel()

    if not files:
        return

    try:
        job = await asyncio.to_thread(
            ScheduledJob.objects.get, pk=job_id
        )
        
        print(f"\nBATCH TRIGGER -> '{job.name}' | {len(files)} file(s)")
        for f in files:
            print(f"  -> {Path(f).name}")

        await asyncio.to_thread(
            async_task,
            'scheduler_app.tasks.execute_job',
            job_id,
            file_paths=files
        )
        
    except Exception as e:
        print(f"ERROR: Error triggering job {job_id}: {e}")


def _schedule_debounce(job_id, loop):
    with _lock:
        old_timer = _timers.get(job_id)
        if old_timer:
            old_timer.cancel()

        def trigger():
            asyncio.run_coroutine_threadsafe(
                _trigger_batch_async(job_id), 
                loop
            )

        timer = threading.Timer(DEBOUNCE, trigger)
        timer.daemon = True
        timer.start()
        _timers[job_id] = timer


class AsyncFileHandler(FileSystemEventHandler):
    def __init__(self, job, loop):
        self.job = job
        self.loop = loop

    def on_created(self, event):
        if event.is_directory:
            return

        file_path = str(Path(event.src_path).resolve())
        
        with _lock:
            batch = _batch.setdefault(self.job.id, set())
            if file_path not in batch:
                batch.add(file_path)
                print(f"[{timezone.now():%H:%M:%S}] + {Path(file_path).name} ({self.job.name})")

        _schedule_debounce(self.job.id, self.loop)


# NEW: Code change handler for auto-reload
class CodeChangeHandler(FileSystemEventHandler):
    def __init__(self, restart_callback):
        self.restart_callback = restart_callback
        self.restart_debounce = None
        self.lock = threading.Lock()

    def on_modified(self, event):
        if event.is_directory:
            return
        
        # Only watch Python files
        if not event.src_path.endswith('.py'):
            return
        
        # Debounce restarts (wait 1 second)
        with self.lock:
            if self.restart_debounce:
                self.restart_debounce.cancel()
            
            self.restart_debounce = threading.Timer(1.0, self._do_restart, args=[event.src_path])
            self.restart_debounce.start()
    
    def _do_restart(self, changed_file):
        print(f"\nCODE CHANGE DETECTED: {Path(changed_file).name}")
        print("Restarting watch_folders...\n")
        self.restart_callback()


class Command(BaseCommand):
    help = 'Watch folders asynchronously with auto-reload on code changes'

    def add_arguments(self, parser):
        parser.add_argument(
            '--no-reload',
            action='store_true',
            help='Disable auto-reload on code changes',
        )

    def handle(self, *args, **options):
        auto_reload = not options.get('no_reload', False)
        
        if auto_reload:
            self._run_with_reload()
        else:
            self._run_watchers()

    def _run_with_reload(self):
        """Run with auto-reload capability"""
        while True:
            try:
                # Start in subprocess so we can restart it
                import subprocess
                
                cmd = [sys.executable, 'manage.py', 'watch_folders', '--no-reload']
                proc = subprocess.Popen(cmd, cwd=os.getcwd())
                
                # Watch for code changes
                code_observer = Observer()
                
                def restart():
                    proc.terminate()
                    proc.wait()
                
                handler = CodeChangeHandler(restart)
                
                # Watch scheduler_app directory
                watch_paths = [
                    Path('scheduler_app'),
                    Path('scheduler_app/management/commands'),
                ]
                
                for path in watch_paths:
                    if path.exists():
                        code_observer.schedule(handler, str(path), recursive=True)
                
                code_observer.start()
                
                # Wait for subprocess to finish
                proc.wait()
                
                code_observer.stop()
                code_observer.join()
                
                # If process was terminated by us, restart
                if proc.returncode != 0:
                    print("Restarting in 1 second...\n")
                    import time
                    time.sleep(1)
                    continue
                else:
                    # Clean exit (Ctrl+C)
                    break
                    
            except KeyboardInterrupt:
                if 'proc' in locals():
                    proc.terminate()
                break

    def _run_watchers(self):
        """Run the actual file watchers"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        observers = []
        running = True

        def cleanup(signum=None, frame=None):
            nonlocal running
            running = False
            print("\nStopping...")
            
            for obs in observers:
                obs.stop()
            
            with _lock:
                for t in _timers.values():
                    t.cancel()
            
            for obs in observers:
                obs.join()
            
            loop.stop()
            print("Stopped")

        signal.signal(signal.SIGINT, cleanup)
        signal.signal(signal.SIGTERM, cleanup)

        try:
            jobs = ScheduledJob.objects.filter(enabled=True, trigger_type='folder')
            
            for job in jobs:
                if not job.watch_path or not Path(job.watch_path).exists():
                    print(f"WARNING: Skipping '{job.name}': invalid path")
                    continue

                observer = Observer()
                handler = AsyncFileHandler(job, loop)
                observer.schedule(handler, job.watch_path, recursive=False)
                observer.start()
                observers.append(observer)
                print(f"WATCHING: {job.watch_path} -> {job.name} [async]")

            if not observers:
                print("WARNING: No folders to watch")
                return

            print(f"\nWatching {len(observers)} folder(s) with async processing.")
            print("Auto-reload: ENABLED (monitoring code changes)")
            print("Press Ctrl+C to stop.\n")
            
            async def keep_alive():
                while running:
                    await asyncio.sleep(0.1)
            
            loop.run_until_complete(keep_alive())

        except Exception as e:
            print(f"ERROR: {e}")
            cleanup()
        finally:
            loop.close()
