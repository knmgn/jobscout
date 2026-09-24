"""SQLite state: what has been judged, what was sent, and what came back.

Keyed by (job_id, track) throughout. Tracks ask different questions of the
same listing, so a listing one track rejected must still reach the other.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from jobscout.models import Job

_SCHEMA = """
CREATE TABLE IF NOT EXISTS processed (
    job_id     TEXT NOT NULL,
    track      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (job_id, track)
);

-- Separate from `processed` because it answers a different question: not
-- "have we paid to judge this" but "what did we send, and was it any good".
-- facts_json freezes the card as it was when sent, because applicant counts
-- and client history keep changing after that, and a pre-filter has to be
-- argued from what was known at the time.
CREATE TABLE IF NOT EXISTS notified (
    job_id      TEXT NOT NULL,
    track       TEXT NOT NULL,
    notified_at TEXT NOT NULL,
    slack_ts    TEXT,
    title       TEXT NOT NULL,
    link        TEXT NOT NULL,
    facts_json  TEXT NOT NULL,
    rating      TEXT,
    rated_at    TEXT,
    note        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (job_id, track)
);
CREATE INDEX IF NOT EXISTS notified_ts ON notified (slack_ts);

CREATE TABLE IF NOT EXISTS alerts (
    track         TEXT PRIMARY KEY,
    last_alert_at REAL NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class Feedback:
    """A person's verdict on one Slack message, and anything they wrote under it."""

    rating: str
    note: str = ""


@dataclass(frozen=True, slots=True)
class RatedNotification:
    job_id: str
    track: str
    notified_at: str
    title: str
    link: str
    facts: dict[str, str]
    rating: str
    note: str
    slack_ts: str | None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            with conn:  # commits on success, rolls back on error
                yield conn
        finally:
            conn.close()

    # --- judging -----------------------------------------------------------

    def is_processed(self, job_id: str, track: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM processed WHERE job_id = ? AND track = ?", (job_id, track)
            ).fetchone()
        return row is not None

    def mark_processed(self, job_id: str, track: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO processed VALUES (?, ?, ?)", (job_id, track, _now())
            )

    def count_processed(self, track: str | None = None) -> int:
        return self._count("processed", track)

    # --- notifications and feedback ---------------------------------------

    def record_notification(self, job: Job, track: str, slack_ts: str | None) -> None:
        """Remember what was sent. A resend replaces the row: its ts is the live one."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO notified
                    (job_id, track, notified_at, slack_ts, title, link, facts_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (job_id, track) DO UPDATE SET
                    notified_at = excluded.notified_at, slack_ts = excluded.slack_ts,
                    title = excluded.title, link = excluded.link,
                    facts_json = excluded.facts_json
                """,
                (
                    job.job_id,
                    track,
                    _now(),
                    slack_ts,
                    job.title,
                    job.link,
                    json.dumps(job.facts, ensure_ascii=False, sort_keys=True),
                ),
            )

    def apply_feedback(self, feedback: Mapping[str, Feedback]) -> int:
        """Store a {slack_ts: Feedback} harvest; return how many rows changed.

        Only rows whose rating or note actually changed are written, so the
        count is news rather than the size of the harvest: a reaction left in
        place is read again on every run.
        """
        changed = 0
        with self._connect() as conn:
            for slack_ts, item in feedback.items():
                cursor = conn.execute(
                    "UPDATE notified SET rating = ?, note = ?, rated_at = ? "
                    "WHERE slack_ts = ? AND (rating IS NULL OR rating != ? OR note != ?)",
                    (item.rating, item.note, _now(), slack_ts, item.rating, item.note),
                )
                changed += cursor.rowcount
        return changed

    def rated(self, track: str | None = None) -> list[RatedNotification]:
        sql = (
            "SELECT job_id, track, notified_at, title, link, facts_json, rating, note, slack_ts "
            "FROM notified WHERE rating IS NOT NULL"
        )
        params: tuple[str, ...] = ()
        if track is not None:
            sql += " AND track = ?"
            params = (track,)
        with self._connect() as conn:
            rows = conn.execute(sql + " ORDER BY notified_at DESC", params).fetchall()
        return [
            RatedNotification(
                job_id=row[0],
                track=row[1],
                notified_at=row[2],
                title=row[3],
                link=row[4],
                facts=json.loads(row[5] or "{}"),
                rating=row[6],
                note=row[7] or "",
                slack_ts=row[8],
            )
            for row in rows
        ]

    def count_notified(self, track: str | None = None) -> int:
        return self._count("notified", track)

    # --- failure alerts ----------------------------------------------------

    def last_alert(self, track: str) -> float | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_alert_at FROM alerts WHERE track = ?", (track,)
            ).fetchone()
        return row[0] if row else None

    def record_alert(self, track: str, at: float) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO alerts VALUES (?, ?) "
                "ON CONFLICT (track) DO UPDATE SET last_alert_at = excluded.last_alert_at",
                (track, at),
            )

    def clear_alert(self, track: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM alerts WHERE track = ?", (track,))

    def _count(self, table: str, track: str | None) -> int:
        with self._connect() as conn:
            if track is None:
                (total,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            else:
                (total,) = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE track = ?", (track,)
                ).fetchone()
        return total
