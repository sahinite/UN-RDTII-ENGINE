"""Tests for src/queue/rate_limit.py — per-user enqueue predicates."""

from __future__ import annotations

import sqlite3

from src.auth.db import get_connection, init_db
from src.queue.rate_limit import check_can_enqueue


def _insert_user(conn: sqlite3.Connection, email: str) -> None:
    conn.execute(
        "INSERT INTO users (email, name, user_hash) VALUES (?, ?, ?)",
        (email, "User", f"hash-{email}"),
    )
    conn.commit()


def _insert_run(
    conn: sqlite3.Connection,
    run_id: str,
    email: str,
    status: str,
    enqueued_expr: str = "CURRENT_TIMESTAMP",
    economy: str = "Singapore",
    pillar: int = 7,
) -> None:
    conn.execute(
        f"INSERT INTO runs (run_id, email, economy, pillar, status, enqueued_at) "
        f"VALUES (?, ?, ?, ?, ?, {enqueued_expr})",
        (run_id, email, economy, pillar, status),
    )
    conn.commit()


def _fresh_db(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    return get_connection(db)


def test_no_history_can_enqueue(tmp_path):
    conn = _fresh_db(tmp_path)
    try:
        _insert_user(conn, "a@b.com")
        ok, reason = check_can_enqueue("a@b.com", conn)
        assert ok is True
        assert reason is None
    finally:
        conn.close()


def test_one_running_one_queued_blocks_third(tmp_path):
    conn = _fresh_db(tmp_path)
    try:
        _insert_user(conn, "a@b.com")
        _insert_run(conn, "r1", "a@b.com", "running")
        _insert_run(conn, "r2", "a@b.com", "queued")
        ok, reason = check_can_enqueue("a@b.com", conn)
        assert ok is False
        assert reason is not None
        assert "pending" in reason or "cancel" in reason
    finally:
        conn.close()


def test_one_running_no_queued_allows_enqueue(tmp_path):
    conn = _fresh_db(tmp_path)
    try:
        _insert_user(conn, "a@b.com")
        _insert_run(conn, "r1", "a@b.com", "running")
        ok, reason = check_can_enqueue("a@b.com", conn)
        assert ok is True
        assert reason is None
    finally:
        conn.close()


def test_zero_running_one_queued_allows_enqueue(tmp_path):
    conn = _fresh_db(tmp_path)
    try:
        _insert_user(conn, "a@b.com")
        _insert_run(conn, "r1", "a@b.com", "queued")
        ok, reason = check_can_enqueue("a@b.com", conn)
        assert ok is True
        assert reason is None
    finally:
        conn.close()


def test_10_runs_in_last_hour_blocks_11th(tmp_path):
    conn = _fresh_db(tmp_path)
    try:
        _insert_user(conn, "a@b.com")
        for i in range(10):
            _insert_run(conn, f"r{i}", "a@b.com", "completed")
        ok, reason = check_can_enqueue("a@b.com", conn)
        assert ok is False
        assert reason is not None
        assert "Rate limit" in reason
    finally:
        conn.close()


def test_10_runs_over_2_hours_allows_enqueue(tmp_path):
    conn = _fresh_db(tmp_path)
    try:
        _insert_user(conn, "a@b.com")
        for i in range(5):
            _insert_run(
                conn, f"old{i}", "a@b.com", "completed",
                enqueued_expr="datetime('now','-2 hours')",
            )
        for i in range(5):
            _insert_run(conn, f"new{i}", "a@b.com", "completed")
        ok, reason = check_can_enqueue("a@b.com", conn)
        assert ok is True
        assert reason is None
    finally:
        conn.close()


def test_rate_limit_message_mentions_minutes(tmp_path):
    conn = _fresh_db(tmp_path)
    try:
        _insert_user(conn, "a@b.com")
        for i in range(10):
            _insert_run(conn, f"r{i}", "a@b.com", "completed")
        ok, reason = check_can_enqueue("a@b.com", conn)
        assert ok is False
        assert reason is not None
        assert "minute" in reason
    finally:
        conn.close()


def test_different_users_independent(tmp_path):
    conn = _fresh_db(tmp_path)
    try:
        _insert_user(conn, "a@b.com")
        _insert_user(conn, "b@b.com")
        for i in range(10):
            _insert_run(conn, f"r{i}", "a@b.com", "completed")
        ok, reason = check_can_enqueue("b@b.com", conn)
        assert ok is True
        assert reason is None
    finally:
        conn.close()
