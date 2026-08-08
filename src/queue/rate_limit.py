"""Per-user rate-limit predicates for the run queue."""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime

MAX_RUNNING_PER_USER = 1
MAX_QUEUED_PER_USER = 1
MAX_RUNS_PER_HOUR_PER_USER = 10


def _count(conn: sqlite3.Connection, sql: str, params: tuple) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def check_can_enqueue(
    email: str, conn: sqlite3.Connection
) -> tuple[bool, str | None]:
    """Return (True, None) if the user may enqueue another run; otherwise
    (False, human_readable_reason)."""
    running = _count(
        conn,
        "SELECT COUNT(*) FROM runs WHERE email=? AND status='running'",
        (email,),
    )
    queued = _count(
        conn,
        "SELECT COUNT(*) FROM runs WHERE email=? AND status='queued'",
        (email,),
    )
    if running >= MAX_RUNNING_PER_USER and queued >= MAX_QUEUED_PER_USER:
        return False, "You already have 2 runs pending; cancel or wait."

    in_last_hour = _count(
        conn,
        "SELECT COUNT(*) FROM runs WHERE email=? "
        "AND enqueued_at > datetime('now','-1 hour')",
        (email,),
    )
    if in_last_hour >= MAX_RUNS_PER_HOUR_PER_USER:
        row = conn.execute(
            "SELECT MIN(enqueued_at) AS oldest, "
            "strftime('%Y-%m-%d %H:%M:%S','now') AS now_utc "
            "FROM runs WHERE email=? "
            "AND enqueued_at > datetime('now','-1 hour')",
            (email,),
        ).fetchone()
        oldest = row["oldest"] if row is not None else None
        now_str = row["now_utc"] if row is not None else None
        if oldest is None or now_str is None:
            minutes = 60
        else:
            # SQLite CURRENT_TIMESTAMP is naive UTC; parse both as naive UTC so
            # the difference is a pure duration with no tz offset drift.
            oldest_dt = datetime.fromisoformat(str(oldest).replace("T", " "))
            now_dt = datetime.fromisoformat(str(now_str).replace("T", " "))
            remaining = (oldest_dt - now_dt).total_seconds() + 3600
            minutes = max(1, math.ceil(remaining / 60))
        return False, f"Rate limit — try again in {minutes} minutes."

    return True, None
