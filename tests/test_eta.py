"""Tests for src/queue/eta.py — ETA computation from completed-run history."""

from __future__ import annotations

import sqlite3

from src.auth.db import get_connection, init_db
from src.queue.eta import compute_eta_seconds


def _insert_user(conn: sqlite3.Connection, email: str = "a@b.com") -> None:
    conn.execute(
        "INSERT INTO users (email, name, user_hash) VALUES (?, ?, ?)",
        (email, "User", "hash"),
    )
    conn.commit()


def _insert_completed(
    conn: sqlite3.Connection,
    run_id: str,
    economy: str,
    pillar: int,
    duration_s: float,
    finished_offset_seconds: int = 0,
    email: str = "a@b.com",
) -> None:
    conn.execute(
        "INSERT INTO runs (run_id, email, economy, pillar, status, "
        "finished_at, duration_s) VALUES (?, ?, ?, ?, 'completed', "
        "datetime('now', ?), ?)",
        (run_id, email, economy, pillar,
         f"{finished_offset_seconds} seconds", duration_s),
    )
    conn.commit()


def _insert_status(
    conn: sqlite3.Connection,
    run_id: str,
    economy: str,
    pillar: int,
    status: str,
    duration_s: float | None,
    email: str = "a@b.com",
) -> None:
    conn.execute(
        "INSERT INTO runs (run_id, email, economy, pillar, status, duration_s) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, email, economy, pillar, status, duration_s),
    )
    conn.commit()


def _fresh_db(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    conn = get_connection(db)
    _insert_user(conn)
    return conn


def test_returns_none_when_insufficient_samples(tmp_path):
    conn = _fresh_db(tmp_path)
    try:
        assert compute_eta_seconds("Singapore", 7, 1, conn) is None
        _insert_completed(conn, "r1", "Singapore", 7, 60)
        assert compute_eta_seconds("Singapore", 7, 1, conn) is None
        _insert_completed(conn, "r2", "Singapore", 7, 90)
        assert compute_eta_seconds("Singapore", 7, 1, conn) is None
    finally:
        conn.close()


def test_returns_estimate_with_min_samples(tmp_path):
    conn = _fresh_db(tmp_path)
    try:
        _insert_completed(conn, "r1", "Singapore", 7, 60)
        _insert_completed(conn, "r2", "Singapore", 7, 90)
        _insert_completed(conn, "r3", "Singapore", 7, 120)
        eta = compute_eta_seconds("Singapore", 7, 1, conn, max_concurrent=5)
        assert eta == (1 / 5) * 90
        assert eta == 18.0
    finally:
        conn.close()


def test_uses_rolling_window(tmp_path):
    conn = _fresh_db(tmp_path)
    try:
        # Oldest 5 with duration=1000 (finished furthest in the past).
        for i in range(5):
            _insert_completed(
                conn, f"old{i}", "Singapore", 7, 1000,
                finished_offset_seconds=-1000 + i,
            )
        # Newest 10 with duration=60.
        for i in range(10):
            _insert_completed(
                conn, f"new{i}", "Singapore", 7, 60,
                finished_offset_seconds=i,
            )
        eta = compute_eta_seconds("Singapore", 7, 1, conn, max_concurrent=5)
        assert eta == (1 / 5) * 60
    finally:
        conn.close()


def test_ignores_other_economy_pillar(tmp_path):
    conn = _fresh_db(tmp_path)
    try:
        _insert_completed(conn, "s1", "Singapore", 7, 100)
        _insert_completed(conn, "s2", "Singapore", 7, 100)
        _insert_completed(conn, "s3", "Singapore", 7, 100)
        _insert_completed(conn, "a1", "Australia", 6, 9999)
        _insert_completed(conn, "a2", "Australia", 6, 9999)
        _insert_completed(conn, "a3", "Australia", 6, 9999)
        eta = compute_eta_seconds("Singapore", 7, 1, conn, max_concurrent=5)
        assert eta == (1 / 5) * 100
    finally:
        conn.close()


def test_ignores_non_completed(tmp_path):
    conn = _fresh_db(tmp_path)
    try:
        _insert_status(conn, "f1", "Singapore", 7, "failed", 60)
        _insert_status(conn, "f2", "Singapore", 7, "failed", 90)
        _insert_status(conn, "f3", "Singapore", 7, "failed", 120)
        assert compute_eta_seconds("Singapore", 7, 1, conn) is None
    finally:
        conn.close()


def test_position_scales_linearly(tmp_path):
    conn = _fresh_db(tmp_path)
    try:
        _insert_completed(conn, "r1", "Singapore", 7, 100)
        _insert_completed(conn, "r2", "Singapore", 7, 100)
        _insert_completed(conn, "r3", "Singapore", 7, 100)
        eta5 = compute_eta_seconds("Singapore", 7, 5, conn, max_concurrent=5)
        eta10 = compute_eta_seconds("Singapore", 7, 10, conn, max_concurrent=5)
        assert eta5 is not None and eta10 is not None
        assert eta10 == 2 * eta5
    finally:
        conn.close()


def test_position_zero_or_one_at_head(tmp_path):
    conn = _fresh_db(tmp_path)
    try:
        _insert_completed(conn, "r1", "Singapore", 7, 100)
        _insert_completed(conn, "r2", "Singapore", 7, 100)
        _insert_completed(conn, "r3", "Singapore", 7, 100)
        eta = compute_eta_seconds("Singapore", 7, 1, conn, max_concurrent=5)
        assert eta == 20.0
    finally:
        conn.close()
