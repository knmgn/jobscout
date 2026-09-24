"""Drive a real Chromium against the demo board through BrowserSession."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from jobscout.demo_board import BoardServer
from jobscout.demo_board.listings import feed
from jobscout.demo_board.server import PAGE_SIZE
from jobscout.models import Job
from jobscout.sources import NothingParsed, SessionExpired
from jobscout.sources.browser import BrowserSession
from jobscout.sources.demo import DemoSource

pytestmark = pytest.mark.browser


def test_reads_a_feed_and_presses_load_more_until_it_has_enough(
    board_server: BoardServer, tmp_path: Path
) -> None:
    expected = [item.listing_id for item in feed(board_server.board.listings, "newest")]
    with BrowserSession(DemoSource(board_server.url), snapshot_dir=tmp_path) as session:
        first_page = session.read("newest")
        grown = session.read("newest", want=20)
        everything = session.read("for-you", want=1000)

    assert [job.job_id for job in first_page] == expected[:PAGE_SIZE]
    assert len(grown) >= 20
    assert [job.job_id for job in grown] == expected[: len(grown)]
    # A feed that runs out stops the presses instead of timing out.
    assert len(everything) == len(feed(board_server.board.listings, "for-you"))
    assert not list(tmp_path.iterdir())  # nothing went wrong, so no snapshots


def test_expired_session_raises_with_a_snapshot(
    expired_board_server: BoardServer, tmp_path: Path
) -> None:
    with (
        BrowserSession(DemoSource(expired_board_server.url), snapshot_dir=tmp_path) as session,
        pytest.raises(SessionExpired) as caught,
    ):
        session.read("newest")

    snapshot = Path(caught.value.snapshot or "")
    assert snapshot.exists()
    # Playwright re-serialises the DOM, so attribute quoting is its own.
    assert 'data-page="signin"' in snapshot.read_text()
    assert snapshot.with_suffix(".png").exists()


class _BrokenParser(DemoSource):
    """The board after a redesign the selectors have not caught up with."""

    def parse(self, html: str, now: datetime | None = None) -> list[Job]:
        return []


def test_a_feed_that_parses_to_nothing_is_an_error_not_a_quiet_day(
    board_server: BoardServer, tmp_path: Path
) -> None:
    with (
        BrowserSession(_BrokenParser(board_server.url), snapshot_dir=tmp_path) as session,
        pytest.raises(NothingParsed) as caught,
    ):
        session.read("newest")

    assert Path(caught.value.snapshot or "").exists()
