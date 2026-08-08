"""Tests for src/queue/scheduler.py — worker lifecycle, subprocess control, cancellation."""

from __future__ import annotations

import os

# Both must be set BEFORE importing src.auth.session / src.auth.crypto.
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "test.apps.googleusercontent.com")
if "SECRET_ENCRYPTION_KEY" not in os.environ:
    from cryptography.fernet import Fernet
    os.environ["SECRET_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.auth import session
from src.auth.db import get_connection, init_db
from src.auth.hash_util import sha256_hash
from src.queue import scheduler


# ── Helpers ──────────────────────────────────────────────────────────────────

def _insert_user(conn: sqlite3.Connection, email: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO users (email, name, user_hash) VALUES (?, ?, ?)",
        (email, email.split("@")[0], sha256_hash(email)),
    )
    conn.commit()


def _wait_for(pred, timeout: float = 10.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


def _wait_for_status(conn, run_id, target, timeout=10.0):
    ok = _wait_for(
        lambda: (
            (r := conn.execute(
                "SELECT status FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()) is not None and r["status"] == target
        ),
        timeout=timeout,
    )
    if not ok:
        r = conn.execute(
            "SELECT status FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        raise AssertionError(
            f"status of {run_id} did not become {target} within {timeout}s "
            f"(final: {r['status'] if r else 'missing'})"
        )


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def stop_scheduler_after():
    """Guaranteed teardown — a running worker will hang pytest otherwise."""
    yield
    try:
        scheduler.stop_worker(timeout_seconds=5.0)
    finally:
        scheduler._subprocess_argv_builder = None


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "test.db"
    init_db(p)
    return p


@pytest.fixture
def conn(db_path):
    c = get_connection(db_path)
    yield c
    c.close()


# ── enqueue ──────────────────────────────────────────────────────────────────

def test_enqueue_creates_row_status_queued(db_path, conn):
    _insert_user(conn, "u@x.com")
    run_id, err = scheduler.enqueue("u@x.com", "Singapore", 7, conn)
    assert err is None and run_id is not None
    row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    assert row["status"] == "queued"
    assert row["economy"] == "Singapore"
    assert row["pillar"] == 7
    assert row["pid"] is None
    assert row["output_dir"] is None


def test_enqueue_returns_uuid(db_path, conn):
    _insert_user(conn, "u@x.com")
    run_id, _ = scheduler.enqueue("u@x.com", "Singapore", 7, conn)
    assert isinstance(run_id, str) and len(run_id) == 32
    int(run_id, 16)  # must be hex


def test_enqueue_respects_rate_limit(db_path, conn):
    _insert_user(conn, "u@x.com")
    # Fill MAX_RUNNING + MAX_QUEUED = 2 slots directly.
    conn.execute(
        "INSERT INTO runs (run_id, email, economy, pillar, status) "
        "VALUES ('r1', 'u@x.com', 'Singapore', 7, 'running')"
    )
    conn.execute(
        "INSERT INTO runs (run_id, email, economy, pillar, status) "
        "VALUES ('r2', 'u@x.com', 'Singapore', 7, 'queued')"
    )
    conn.commit()
    run_id, reason = scheduler.enqueue("u@x.com", "Singapore", 7, conn)
    assert run_id is None
    assert reason is not None


def test_enqueue_requires_existing_user(db_path, conn):
    with pytest.raises(sqlite3.IntegrityError):
        scheduler.enqueue("ghost@x.com", "Singapore", 7, conn)


# ── get_run / get_queue_position ─────────────────────────────────────────────

def test_get_run_returns_dict(db_path, conn):
    _insert_user(conn, "u@x.com")
    run_id, _ = scheduler.enqueue("u@x.com", "Singapore", 7, conn)
    r = scheduler.get_run(run_id, conn)
    assert isinstance(r, dict)
    assert r["run_id"] == run_id
    assert r["status"] == "queued"


def test_get_run_missing_returns_none(conn):
    assert scheduler.get_run("nope", conn) is None


def test_get_queue_position_head_of_queue(db_path, conn):
    _insert_user(conn, "u@x.com")
    run_id, _ = scheduler.enqueue("u@x.com", "Singapore", 7, conn)
    assert scheduler.get_queue_position(run_id, conn) == 1


def test_get_queue_position_second(db_path, conn):
    _insert_user(conn, "a@x.com")
    _insert_user(conn, "b@x.com")
    conn.execute(
        "INSERT INTO runs (run_id, email, economy, pillar, status, enqueued_at) "
        "VALUES ('rA', 'a@x.com', 'Singapore', 7, 'queued', '2026-01-01 00:00:00')"
    )
    conn.execute(
        "INSERT INTO runs (run_id, email, economy, pillar, status, enqueued_at) "
        "VALUES ('rB', 'b@x.com', 'Singapore', 7, 'queued', '2026-01-01 00:00:05')"
    )
    conn.commit()
    assert scheduler.get_queue_position("rA", conn) == 1
    assert scheduler.get_queue_position("rB", conn) == 2


def test_get_queue_position_none_for_running(db_path, conn):
    _insert_user(conn, "u@x.com")
    run_id, _ = scheduler.enqueue("u@x.com", "Singapore", 7, conn)
    conn.execute("UPDATE runs SET status='running' WHERE run_id=?", (run_id,))
    conn.commit()
    assert scheduler.get_queue_position(run_id, conn) is None


# ── cancel_run ───────────────────────────────────────────────────────────────

def test_cancel_queued_marks_cancelled(db_path, conn):
    _insert_user(conn, "u@x.com")
    run_id, _ = scheduler.enqueue("u@x.com", "Singapore", 7, conn)
    ok, err = scheduler.cancel_run(run_id, "u@x.com", conn)
    assert ok and err is None
    r = conn.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
    assert r["status"] == "cancelled"


def test_cancel_running_kills_pid(db_path, conn):
    _insert_user(conn, "u@x.com")
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    try:
        conn.execute(
            "INSERT INTO runs (run_id, email, economy, pillar, status, pid) "
            "VALUES ('r-live', 'u@x.com', 'Singapore', 7, 'running', ?)",
            (proc.pid,),
        )
        conn.commit()

        ok, err = scheduler.cancel_run("r-live", "u@x.com", conn)
        assert ok and err is None
        assert _wait_for(lambda: proc.poll() is not None, timeout=5.0)
        r = conn.execute(
            "SELECT status FROM runs WHERE run_id='r-live'"
        ).fetchone()
        assert r["status"] == "cancelled"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_cancel_by_wrong_user_returns_false(db_path, conn):
    _insert_user(conn, "u@x.com")
    _insert_user(conn, "other@x.com")
    run_id, _ = scheduler.enqueue("u@x.com", "Singapore", 7, conn)
    ok, err = scheduler.cancel_run(run_id, "other@x.com", conn)
    assert ok is False and err == "not found"


def test_cancel_missing_run_returns_false(conn):
    ok, err = scheduler.cancel_run("nope", "u@x.com", conn)
    assert ok is False and err == "not found"


def test_cancel_already_completed_returns_false(db_path, conn):
    _insert_user(conn, "u@x.com")
    conn.execute(
        "INSERT INTO runs (run_id, email, economy, pillar, status) "
        "VALUES ('r-done', 'u@x.com', 'Singapore', 7, 'completed')"
    )
    conn.commit()
    ok, err = scheduler.cancel_run("r-done", "u@x.com", conn)
    assert ok is False and err == "already completed"


def test_cancel_stale_pid_still_succeeds(db_path, conn):
    _insert_user(conn, "u@x.com")
    # Grab a definitely-dead pid: spawn + wait + reuse the pid number.
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    stale_pid = dead.pid

    conn.execute(
        "INSERT INTO runs (run_id, email, economy, pillar, status, pid) "
        "VALUES ('r-stale', 'u@x.com', 'Singapore', 7, 'running', ?)",
        (stale_pid,),
    )
    conn.commit()

    ok, err = scheduler.cancel_run(
        "r-stale", "u@x.com", conn, sigterm_wait_seconds=0.1
    )
    assert ok and err is None
    r = conn.execute("SELECT status FROM runs WHERE run_id='r-stale'").fetchone()
    assert r["status"] == "cancelled"


# ── Worker lifecycle ─────────────────────────────────────────────────────────

def test_start_worker_idempotent(db_path):
    scheduler.start_worker(db_path, poll_interval_seconds=0.05)
    t1 = scheduler._worker_thread
    scheduler.start_worker(db_path, poll_interval_seconds=0.05)
    t2 = scheduler._worker_thread
    assert t1 is t2
    assert scheduler.is_worker_running()


def test_stop_worker_joins_cleanly(db_path):
    scheduler.start_worker(db_path, poll_interval_seconds=0.05)
    assert scheduler.is_worker_running()
    scheduler.stop_worker(timeout_seconds=5.0)
    assert scheduler.is_worker_running() is False
    # Second stop is a no-op.
    scheduler.stop_worker(timeout_seconds=1.0)
    assert scheduler.is_worker_running() is False


# ── Worker + subprocess integration ──────────────────────────────────────────

def test_worker_runs_queued_job_to_completion(db_path, conn, tmp_path):
    _insert_user(conn, "u@x.com")
    scheduler._subprocess_argv_builder = lambda e, p: [
        sys.executable, "-c", "print('ok')"
    ]
    scheduler.start_worker(
        db_path,
        poll_interval_seconds=0.05,
        outputs_root=str(tmp_path / "out"),
        logs_root=str(tmp_path / "logs"),
    )
    run_id, _ = scheduler.enqueue("u@x.com", "Singapore", 7, conn)
    _wait_for_status(conn, run_id, "completed", timeout=15.0)

    r = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    assert r["finished_at"] is not None
    assert r["duration_s"] is not None and r["duration_s"] >= 0


def test_worker_marks_failed_on_nonzero_exit(db_path, conn, tmp_path):
    _insert_user(conn, "u@x.com")
    scheduler._subprocess_argv_builder = lambda e, p: [
        sys.executable, "-c", "import sys; sys.exit(2)"
    ]
    scheduler.start_worker(
        db_path,
        poll_interval_seconds=0.05,
        outputs_root=str(tmp_path / "out"),
        logs_root=str(tmp_path / "logs"),
    )
    run_id, _ = scheduler.enqueue("u@x.com", "Singapore", 7, conn)
    _wait_for_status(conn, run_id, "failed", timeout=15.0)


def test_worker_respects_max_concurrent(db_path, conn, tmp_path):
    for i in range(4):
        _insert_user(conn, f"u{i}@x.com")

    scheduler._subprocess_argv_builder = lambda e, p: [
        sys.executable, "-c", "import time; time.sleep(2)"
    ]
    scheduler.start_worker(
        db_path,
        max_concurrent=2,
        poll_interval_seconds=0.05,
        outputs_root=str(tmp_path / "out"),
        logs_root=str(tmp_path / "logs"),
    )
    ids = []
    for i in range(4):
        # Manually INSERT with staggered enqueued_at so ordering is stable.
        rid = f"m{i}"
        conn.execute(
            "INSERT INTO runs (run_id, email, economy, pillar, status, enqueued_at) "
            "VALUES (?, ?, 'Singapore', 7, 'queued', ?)",
            (rid, f"u{i}@x.com", f"2026-01-01 00:00:0{i}"),
        )
        ids.append(rid)
    conn.commit()

    time.sleep(0.5)  # let worker pick up 2 of them
    rows = conn.execute(
        "SELECT status FROM runs WHERE run_id IN (?,?,?,?)", tuple(ids)
    ).fetchall()
    statuses = sorted([r["status"] for r in rows])
    assert statuses.count("running") == 2
    assert statuses.count("queued") == 2


def test_worker_recovers_after_run(db_path, conn, tmp_path):
    _insert_user(conn, "u@x.com")
    scheduler._subprocess_argv_builder = lambda e, p: [
        sys.executable, "-c", "print('hi')"
    ]
    scheduler.start_worker(
        db_path,
        poll_interval_seconds=0.05,
        outputs_root=str(tmp_path / "out"),
        logs_root=str(tmp_path / "logs"),
    )
    a, _ = scheduler.enqueue("u@x.com", "Singapore", 7, conn)
    _wait_for_status(conn, a, "completed", timeout=15.0)

    b, _ = scheduler.enqueue("u@x.com", "Singapore", 7, conn)
    _wait_for_status(conn, b, "completed", timeout=15.0)


def test_worker_writes_subprocess_log(db_path, conn, tmp_path):
    _insert_user(conn, "u@x.com")
    scheduler._subprocess_argv_builder = lambda e, p: [
        sys.executable, "-c", "print('line1'); print('line2')"
    ]
    logs_root = tmp_path / "logs"
    scheduler.start_worker(
        db_path,
        poll_interval_seconds=0.05,
        outputs_root=str(tmp_path / "out"),
        logs_root=str(logs_root),
    )
    run_id, _ = scheduler.enqueue("u@x.com", "Singapore", 7, conn)
    _wait_for_status(conn, run_id, "completed", timeout=15.0)

    log_path = logs_root / sha256_hash("u@x.com") / run_id / "subprocess.log"
    assert log_path.exists()
    text = log_path.read_text()
    assert "line1" in text and "line2" in text


def test_worker_honors_mid_run_cancel(db_path, conn, tmp_path):
    _insert_user(conn, "u@x.com")
    scheduler._subprocess_argv_builder = lambda e, p: [
        sys.executable, "-c", "import time; time.sleep(30)"
    ]
    scheduler.start_worker(
        db_path,
        poll_interval_seconds=0.05,
        outputs_root=str(tmp_path / "out"),
        logs_root=str(tmp_path / "logs"),
    )
    run_id, _ = scheduler.enqueue("u@x.com", "Singapore", 7, conn)
    _wait_for_status(conn, run_id, "running", timeout=10.0)

    r = conn.execute("SELECT pid FROM runs WHERE run_id=?", (run_id,)).fetchone()
    assert _wait_for(
        lambda: conn.execute(
            "SELECT pid FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()["pid"] is not None,
        timeout=5.0,
    )
    pid = conn.execute(
        "SELECT pid FROM runs WHERE run_id=?", (run_id,)
    ).fetchone()["pid"]

    ok, _ = scheduler.cancel_run(run_id, "u@x.com", conn)
    assert ok
    _wait_for_status(conn, run_id, "cancelled", timeout=10.0)
    assert _wait_for(lambda: not scheduler._pid_alive(pid), timeout=5.0)


def test_worker_uses_ctx_secrets_in_subprocess_env(db_path, conn, tmp_path):
    _insert_user(conn, "u@x.com")
    session.save_user_secret("u@x.com", "OPENAI_API_KEY", "sk-secret-123", conn)

    scheduler._subprocess_argv_builder = lambda e, p: [
        sys.executable, "-c",
        "import os; print('KEY=' + os.environ.get('OPENAI_API_KEY', 'MISSING'))",
    ]
    logs_root = tmp_path / "logs"
    scheduler.start_worker(
        db_path,
        poll_interval_seconds=0.05,
        outputs_root=str(tmp_path / "out"),
        logs_root=str(logs_root),
    )
    run_id, _ = scheduler.enqueue("u@x.com", "Singapore", 7, conn)
    _wait_for_status(conn, run_id, "completed", timeout=15.0)

    log_path = logs_root / sha256_hash("u@x.com") / run_id / "subprocess.log"
    text = log_path.read_text()
    assert "KEY=sk-secret-123" in text


def test_worker_missing_user_marks_failed(db_path, conn, tmp_path):
    _insert_user(conn, "u@x.com")
    run_id, _ = scheduler.enqueue("u@x.com", "Singapore", 7, conn)

    # Delete the user without cascading into runs — open a raw connection
    # with foreign_keys OFF so the runs row survives.
    raw = sqlite3.connect(str(db_path))
    raw.execute("PRAGMA foreign_keys = OFF")
    raw.execute("DELETE FROM users WHERE email='u@x.com'")
    raw.commit()
    raw.close()

    scheduler._subprocess_argv_builder = lambda e, p: [
        sys.executable, "-c", "print('should not run')"
    ]
    scheduler.start_worker(
        db_path,
        poll_interval_seconds=0.05,
        outputs_root=str(tmp_path / "out"),
        logs_root=str(tmp_path / "logs"),
    )
    _wait_for_status(conn, run_id, "failed", timeout=10.0)


def test_worker_respects_orphaned_running_requeue(db_path, conn, tmp_path):
    _insert_user(conn, "u@x.com")
    conn.execute(
        "INSERT INTO runs (run_id, email, economy, pillar, status, "
        "started_at, pid) VALUES ('r-orphan','u@x.com','Singapore',7,"
        "'running','2026-01-01 00:00:00', 99999)"
    )
    conn.commit()

    init_db(db_path)  # requeues orphaned running rows

    scheduler._subprocess_argv_builder = lambda e, p: [
        sys.executable, "-c", "print('recovered')"
    ]
    scheduler.start_worker(
        db_path,
        poll_interval_seconds=0.05,
        outputs_root=str(tmp_path / "out"),
        logs_root=str(tmp_path / "logs"),
    )
    _wait_for_status(conn, "r-orphan", "completed", timeout=15.0)
