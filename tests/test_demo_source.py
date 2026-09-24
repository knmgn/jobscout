"""DemoSource.parse against HTML the board renders; no browser involved."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from jobscout.demo_board.listings import generate
from jobscout.demo_board.server import DemoBoard
from jobscout.models import Job
from jobscout.sources.demo import DemoSource, posted_at

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
source = DemoSource("http://board.test")


def test_parses_every_card_on_a_feed_page_in_page_order() -> None:
    board = DemoBoard()
    html = board.feed_page("newest")
    jobs = source.parse(html, now=NOW)

    expected = [item.listing_id for item in sorted(board.listings, key=lambda i: i.minutes_ago)]
    assert [job.job_id for job in jobs] == expected[: len(jobs)]
    assert len(jobs) == 8


def test_card_fields_become_job_fields() -> None:
    item = next(i for i in generate() if "\n" in i.description and i.client_rating is not None)
    html = DemoBoard(listings=[item]).feed_page("newest")
    (job,) = source.parse(html, now=NOW)

    assert job.title == item.title
    # The feed's ?ref= tracking parameter is dropped, so the link is stable.
    assert job.link == f"http://board.test/listings/{item.listing_id}"
    assert job.description == item.description
    assert job.posted_at == NOW - timedelta(minutes=item.minutes_ago)
    assert job.facts["budget"] == item.budget
    assert job.facts["applicants"] == str(item.applicants)
    assert job.facts["client_rating"] == f"{item.client_rating:.1f} of 5"
    assert job.facts["skills"] == ", ".join(item.skills)


def test_unrated_client_has_no_rating_rather_than_a_bad_one() -> None:
    item = replace(generate()[0], client_rating=None)
    (job,) = source.parse(DemoBoard(listings=[item]).feed_page("newest"), now=NOW)
    assert "client_rating" not in job.facts


def test_page_without_listings_parses_to_nothing() -> None:
    assert source.parse(DemoBoard().signin_page("/feeds/newest"), now=NOW) == []


@pytest.mark.parametrize(
    ("text", "age"),
    [
        ("Posted just now by X", timedelta(0)),
        ("Posted 1 minute ago by X", timedelta(minutes=1)),
        ("Posted 45 minutes ago by X", timedelta(minutes=45)),
        ("Posted 3 hours ago by X", timedelta(hours=3)),
        ("Posted yesterday by X", timedelta(days=1)),
        ("Posted 4 days ago by X", timedelta(days=4)),
    ],
)
def test_relative_ages_decode(text: str, age: timedelta) -> None:
    assert posted_at(text, NOW) == NOW - age


def test_undated_card_has_no_time() -> None:
    assert posted_at("Posted by X", NOW) is None


def test_unknown_feed_is_rejected() -> None:
    with pytest.raises(ValueError, match="no feed"):
        source.feed_url("everything")


def test_job_rejects_fact_keys_outside_the_fixed_set() -> None:
    with pytest.raises(ValueError, match="Unknown fact keys"):
        Job(job_id="1", title="t", link="l", description="d", facts={"spend": "$1"})
