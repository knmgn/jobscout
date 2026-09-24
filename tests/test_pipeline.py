"""The pipeline's promises, against fakes: no browser, no network."""

from __future__ import annotations

from pathlib import Path

import pytest

from jobscout.judge.base import Verdict
from jobscout.models import Job
from jobscout.pipeline import ALERT_INTERVAL, Options, Pipeline, collect, sort_oldest_first
from jobscout.slack import Delivery
from jobscout.sources.base import SessionExpired
from jobscout.store import Feedback, Store
from jobscout.tracks import Track
from tests.helpers import make_job, make_track


class FakeReader:
    def __init__(self, feeds: dict[str, list[Job]] | Exception) -> None:
        self.feeds = feeds
        self.reads: list[tuple[str, int]] = []

    def read(self, feed: str, want: int = 0) -> list[Job]:
        self.reads.append((feed, want))
        if isinstance(self.feeds, Exception):
            raise self.feeds
        return self.feeds[feed]


class FakeJudge:
    name = "fake"

    def __init__(self, answers: dict[str, bool | None] | None = None) -> None:
        self.answers = answers or {}
        self.seen: list[tuple[str, str]] = []

    def judge(self, job: Job, track: Track) -> Verdict | None:
        self.seen.append((job.job_id, track.name))
        answer = self.answers.get(job.job_id, True)
        return None if answer is None else Verdict(answer, "an hour", "because", "draft")


class FakeNotifier:
    kind = "fake"

    def __init__(self, failing: set[str] | None = None) -> None:
        self.failing = failing or set()
        self.posted: list[tuple[str, str]] = []
        self.alerts: list[tuple[str, str]] = []
        self.feedback: dict[str, Feedback] = {}

    def post(self, job: Job, verdict: Verdict, track: Track) -> Delivery:
        if job.job_id in self.failing:
            return Delivery(False)
        self.posted.append((job.job_id, track.name))
        return Delivery(True, f"ts-{job.job_id}")

    def alert(self, track: Track, message: str) -> bool:
        self.alerts.append((track.name, message))
        return True

    def harvest(self) -> dict[str, Feedback]:
        return self.feedback


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "state.db")


def test_sorting_is_oldest_first_with_undated_last() -> None:
    jobs = [make_job("new", 1), make_job("undated", None), make_job("old", 90)]
    assert [j.job_id for j in sort_oldest_first(jobs)] == ["old", "new", "undated"]


def test_collect_merges_feeds_once_and_applies_the_query() -> None:
    reader = FakeReader(
        {
            "a": [
                make_job("J-1", title="Python script for invoices"),
                make_job("J-2", title="Logo", description="A logo."),
            ],
            "b": [make_job("J-1", title="Python script for invoices")],
        }
    )
    track = make_track(feeds=("a", "b"), query='"python script"', max_jobs=25)
    jobs = collect(reader, track)
    assert [j.job_id for j in jobs] == ["J-1"]
    assert reader.reads == [("a", 25), ("b", 25)]


def test_suitable_jobs_are_sent_and_everything_judged_is_remembered(store: Store) -> None:
    judge, notifier = FakeJudge({"J-2": False}), FakeNotifier()
    pipeline = Pipeline(store, judge, notifier)
    reader = FakeReader({"newest": [make_job("J-1"), make_job("J-2")]})

    pipeline.run(reader, [make_track()])
    assert notifier.posted == [("J-1", "quick")]
    assert store.is_processed("J-1", "quick") and store.is_processed("J-2", "quick")

    # Next run: nothing is judged or sent twice.
    pipeline.run(reader, [make_track()])
    assert len(judge.seen) == 2
    assert len(notifier.posted) == 1


def test_no_verdict_and_failed_delivery_are_retried_next_run(store: Store) -> None:
    judge = FakeJudge({"J-1": None})
    notifier = FakeNotifier(failing={"J-2"})
    reader = FakeReader({"newest": [make_job("J-1"), make_job("J-2")]})

    result = Pipeline(store, judge, notifier).run(reader, [make_track()])["quick"]
    assert result.retry == 2
    assert not store.is_processed("J-1", "quick")
    assert not store.is_processed("J-2", "quick")

    judge.answers.clear()
    notifier.failing.clear()
    Pipeline(store, judge, notifier).run(reader, [make_track()])
    assert sorted(notifier.posted) == [("J-1", "quick"), ("J-2", "quick")]


def test_the_same_job_is_judged_by_each_track_independently(store: Store) -> None:
    judge = FakeJudge()
    reader = FakeReader({"newest": [make_job("J-1")]})
    Pipeline(store, judge, FakeNotifier()).run(reader, [make_track("quick"), make_track("fit")])
    assert judge.seen == [("J-1", "quick"), ("J-1", "fit")]


def test_limit_caps_judging_per_run_oldest_first(store: Store) -> None:
    judge = FakeJudge()
    jobs = [make_job(f"J-{age}", age) for age in (5, 50, 500)]
    Pipeline(store, judge, FakeNotifier(), Options(limit=2)).run(
        FakeReader({"newest": jobs}), [make_track()]
    )
    assert [job_id for job_id, _ in judge.seen] == ["J-500", "J-50"]


def test_dry_run_judges_but_sends_and_stores_nothing(store: Store) -> None:
    notifier = FakeNotifier()
    Pipeline(store, FakeJudge(), notifier, Options(dry_run=True)).run(
        FakeReader({"newest": [make_job()]}), [make_track()]
    )
    assert notifier.posted == []
    assert store.count_processed() == 0


def test_seed_marks_the_backlog_without_judging(store: Store) -> None:
    judge = FakeJudge()
    Pipeline(store, judge, FakeNotifier(), Options(seed=True)).run(
        FakeReader({"newest": [make_job("J-1"), make_job("J-2")]}), [make_track()]
    )
    assert judge.seen == []
    assert store.count_processed("quick") == 2


def test_dump_prints_candidates_and_touches_nothing(store: Store) -> None:
    printed: list[str] = []
    judge = FakeJudge()
    Pipeline(store, judge, FakeNotifier(), Options(dump=True), out=printed.append).run(
        FakeReader({"newest": [make_job("J-7")]}), [make_track()]
    )
    assert judge.seen == [] and store.count_processed() == 0
    assert "[J-7]" in "\n".join(printed)


def test_a_failing_track_alerts_once_per_interval_and_the_others_carry_on(store: Store) -> None:
    now = [1_000_000.0]
    notifier = FakeNotifier()
    pipeline = Pipeline(store, FakeJudge(), notifier, clock=lambda: now[0])

    class OneBrokenFeed(FakeReader):
        def read(self, feed: str, want: int = 0) -> list[Job]:
            if feed == "broken":
                raise SessionExpired("sign in again")
            return [make_job()]

    reader = OneBrokenFeed({})
    tracks = [make_track("quick", feeds=("broken",)), make_track("fit", feeds=("fine",))]

    results = pipeline.run(reader, tracks)
    assert results["quick"].failed and not results["fit"].failed
    assert notifier.posted == [("J-1", "fit")]
    assert [name for name, _ in notifier.alerts] == ["quick"]

    now[0] += ALERT_INTERVAL - 60  # still broken, too soon to say so again
    pipeline.run(reader, tracks)
    assert len(notifier.alerts) == 1

    now[0] += 120  # past the interval: remind
    pipeline.run(reader, tracks)
    assert len(notifier.alerts) == 2


def test_recovery_resets_the_alert_throttle(store: Store) -> None:
    notifier = FakeNotifier()
    pipeline = Pipeline(store, FakeJudge(), notifier, clock=lambda: 1_000_000.0)
    track = make_track()

    pipeline.run(FakeReader(SessionExpired("x")), [track])
    pipeline.run(FakeReader({"newest": []}), [track])  # works again
    pipeline.run(FakeReader(SessionExpired("x")), [track])  # a fresh outage
    assert len(notifier.alerts) == 2


def test_feedback_is_harvested_before_the_run(store: Store) -> None:
    notifier = FakeNotifier()
    store.record_notification(make_job("J-1"), "quick", "ts-J-1")
    notifier.feedback = {"ts-J-1": Feedback("good", "")}
    Pipeline(store, FakeJudge(), notifier).run(FakeReader({"newest": []}), [make_track()])
    assert [row.rating for row in store.rated()] == ["good"]
