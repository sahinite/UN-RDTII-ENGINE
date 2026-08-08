"""SQLite schema, initialization, and migration scaffolding for user data."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path("data/users.db")
CURRENT_SCHEMA_VERSION = 1

_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS users (
        email        TEXT PRIMARY KEY,
        name         TEXT,
        picture_url  TEXT,
        contact      TEXT,
        user_hash    TEXT NOT NULL,
        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_users_user_hash ON users(user_hash)",
    """
    CREATE TABLE IF NOT EXISTS user_secrets (
        email            TEXT NOT NULL,
        key_name         TEXT NOT NULL,
        value_encrypted  BLOB NOT NULL,
        updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (email, key_name),
        FOREIGN KEY (email) REFERENCES users(email) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_configs (
        email        TEXT PRIMARY KEY,
        config_json  TEXT NOT NULL DEFAULT '{}',
        updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (email) REFERENCES users(email) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id       TEXT PRIMARY KEY,
        email        TEXT NOT NULL,
        economy      TEXT NOT NULL,
        pillar       INTEGER NOT NULL,
        status       TEXT NOT NULL CHECK (status IN ('queued','running','completed','failed','cancelled')),
        enqueued_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        started_at   TIMESTAMP,
        finished_at  TIMESTAMP,
        pid          INTEGER,
        output_dir   TEXT,
        duration_s   REAL,
        FOREIGN KEY (email) REFERENCES users(email) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_runs_email ON runs(email)",
    "CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status)",
    "CREATE INDEX IF NOT EXISTS idx_runs_enqueued_at ON runs(enqueued_at)",
    "CREATE INDEX IF NOT EXISTS idx_runs_economy_pillar ON runs(economy, pillar)",
)


def get_connection(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Return a SQLite connection with row_factory=sqlite3.Row and foreign_keys=ON."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # Foreign keys are off by default in SQLite; must be enabled per-connection.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    """Create schema, run migrations, and requeue orphaned running rows."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = get_connection(path)
    try:
        current_version = conn.execute("PRAGMA user_version").fetchone()[0]

        if current_version > CURRENT_SCHEMA_VERSION:
            raise RuntimeError("DB schema is newer than app expects")

        for stmt in _SCHEMA_STATEMENTS:
            conn.execute(stmt)

        # Future migrations dispatch on current_version here (0 -> 1 -> ...).
        # For v1, the CREATE-IF-NOT-EXISTS pass above is sufficient.

        # Crash-recovery: any row still marked 'running' belongs to a dead process.
        conn.execute(
            "UPDATE runs SET status='queued', started_at=NULL, pid=NULL "
            "WHERE status='running'"
        )

        # PRAGMA user_version does not accept parameter binding.
        conn.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()
