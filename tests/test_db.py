"""Tests for src/auth/db.py — schema, init, migrations, requeue behaviour."""

from __future__ import annotations

import sqlite3

import pytest

from src.auth.db import CURRENT_SCHEMA_VERSION, get_connection, init_db


EXPECTED_TABLES = {"users", "user_secrets", "user_configs", "runs"}
EXPECTED_INDEXES = {
    "idx_users_user_hash",
    "idx_runs_email",
    "idx_runs_status",
    "idx_runs_enqueued_at",
    "idx_runs_economy_pillar",
}


def _insert_user(conn: sqlite3.Connection, email: str = "a@b.com") -> None:
    conn.execute(
        "INSERT INTO users (email, name, user_hash) VALUES (?, ?, ?)",
        (email, "Alice", "hash-abc"),
    )
    conn.commit()


def test_init_creates_all_tables(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)

    conn = get_connection(db)
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        indexes = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        assert EXPECTED_TABLES.issubset(tables)
        assert EXPECTED_INDEXES.issubset(indexes)
    finally:
        conn.close()


def test_init_is_idempotent(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)

    conn = get_connection(db)
    _insert_user(conn)
    conn.close()

    init_db(db)

    conn = get_connection(db)
    try:
        rows = conn.execute("SELECT email FROM users").fetchall()
        assert len(rows) == 1
        assert rows[0]["email"] == "a@b.com"
    finally:
        conn.close()


def test_init_sets_user_version(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)

    conn = get_connection(db)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == CURRENT_SCHEMA_VERSION == 1
    finally:
        conn.close()


def test_init_creates_parent_dir(tmp_path):
    db = tmp_path / "nested" / "dir" / "x.db"
    init_db(db)
    assert db.exists()


def test_foreign_keys_enforced(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)

    conn = get_connection(db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO user_secrets (email, key_name, value_encrypted) "
                "VALUES (?, ?, ?)",
                ("nobody@example.com", "openai", b"blob"),
            )
            conn.commit()
    finally:
        conn.close()


def test_cascade_delete_on_user(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)

    conn = get_connection(db)
    try:
        _insert_user(conn, "u@x.com")
        conn.execute(
            "INSERT INTO user_secrets (email, key_name, value_encrypted) "
            "VALUES (?, ?, ?)",
            ("u@x.com", "openai", b"enc"),
        )
        conn.execute(
            "INSERT INTO user_configs (email, config_json) VALUES (?, ?)",
            ("u@x.com", '{"k":"v"}'),
        )
        conn.execute(
            "INSERT INTO runs (run_id, email, economy, pillar, status) "
            "VALUES (?, ?, ?, ?, ?)",
            ("run-1", "u@x.com", "Singapore", 7, "queued"),
        )
        conn.commit()

        conn.execute("DELETE FROM users WHERE email = ?", ("u@x.com",))
        conn.commit()

        assert conn.execute("SELECT COUNT(*) FROM user_secrets").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM user_configs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    finally:
        conn.close()


def test_orphaned_running_rows_requeued(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)

    conn = get_connection(db)
    try:
        _insert_user(conn, "u@x.com")
        conn.execute(
            "INSERT INTO runs (run_id, email, economy, pillar, status, "
            "started_at, pid) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("run-orphan", "u@x.com", "Singapore", 7, "running",
             "2026-01-01 00:00:00", 1234),
        )
        conn.commit()
    finally:
        conn.close()

    init_db(db)

    conn = get_connection(db)
    try:
        row = conn.execute(
            "SELECT status, started_at, pid FROM runs WHERE run_id = ?",
            ("run-orphan",),
        ).fetchone()
        assert row["status"] == "queued"
        assert row["started_at"] is None
        assert row["pid"] is None
    finally:
        conn.close()


def test_status_check_constraint(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)

    conn = get_connection(db)
    try:
        _insert_user(conn, "u@x.com")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO runs (run_id, email, economy, pillar, status) "
                "VALUES (?, ?, ?, ?, ?)",
                ("run-bad", "u@x.com", "Singapore", 7, "bogus"),
            )
            conn.commit()
    finally:
        conn.close()


def test_get_connection_row_factory(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)

    conn = get_connection(db)
    try:
        assert conn.row_factory is sqlite3.Row
    finally:
        conn.close()


def test_get_connection_foreign_keys_on(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)

    conn = get_connection(db)
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_reject_newer_schema_version(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)

    conn = get_connection(db)
    try:
        conn.execute("PRAGMA user_version = 999")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(RuntimeError, match="newer than app expects"):
        init_db(db)
