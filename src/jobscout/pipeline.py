"""One run: read each track's feeds, filter, judge, deliver, remember.

    feeds --(Source)--> jobs --(keywords)--> candidates --(judge)--> verdicts
                                                                        |
                          store (job_id, track) <--(after delivery)-- Slack

The rules that make it safe to run unattended every 30 minutes:

- A listing is marked processed only once it has a verdict and, if it was
  suitable, has actually been delivered. A failed LLM call or Slack post is
  retried next run instead of being lost.
- A failing track alerts at most once per ALERT_INTERVAL, and a failing track
  never stops the others.
- `limit` caps how many listings a track judges per run, which caps the cost.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from jobscout.judge import Judge
from jobscout.models import Job
from jobscout.slack import Notifier
from jobscout.sources.base import SourceError
from jobscout.store import Feedback, Store
from jobscout.tracks import Track

logger = logging.getLogger(__name__)

# A persistent failure (an expired session, say) alerts this often, not on
# every tick: an alert that repeats every 30 minutes is an alert people mute.
ALERT_INTERVAL = 6 * 60 * 60


class FeedReader(Protocol):
    def read(self, feed: str, want: int = 0) -> list[Job]: ...


@dataclass(frozen=True)
class Options:
    dry_run: bool = False  # judge and log, but send nothing and write nothing
    seed: bool = False  # mark what is on the feeds as processed, judge nothing
    dump: bool = False  # print what would be judged, judge nothing
    limit: int = 0  # overrides every track's limit when non-zero


@dataclass
class TrackResult:
    found: int = 0
    judged: int = 0
    notified: int = 0
    retry: int = 0
    failed: bool = False


def sort_oldest_first(jobs: Iterable[Job]) -> list[Job]:
    """Oldest first, so a run works forward in time and Slack reads in order.

    Page order is not trusted: a recommended feed is ranked, not dated.
    Undated listings go last; they are the ones a `limit` can best afford to
    leave for the next run.
    """
    return sorted(
        jobs,
        key=lambda j: (j.posted_at is None, j.posted_at.timestamp() if j.posted_at else 0.0),
    )


def collect(reader: FeedReader, track: Track) -> list[Job]:
    """A track's candidates: every feed read, merged, filtered, sorted.

    A listing in two feeds is kept once, so it is not judged (and paid for)
    twice in the same run.
    """
    merged: dict[str, Job] = {}
    for feed in track.feeds:
        for job in reader.read(feed, want=track.max_jobs):
            merged.setdefault(job.job_id, job)
    kept = [job for job in merged.values() if track.matches(job)]
    logger.info("[%s] %d listings read, %d match the query.", track.name, len(merged), len(kept))
    return sort_oldest_first(kept)


class Pipeline:
    def __init__(
        self,
        store: Store,
        judge: Judge,
        notifier: Notifier,
        options: Options | None = None,
        clock: Callable[[], float] = time.time,
        out: Callable[[str], None] = print,
    ) -> None:
        self.store = store
        self.judge = judge
        self.notifier = notifier
        self.options = options or Options()
        self.clock = clock
        self.out = out

    def run(self, reader: FeedReader, tracks: list[Track]) -> dict[str, TrackResult]:
        if not (self.options.dump or self.options.dry_run):
            self.harvest_feedback()
        results = {}
        for track in tracks:
            try:
                jobs = collect(reader, track)
            except SourceError as exc:
                logger.error("[%s] %s", track.name, exc)
                self.report_failure(track, f"{type(exc).__name__}: {exc}")
                results[track.name] = TrackResult(failed=True)
                continue
            if not (self.options.dump or self.options.dry_run):
                self.store.clear_alert(track.name)
            results[track.name] = self.process(jobs, track)
        return results

    def process(self, jobs: list[Job], track: Track) -> TrackResult:
        result = TrackResult(found=len(jobs))
        if self.options.dump:
            self.out(f"===== {track.name}: {len(jobs)} candidate(s) =====")
            self.out(summarise(jobs))
            return result
        if self.options.seed:
            for job in jobs:
                self.store.mark_processed(job.job_id, track.name)
            logger.info("[%s] seeded %d listings as processed.", track.name, len(jobs))
            return result

        limit = self.options.limit or track.limit
        for job in jobs:
            if self.store.is_processed(job.job_id, track.name):
                continue
            if limit and result.judged >= limit:
                logger.info("[%s] judged %d, the per-run limit; the rest wait.", track.name, limit)
                break

            verdict = self.judge.judge(job, track)
            result.judged += 1
            if verdict is None:
                result.retry += 1
                logger.warning("[%s] no verdict for %s; will retry.", track.name, job.job_id)
                continue
            logger.info(
                "[%s] %s suitable=%s: %s", track.name, job.job_id, verdict.suitable, verdict.reason
            )
            if self.options.dry_run:
                continue

            if verdict.suitable:
                delivery = self.notifier.post(job, verdict, track)
                if not delivery.ok:
                    result.retry += 1
                    logger.warning(
                        "[%s] delivery failed for %s; will retry.", track.name, job.job_id
                    )
                    continue
                # Recorded even without a ts (webhook): it is still the record
                # of what the card said when it was sent.
                self.store.record_notification(job, track.name, delivery.ts)
                result.notified += 1
            # Unsuitable listings are recorded too, so none is paid for twice.
            self.store.mark_processed(job.job_id, track.name)

        logger.info(
            "[%s] judged=%d notified=%d retry=%d",
            track.name,
            result.judged,
            result.notified,
            result.retry,
        )
        return result

    def report_failure(self, track: Track, message: str) -> None:
        """Alert that a track is broken, at most once per ALERT_INTERVAL.

        The throttle is per track, so one track's stale session does not mute
        the news that the other one has broken too.
        """
        if self.options.dump or self.options.dry_run:
            return
        now = self.clock()
        last = self.store.last_alert(track.name)
        if last is not None and now - last < ALERT_INTERVAL:
            logger.info(
                "[%s] already alerted %.0f min ago; staying quiet.", track.name, (now - last) / 60
            )
            return
        if self.notifier.alert(track, message):
            self.store.record_alert(track.name, now)

    def harvest_feedback(self) -> int:
        """Pull reactions left since last time. Cheap, and never fatal."""
        harvest: Callable[[], dict[str, Feedback]] | None = getattr(self.notifier, "harvest", None)
        if harvest is None:
            return 0
        try:
            changed = self.store.apply_feedback(harvest())
        except Exception as exc:
            # A rating not read today is read tomorrow; the reaction stays put.
            logger.warning("Could not read Slack feedback: %s", exc)
            return 0
        if changed:
            logger.info("Feedback: %d notification(s) newly rated.", changed)
        return changed


def summarise(jobs: list[Job]) -> str:
    """Human-readable listing for --dump and --parse-file."""
    if not jobs:
        return "Nothing to list."
    blocks = []
    for job in jobs:
        posted = job.posted_at.strftime("%Y-%m-%d %H:%M UTC") if job.posted_at else "-"
        facts = " · ".join(f"{k}={v}" for k, v in job.facts.items() if k != "skills")
        blocks.append(
            f"[{job.job_id}] {job.title}\n  {job.link}\n  posted: {posted}\n  {facts}\n"
            f"  {job.description[:200]!r}\n"
        )
    return "\n".join(blocks)
