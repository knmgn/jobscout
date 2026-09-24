"""Small builders shared by the unit tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jobscout.models import Job
from jobscout.tracks import Track

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def make_job(job_id: str = "J-1", minutes_ago: int | None = 10, **overrides: object) -> Job:
    fields: dict[str, object] = {
        "job_id": job_id,
        "title": f"Automate the weekly report ({job_id})",
        "link": f"http://board.test/listings/{job_id}",
        "description": "A small Python script that emails a CSV summary every Monday.",
        "posted_at": None if minutes_ago is None else NOW - timedelta(minutes=minutes_ago),
        "facts": {"applicants": "3", "client_verified": "Verified", "client_rating": "4.8 of 5"},
    }
    fields.update(overrides)
    return Job(**fields)  # type: ignore[arg-type]


def make_track(name: str = "quick", **overrides: object) -> Track:
    fields: dict[str, object] = {
        "name": name,
        "label": name.title(),
        "emoji": "⚡",
        "feeds": ("newest",),
        "guidance": f"Rubric for {name}.",
        "query": "",
        "limit": 8,
    }
    fields.update(overrides)
    return Track(**fields)  # type: ignore[arg-type]
