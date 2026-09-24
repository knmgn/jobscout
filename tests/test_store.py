from __future__ import annotations

from pathlib import Path

from jobscout.store import Feedback, Store
from tests.helpers import make_job


def test_processed_is_keyed_by_job_and_track(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    store.mark_processed("J-1", "quick")
    store.mark_processed("J-1", "quick")  # idempotent
    assert store.is_processed("J-1", "quick")
    assert not store.is_processed("J-1", "fit")
    assert store.count_processed() == 1


def test_feedback_lands_on_the_message_it_was_left_on(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    job = make_job(facts={"applicants": "3"})
    store.record_notification(job, "quick", "111.1")
    store.record_notification(make_job("J-2"), "quick", "222.2")

    assert store.apply_feedback({"111.1": Feedback("bad", "Scope was vague.")}) == 1
    # Read again next run with nothing new: not counted as a change.
    assert store.apply_feedback({"111.1": Feedback("bad", "Scope was vague.")}) == 0
    # Unknown timestamps (someone else's message) change nothing.
    assert store.apply_feedback({"999.9": Feedback("good")}) == 0

    (row,) = store.rated()
    assert (row.job_id, row.rating, row.note) == ("J-1", "bad", "Scope was vague.")
    assert row.facts == {"applicants": "3"}  # frozen as it was when sent
    assert store.count_notified() == 2


def test_alert_state_is_per_track(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    store.record_alert("quick", 100.0)
    assert store.last_alert("quick") == 100.0
    assert store.last_alert("fit") is None
    store.clear_alert("quick")
    assert store.last_alert("quick") is None


def test_state_survives_reopening(tmp_path: Path) -> None:
    Store(tmp_path / "s.db").mark_processed("J-1", "quick")
    assert Store(tmp_path / "s.db").is_processed("J-1", "quick")
