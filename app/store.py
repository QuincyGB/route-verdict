"""Prior-check persistence (sqlite) — the memory the diff runs against.

One row per check: resource_url, ISO timestamp, and the full result JSON.
The latest row for a URL is the baseline for the next check's diff. The
database is the service's only state; lose it and every check becomes a
"first check" again (the diff says so honestly).
"""

from __future__ import annotations

import json
import sqlite3
import threading


class CheckStore:
    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    resource_url TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    result_json TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_checks_url ON checks (resource_url, id)"
            )
            self._conn.commit()

    def latest(self, resource_url: str) -> dict | None:
        """The most recent stored result for this URL, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT result_json FROM checks WHERE resource_url = ? ORDER BY id DESC LIMIT 1",
                (resource_url,),
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except ValueError:
            return None

    def save(self, result: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO checks (resource_url, checked_at, result_json) VALUES (?, ?, ?)",
                (result["resource_url"], result["checked_at"], json.dumps(result)),
            )
            self._conn.commit()

    def count(self, resource_url: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM checks WHERE resource_url = ?", (resource_url,)
            ).fetchone()
        return int(row[0]) if row else 0
