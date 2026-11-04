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
import time
from datetime import datetime, timezone


def _iso_of(epoch_seconds: float) -> str:
    return (
        datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


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
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS nudges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    sender TEXT NOT NULL,
                    stall TEXT NOT NULL,
                    route TEXT NOT NULL,
                    defect_code TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    received_epoch REAL NOT NULL,
                    message_json TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS nudge_optouts (
                    stall TEXT PRIMARY KEY,
                    wallet TEXT NOT NULL,
                    received_epoch REAL NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS payer_history (
                    payer_key TEXT NOT NULL,
                    route TEXT NOT NULL,
                    calls INTEGER NOT NULL,
                    distinct_days INTEGER NOT NULL,
                    total_units INTEGER NOT NULL,
                    first_seen_epoch REAL NOT NULL,
                    last_seen_epoch REAL NOT NULL,
                    last_day TEXT NOT NULL,
                    PRIMARY KEY (payer_key, route)
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS payer_history_days (
                    payer_key TEXT NOT NULL,
                    day TEXT NOT NULL,
                    PRIMARY KEY (payer_key, day)
                )
                """
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

    def history(self, resource_url: str, limit: int = 200) -> list[dict]:
        """Every stored check for this URL, oldest first — the read
        the fleet timeline aggregates (GET /v1/history). The checks
        table already holds the full result JSON per check; this is
        a pure read, it changes nothing."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT result_json FROM checks WHERE resource_url = ? "
                "ORDER BY id ASC LIMIT ?",
                (resource_url, max(1, min(int(limit), 1000))),
            ).fetchall()
        out = []
        for (raw,) in rows:
            try:
                out.append(json.loads(raw))
            except ValueError:
                continue
        return out

    def count(self, resource_url: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM checks WHERE resource_url = ?", (resource_url,)
            ).fetchone()
        return int(row[0]) if row else 0

    # --- nudges ----------------------------------------------------------
    # The fleet-health nudge inbox (app/nudge.py): signed inbound
    # mail about broken routes, stored verbatim, deduped on
    # (sender, route, defect, first_seen). Opt-outs live beside the
    # mail: a stall whose payTo signed an opt-out is never nudged
    # again, and enforcement reads this table (the sender engine
    # syncs it into its own registry before every send).

    def store_nudge(self, message: dict, dedupe_key: str) -> bool:
        """Store one verified nudge. Returns False when the dedupe
        key already exists — a re-sent identical nudge is a
        duplicate delivery, not a second entry."""
        now = time.time()
        defect = message.get("defect") or {}
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO nudges (dedupe_key, sender, "
                "stall, route, defect_code, first_seen, "
                "received_epoch, message_json) VALUES (?, ?, ?, ?, "
                "?, ?, ?, ?)",
                (
                    dedupe_key,
                    str(message["sender"]),
                    str(message["stall"]),
                    str(message.get("route", "")),
                    str(defect.get("code", "")),
                    str(message.get("first_seen", "")),
                    now,
                    json.dumps(message, sort_keys=True),
                ),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def nudge_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM nudges").fetchone()
        return int(row[0])

    def nudges_since(self, sender: str, since_epoch: float) -> int:
        """Verified nudges received from one sender after an epoch —
        the rate limiter's count (duplicates included: a re-send is
        still a knock)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM nudges WHERE lower(sender) = "
                "lower(?) AND received_epoch > ?",
                (sender, since_epoch),
            ).fetchone()
        return int(row[0])

    def list_nudges(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT received_epoch, message_json FROM nudges "
                "ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [
            {
                "received_at": _iso_of(row[0]),
                "message": json.loads(row[1]),
            }
            for row in rows
        ]

    def record_optout(self, stall: str, wallet: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO nudge_optouts (stall, wallet, "
                "received_epoch) VALUES (?, ?, ?)",
                (stall.rstrip("/"), wallet, time.time()),
            )
            self._conn.commit()

    def list_optouts(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT stall, wallet, received_epoch FROM "
                "nudge_optouts ORDER BY stall"
            ).fetchall()
        return [
            {"stall": row[0], "wallet": row[1], "recorded_at": _iso_of(row[2])}
            for row in rows
        ]

    def is_opted_out(self, stall: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM nudge_optouts WHERE stall = ?",
                (stall.rstrip("/"),),
            ).fetchone()
        return row is not None

    # --- payer loop memory (Next-20 #5) --------------------------------
    # Per-wallet delivery history for the loop block: deliveries
    # recognise a returning payer and personalise the offer off their
    # own proven spend. One payer_history row per (payer, route) —
    # calls, distinct days, total units, first/last seen — plus a
    # (payer, day) set so the STALL-WIDE distinct-day count stays
    # exact even when one payer spreads calls across routes on the
    # same day. The HTTP layer records exactly one row-update per
    # settled/funded delivery, after the delivery succeeded; errors
    # and unpaid 402 challenges never reach the recorder.
    #
    # payer_key is the strongest identity the delivery carried: the
    # EVM payer wallet (payload.authorization.from, lowercased) for
    # per-call payments, or a funding-credential key
    # ("fleet-pass:<id>") for pass-funded deliveries — the pass
    # ledger records only its minting payment_key, never a buyer
    # wallet, so the credential id itself is the payer identity
    # available there. (Same semantics as StallRow's payer memory.)

    def payer_history(self, payer_key: str) -> dict | None:
        """Stall-wide history for one payer identity (aggregated
        over its per-route rows), or None when this payer has never
        had a settled/funded delivery recorded."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT route, calls, total_units, first_seen_epoch, "
                "last_seen_epoch FROM payer_history WHERE payer_key = ?",
                (payer_key,),
            ).fetchall()
            days = self._conn.execute(
                "SELECT COUNT(*) FROM payer_history_days "
                "WHERE payer_key = ?",
                (payer_key,),
            ).fetchone()
        if not rows:
            return None
        return {
            "payer_key": payer_key,
            "calls": sum(r[1] for r in rows),
            "total_units": sum(r[2] for r in rows),
            "distinct_days": int(days[0]) if days else 0,
            "first_seen_epoch": min(r[3] for r in rows),
            "last_seen_epoch": max(r[4] for r in rows),
            "routes": {
                r[0]: {"calls": r[1], "total_units": r[2]} for r in rows
            },
        }

    def record_payer_delivery(
        self,
        payer_key: str,
        route: str,
        units: int,
        now_epoch: float | None = None,
    ) -> dict:
        """Record ONE settled/funded delivery and return the
        payer's history snapshot AFTER the update, plus
        ``prior_calls`` — the stall-wide call count BEFORE this
        delivery, so the caller can tell a first-time payer
        (prior_calls == 0, static block) from a returning one
        (personalised block)."""
        now = time.time() if now_epoch is None else now_epoch
        day = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            prior = self._conn.execute(
                "SELECT COALESCE(SUM(calls), 0) FROM payer_history "
                "WHERE payer_key = ?",
                (payer_key,),
            ).fetchone()
            prior_calls = int(prior[0]) if prior else 0
            existing = self._conn.execute(
                "SELECT last_day, distinct_days FROM payer_history "
                "WHERE payer_key = ? AND route = ?",
                (payer_key, route),
            ).fetchone()
            if existing is None:
                self._conn.execute(
                    "INSERT INTO payer_history (payer_key, route, "
                    "calls, distinct_days, total_units, "
                    "first_seen_epoch, last_seen_epoch, last_day) "
                    "VALUES (?, ?, 1, 1, ?, ?, ?, ?)",
                    (payer_key, route, units, now, now, day),
                )
            else:
                # Days only move forward: a day string later than
                # the stored last_day is a day this (payer, route)
                # has never been seen on.
                new_day = 1 if day > existing[0] else 0
                self._conn.execute(
                    "UPDATE payer_history SET calls = calls + 1, "
                    "distinct_days = distinct_days + ?, "
                    "total_units = total_units + ?, "
                    "last_seen_epoch = ?, last_day = MAX(last_day, ?) "
                    "WHERE payer_key = ? AND route = ?",
                    (new_day, units, now, day, payer_key, route),
                )
            self._conn.execute(
                "INSERT OR IGNORE INTO payer_history_days "
                "(payer_key, day) VALUES (?, ?)",
                (payer_key, day),
            )
            self._conn.commit()
        snapshot = self.payer_history(payer_key)
        assert snapshot is not None  # we just wrote a row for this payer
        snapshot["prior_calls"] = prior_calls
        return snapshot
