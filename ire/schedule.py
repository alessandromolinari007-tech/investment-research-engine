"""Automatic updates: decide whether the analysis is old enough to be repeated.

`python -m ire update` is started by the Windows Task Scheduler (schedule_updates.bat) at every logon and once a
day. It is harmless to start it often: it only runs the analysis when the last completed one is older than
`[update] max_age_days` (statements are published quarterly, so more often is pointless).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def update_needed(con: sqlite3.Connection, max_age_days: float, default_mode: str,
                  now: datetime | None = None) -> tuple[bool, str, str]:
    """(needed, mode, reason). The mode of the last completed analysis is kept (the same universe, comparable results)."""
    now = now or datetime.now(timezone.utc)
    last = con.execute("SELECT run_id, mode, finished_at FROM runs WHERE status='completed' AND finished_at IS NOT NULL "
                       "ORDER BY run_id DESC LIMIT 1").fetchone()
    if last is None:
        return True, default_mode, "nessuna analisi completata"
    fin = datetime.fromisoformat(last["finished_at"])
    if fin.tzinfo is None:
        fin = fin.replace(tzinfo=timezone.utc)
    age = (now - fin).total_seconds() / 86400
    if age >= max_age_days:
        return True, last["mode"], f"ultima analisi (#{last['run_id']}) di {age:.1f} giorni fa"
    return False, last["mode"], f"ultima analisi (#{last['run_id']}) di {age:.1f} giorni fa: ancora recente (soglia {max_age_days:g})"
