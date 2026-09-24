from __future__ import annotations

from jobscout.report import render
from jobscout.store import RatedNotification


def row(rating: str, applicants: str, note: str = "", track: str = "quick") -> RatedNotification:
    return RatedNotification(
        job_id=f"J-{applicants}-{rating}",
        track=track,
        notified_at="2026-01-01T00:00:00+00:00",
        title="A listing",
        link="http://board.test/listings/J",
        facts={"applicants": applicants, "client_verified": "Verified"},
        rating=rating,
        note=note,
        slack_ts="1.0",
    )


def test_empty_report_explains_how_to_rate() -> None:
    text = render([], total_sent=3)
    assert "Rated 0 of 3" in text
    assert ":+1:" in text


def test_ratings_are_grouped_by_facts_that_vary() -> None:
    rows = [row("good", "1"), row("good", "3"), row("bad", "25", note="Far too crowded.")]
    text = render(rows, total_sent=5)

    assert "[quick]  67% good (2/3)" in text
    assert "> Far too crowded." in text
    # Applicants vary, so they get a table, bucketed into ranges...
    assert "--- Applicants so far ---" in text
    assert "0-5" in text and "20+" in text
    # ...while client_verified is the same everywhere and says nothing.
    assert "Client verified" not in text
