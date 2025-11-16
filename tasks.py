# scheduler_app/tasks.py
import os
import subprocess
from pathlib import Path
from django.utils import timezone
from django.db import transaction
from .models import ScheduledJob, JobLog


# scheduler_app/tasks.py
import os
import subprocess
from pathlib import Path
from django.utils import timezone
from django.db import transaction
from .models import ScheduledJob, JobLog
from datetime import datetime
# --- GLOBAL BATCH LOCK ---
_batch_lock = {}
import threading
_global_lock = threading.Lock()

def execute_job(job_id, file_paths=None,**kwargs):
    file_paths = file_paths or []
    using = kwargs.pop('using', None)
    print(f"[{timezone.now().strftime('%H:%M:%S')}] START Job {job_id} | {len(file_paths)} file(s)")

    try:
        job = ScheduledJob.objects.using("default").get(pk=job_id)
    except ScheduledJob.DoesNotExist:
        return

    db_alias = job.get_db_alias()
    log_entry = None
    success = False
    message = "Unknown error"

    # --- DEDUP: Only one job per batch ---
    batch_key = f"batch_{job_id}_{hash(tuple(sorted(file_paths)))}"
    with _global_lock:
        if batch_key in _batch_lock:
            print(f"SKIPPED: Job {job_id} already running for this batch")
            return
        _batch_lock[batch_key] = True

    try:
        with transaction.atomic(using=db_alias):
            job = ScheduledJob.objects.using(db_alias).select_related(
                "deployment_version__virtual_env"
            ).get(pk=job_id)

            version = job.deployment_version
            if not version:
                raise ValueError("No deployment version")

            log_entry = JobLog.objects.using(db_alias).create(
                job=job,
                deployment_version=version,
                started_at=timezone.now(),
            )

            # --- Virtual Env ---
            venv_path = Path(version.virtual_env.path)
            python = venv_path / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
            if not python.exists():
                raise FileNotFoundError(f"Python not found: {python}")

            # --- main.py ---
            extracted_path = Path(version.extracted_path)
            main_py = next(extracted_path.rglob("main.py"), None)
            if not main_py:
                raise FileNotFoundError("main.py not found")

            # --- pip install ---
            reqs_file = extracted_path / "requirements.txt"
            pip_bin = venv_path / ("Scripts" if os.name == "nt" else "bin") / ("pip.exe" if os.name == "nt" else "pip")
            if reqs_file.exists() and pip_bin.exists():
                install_result = subprocess.run(
                    [str(pip_bin), "install", "-r", str(reqs_file)],
                    cwd=extracted_path,
                    capture_output=True,
                    text=True,
                    encoding='utf-8',  # ← UTF-8
                    errors='replace',  # ← REPLACE BAD CHARS
                    timeout=300,
                )
                if install_result.returncode != 0:
                    raise RuntimeError(f"pip install failed:\n{install_result.stderr}")

            # --- Run main.py ---
            cmd = [str(python), str(main_py), str(log_entry.pk)] + file_paths

            run_result = subprocess.run(
                cmd,
                cwd=extracted_path,
                capture_output=True,
                text=True,
                encoding='utf-8',  # ← UTF-8
                errors='replace',  # ← REPLACE BAD CHARS
                timeout=3600,
            )

            output = (run_result.stdout + "\n" + run_result.stderr).strip()
            success = run_result.returncode == 0
            message = output or ("Success" if success else "Failed")

    except Exception as e:
        success = False
        message = f"Error: {e}"
        log_entry = None
    finally:
        with _global_lock:
            _batch_lock.pop(batch_key, None)

    _log_safe(job, log_entry, success, message, using=db_alias)
    print(f"[{timezone.now().strftime('%H:%M:%S')}] END Job {job_id}")


def _log_safe(job, log_entry, success, message, using="default"):
    """
    Log result OUTSIDE any transaction.
    Uses .update() → no signals → no recursion.
    """
    now = timezone.now()
    try:
        if log_entry:
            JobLog.objects.using(using).filter(pk=log_entry.pk).update(
                success=success,
                message=message[:3000],
                finished_at=now,
            )
        else:
            JobLog.objects.using(using).create(
                job=job,
                started_at=now,
                finished_at=now,
                success=success,
                message=message[:3000],
            )

        ScheduledJob.objects.using(using).filter(pk=job.pk).update(
            last_run=now,
            last_status="success" if success else "failed",
        )
    except Exception as e:
        print(f"LOGGING FAILED (non-critical): {e}")
