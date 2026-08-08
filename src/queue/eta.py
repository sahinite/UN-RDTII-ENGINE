"""ETA computation for queued runs based on historical completed durations."""

from __future__ import annotations

import sqlite3

MIN_SAMPLES = 3
SAMPLE_WINDOW = 10
MAX_CONCURRENT_RUNS = 5


def compute_eta_seconds(
    economy: str,
    pillar: int,
    position_in_queue: int,
    conn: sqlite3.Connection,
    max_concurrent: int = MAX_CONCURRENT_RUNS,
) -> float | None:
    """Return the estimated seconds until this queued run STARTS.
    Returns None if fewer than MIN_SAMPLES completed runs exist for the given
    (economy, pillar) — caller should display "ETA unavailable" then."""
    rows = conn.execute(
        "SELECT duration_s FROM ("
        "  SELECT duration_s FROM runs "
        "  WHERE economy=? AND pillar=? AND status='completed' "
        "  AND duration_s IS NOT NULL "
        "  ORDER BY finished_at DESC LIMIT ?"
        ")",
        (economy, pillar, SAMPLE_WINDOW),
    ).fetchall()

    if len(rows) < MIN_SAMPLES:
        return None

    durations = [float(r[0]) for r in rows]
    avg_duration = sum(durations) / len(durations)
    return (position_in_queue / max_concurrent) * avg_duration
