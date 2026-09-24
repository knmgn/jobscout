"""The source for the local demo board, and the reference for writing others.

Everything specific to the board is in this file: its URLs, its selectors,
its sign-in redirect, its ID format and the way it words dates and facts. A
source for another site is a copy of this file with those answers changed.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from jobscout.models import Job
from jobscout.sources.base import PageState, Source
from jobscout.sources.browser import press_load_more

if TYPE_CHECKING:
    from playwright.sync_api import Page

LISTING = "article.listing[data-listing-id]"
LOAD_MORE = re.compile(r"load more", re.I)

# The board's fact labels, mapped onto the pipeline's fixed keys.
_FACTS = {
    "Budget": "budget",
    "Length": "duration",
    "Level": "experience",
    "Applicants": "applicants",
    "Client rating": "client_rating",
    "Client": "client_verified",
    "Past hires": "client_history",
    "Based in": "client_location",
}

_AGE = re.compile(r"(\d+)\s+(minute|hour|day)s?\s+ago", re.I)
_UNIT = {"minute": timedelta(minutes=1), "hour": timedelta(hours=1), "day": timedelta(days=1)}


class DemoSource(Source):
    name = "demo"
    feeds = ("newest", "for-you")

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def feed_url(self, feed: str) -> str:
        self.check_feed(feed)
        return f"{self.base_url}/feeds/{feed}"

    def page_state(self, page: Page) -> PageState:
        if urlsplit(page.url).path.startswith("/signin"):
            return PageState.SESSION_EXPIRED
        if page.locator("body[data-page='feed']").count():
            return PageState.OK
        return PageState.UNEXPECTED

    def expand(self, page: Page, want: int) -> int:
        button = page.get_by_role("button", name=LOAD_MORE)
        return press_load_more(page, LISTING, button, want)

    def parse(self, html: str, now: datetime | None = None) -> list[Job]:
        now = now or datetime.now(UTC)
        soup = BeautifulSoup(html, "html.parser")
        jobs: list[Job] = []
        seen: set[str] = set()
        for card in soup.select(LISTING):
            job = self._parse_card(card, now)
            if job is not None and job.job_id not in seen:
                seen.add(job.job_id)
                jobs.append(job)
        return jobs

    def _parse_card(self, card: Tag, now: datetime) -> Job | None:
        anchor = card.select_one("h2 a[href]")
        job_id = str(card.get("data-listing-id", "")).strip()
        if anchor is None or not job_id:
            return None

        facts = {}
        for pair in card.select("dl.facts > div"):
            label, value = _text(pair.find("dt")), _text(pair.find("dd"))
            key = _FACTS.get(label)
            if key and value:
                facts[key] = value
        # An unrated client is not a badly rated one; say nothing rather than
        # hand the judge something that reads like a low score.
        if facts.get("client_rating", "").lower().startswith("no rating"):
            del facts["client_rating"]
        skills = [_text(li) for li in card.select("ul.skills li")]
        if skills:
            facts["skills"] = ", ".join(s for s in skills if s)

        return Job(
            job_id=job_id,
            title=_text(anchor),
            link=self._canonical(str(anchor["href"])),
            description=_text(card.select_one("p.summary"), keep_lines=True),
            posted_at=posted_at(_text(card.select_one("p.posted")), now),
            facts=facts,
        )

    def _canonical(self, href: str) -> str:
        """Absolute, without the query string the feed adds for tracking."""
        parts = urlsplit(urljoin(self.base_url + "/", href))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def posted_at(text: str, now: datetime) -> datetime | None:
    """Decode the board's "Posted 12 minutes ago" wording into a time."""
    lowered = text.lower()
    if "just now" in lowered:
        return now
    if "yesterday" in lowered:
        return now - timedelta(days=1)
    match = _AGE.search(text)
    if match is None:
        return None
    return now - int(match.group(1)) * _UNIT[match.group(2).lower()]


def _text(node: Tag | None, keep_lines: bool = False) -> str:
    if node is None:
        return ""
    if keep_lines:
        lines = (" ".join(line.split()) for line in node.get_text().splitlines())
        return "\n".join(lines).strip()
    return " ".join(node.get_text(" ").split())
