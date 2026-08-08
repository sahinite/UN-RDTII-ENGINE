"""Background worker that owns the pipeline-run subprocess lifecycle.

Gradio handlers only INSERT queued rows and observe status; this module drains
the queue on a background thread so runs survive UI tab-close.
"""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from src.auth import session
from src.auth.db import get_connection
from src.queue import env_builder, rate_limit

DEFAULT_MAX_CONCURRENT = 5

# ── Module-level worker state (guarded by _worker_lock) ───────────────────────
_worker_thread: threading.Thread | None = None
_worker_lock = threading.Lock()
_shutdown_event = threading.Event()
_semaphore: threading.Semaphore | None = None
_max_concurrent: int = DEFAULT_MAX_CONCURRENT
_db_path: Path | None = None
_project_root: Path = Path(".")
_outputs_root: str = "outputs"
_logs_root: str = "logs"
_poll_interval: float = 1.0
_active_threads: list[threading.Thread] = []
_active_threads_lock = threading.Lock()

# Test hook: replace argv construction so tests can spawn a stub process
# instead of the real main.py. Signature: (economy, pillar) -> list[str].
_subprocess_argv_builder: Callable[[str, int], list[str]] | None = None


def _default_subprocess_argv(economy: str, pillar: int) -> list[str]:
    return [sys.executable, "main.py", "--economy", economy, "--pillar", str(pillar)]


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


# ── Public API ────────────────────────────────────────────────────────────────

def enqueue(
    email: str,
    economy: str,
    pillar: int,
    conn: sqlite3.Connection,
) -> tuple[str | None, str | None]:
    ok, reason = rate_limit.check_can_enqueue(email, conn)
    if not ok:
        return None, reason

    run_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO runs (run_id, email, economy, pillar, status) "
        "VALUES (?, ?, ?, ?, 'queued')",
        (run_id, email, economy, pillar),
    )
    conn.commit()
    return run_id, None


def get_run(run_id: str, conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT * FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    return _row_to_dict(row)


def get_queue_position(run_id: str, conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT status, enqueued_at FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None or row["status"] != "queued":
        return None
    ahead = conn.execute(
        "SELECT COUNT(*) FROM runs WHERE status='queued' "
        "AND enqueued_at < ?",
        (row["enqueued_at"],),
    ).fetchone()[0]
    return int(ahead) + 1


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def cancel_run(
    run_id: str,
    email: str,
    conn: sqlite3.Connection,
    sigterm_wait_seconds: float = 5.0,
) -> tuple[bool, str | None]:
    row = conn.execute(
        "SELECT email, status, pid FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None or row["email"] != email:
        return False, "not found"

    status = row["status"]
    if status in ("completed", "failed", "cancelled"):
        return False, f"already {status}"

    if status == "queued":
        conn.execute(
            "UPDATE runs SET status='cancelled', finished_at=CURRENT_TIMESTAMP "
            "WHERE run_id=? AND status='queued'",
            (run_id,),
        )
        conn.commit()
        return True, None

    # running: mark cancelled first so the worker's poll notices, then kill.
    conn.execute(
        "UPDATE runs SET status='cancelled', finished_at=CURRENT_TIMESTAMP "
        "WHERE run_id=?",
        (run_id,),
    )
    conn.commit()

    pid = row["pid"]
    if pid is not None:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return True, None
        deadline = time.monotonic() + sigterm_wait_seconds
        while time.monotonic() < deadline and _pid_alive(pid):
            time.sleep(0.05)
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    return True, None


# ── Worker internals ──────────────────────────────────────────────────────────

def _pump_stdout(process: subprocess.Popen, log_path: Path) -> None:
    with open(log_path, "a", encoding="utf-8", buffering=1) as f:
        assert process.stdout is not None
        for line in process.stdout:
            f.write(line)
            f.flush()


def _run_one(row_dict: dict) -> None:
    # Every worker-child thread MUST open its own DB connection —
    # sqlite3.Connection objects are not safe to share across threads.
    conn = get_connection(_db_path)
    run_id = row_dict["run_id"]
    started_monotonic = time.monotonic()

    def _mark_failed() -> None:
        try:
            conn.execute(
                "UPDATE runs SET status='failed', finished_at=CURRENT_TIMESTAMP, "
                "duration_s=? WHERE run_id=? AND status != 'cancelled'",
                (time.monotonic() - started_monotonic, run_id),
            )
            conn.commit()
        except Exception:
            pass

    try:
        ctx = session.load_run_context(row_dict["email"], conn, admin_emails=[])
        if ctx is None:
            _mark_failed()
            return

        env = env_builder.build_subprocess_env(
            ctx, run_id, base_env=None,
            outputs_root=_outputs_root, logs_root=_logs_root,
        )
        output_dir_full = f"{_outputs_root}/{ctx.user_hash}/{run_id}"
        conn.execute(
            "UPDATE runs SET output_dir=? WHERE run_id=?",
            (output_dir_full, run_id),
        )
        conn.commit()

        argv_builder = _subprocess_argv_builder or _default_subprocess_argv
        argv = argv_builder(row_dict["economy"], int(row_dict["pillar"]))

        log_path = Path(_logs_root) / ctx.user_hash / run_id / "subprocess.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            process = subprocess.Popen(
                argv,
                cwd=str(_project_root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception:
            _mark_failed()
            return

        conn.execute(
            "UPDATE runs SET pid=? WHERE run_id=?",
            (process.pid, run_id),
        )
        conn.commit()

        pumper = threading.Thread(
            target=_pump_stdout, args=(process, log_path), daemon=True
        )
        pumper.start()

        cancelled = False
        while process.poll() is None:
            status_row = conn.execute(
                "SELECT status FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if status_row is not None and status_row["status"] == "cancelled":
                cancelled = True
                try:
                    process.terminate()
                except OSError:
                    pass
                try:
                    process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except OSError:
                        pass
                break
            time.sleep(_poll_interval)

        process.wait()
        pumper.join(timeout=2.0)

        duration = time.monotonic() - started_monotonic
        # Re-read: cancel_run may have flipped status → 'cancelled' after our
        # poll but before process exit; we must not overwrite that.
        current = conn.execute(
            "SELECT status FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        current_status = current["status"] if current is not None else None
        if cancelled or current_status == "cancelled":
            conn.execute(
                "UPDATE runs SET duration_s=?, finished_at=CURRENT_TIMESTAMP "
                "WHERE run_id=?",
                (duration, run_id),
            )
            conn.commit()
        else:
            new_status = "completed" if process.returncode == 0 else "failed"
            conn.execute(
                "UPDATE runs SET status=?, finished_at=CURRENT_TIMESTAMP, "
                "duration_s=? WHERE run_id=? AND status='running'",
                (new_status, duration, run_id),
            )
            conn.commit()

    except Exception:
        _mark_failed()
    finally:
        # Release the semaphore BEFORE closing the connection so the worker
        # can pick up the next queued row immediately.
        try:
            if _semaphore is not None:
                _semaphore.release()
        finally:
            conn.close()


def _worker_loop() -> None:
    conn = get_connection(_db_path)
    try:
        while not _shutdown_event.is_set():
            sem = _semaphore
            if sem is None:
                break
            if not sem.acquire(blocking=False):
                time.sleep(_poll_interval)
                continue

            row = conn.execute(
                "SELECT * FROM runs WHERE status='queued' "
                "ORDER BY enqueued_at ASC, run_id ASC LIMIT 1"
            ).fetchone()
            if row is None:
                sem.release()
                time.sleep(_poll_interval)
                continue

            run_id = row["run_id"]
            cur = conn.execute(
                "UPDATE runs SET status='running', started_at=CURRENT_TIMESTAMP "
                "WHERE run_id=? AND status='queued'",
                (run_id,),
            )
            conn.commit()
            if cur.rowcount == 0:
                sem.release()
                continue

            row_dict = _row_to_dict(row) or {}
            row_dict["status"] = "running"

            t = threading.Thread(target=_run_one, args=(row_dict,), daemon=True)
            with _active_threads_lock:
                _active_threads.append(t)
            t.start()
    finally:
        conn.close()


# ── Lifecycle ─────────────────────────────────────────────────────────────────

def start_worker(
    db_path: Path | str,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    poll_interval_seconds: float = 1.0,
    project_root: Path | str = ".",
    outputs_root: str = "outputs",
    logs_root: str = "logs",
) -> None:
    global _worker_thread, _semaphore, _max_concurrent, _db_path
    global _project_root, _outputs_root, _logs_root, _poll_interval
    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return
        _shutdown_event.clear()
        _db_path = Path(db_path)
        _max_concurrent = max_concurrent
        _semaphore = threading.Semaphore(max_concurrent)
        _project_root = Path(project_root)
        _outputs_root = outputs_root
        _logs_root = logs_root
        _poll_interval = poll_interval_seconds
        with _active_threads_lock:
            _active_threads.clear()
        _worker_thread = threading.Thread(target=_worker_loop, daemon=True)
        _worker_thread.start()


def stop_worker(timeout_seconds: float = 10.0) -> None:
    global _worker_thread, _semaphore
    with _worker_lock:
        thread = _worker_thread
        if thread is None:
            return
        _shutdown_event.set()

    thread.join(timeout=timeout_seconds)

    with _active_threads_lock:
        active = list(_active_threads)
        _active_threads.clear()

    deadline = time.monotonic() + timeout_seconds
    for t in active:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        t.join(timeout=remaining)

    with _worker_lock:
        _worker_thread = None
        _semaphore = None


def is_worker_running() -> bool:
    with _worker_lock:
        return _worker_thread is not None and _worker_thread.is_alive()
