"""The boundary between the pipeline and any particular site.

Everything a real site would need lives behind `Source`: its URLs, its
selectors, how it signals an expired session, its ID format and its wording.
The browser plumbing in `browser.py` and the whole pipeline above it only call
these methods, which is what keeps the rest of the code site-agnostic.

A source is deliberately split into what needs a live page and what does not.
`parse` takes plain HTML, so a snapshot saved from a failed run can be re-parsed
offline (`--parse-file`) while the selectors are retuned.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

    from jobscout.models import Job


class PageState(Enum):
    """What a feed URL actually served."""

    OK = "ok"
    # The site sent us to sign in. The only response to this is to stop and
    # tell a person; nothing here tries to sign back in.
    SESSION_EXPIRED = "session_expired"
    # Something else: an error page, a maintenance notice, a redesign.
    UNEXPECTED = "unexpected"


class SourceError(RuntimeError):
    """A feed could not be read. Reported per track, never crashes the run."""

    def __init__(self, message: str, snapshot: str | None = None) -> None:
        super().__init__(message)
        # Path of the HTML saved at the moment of failure, if one was saved.
        self.snapshot = snapshot


class SessionExpired(SourceError):
    """The signed-in session is gone. Needs a person, so it is alerted on."""


class UnexpectedPage(SourceError):
    """The page was neither the feed nor a sign-in page."""


class NothingParsed(SourceError):
    """The feed loaded but no listings came out of it.

    Raised rather than returning an empty list, because from the outside a
    broken parser looks exactly like a quiet day.
    """


class Source(ABC):
    """One site's knowledge, and nothing else."""

    #: Short identifier, used in logs, snapshot names and config.
    name: str

    #: Feed names this source can read, as written in tracks.toml.
    feeds: tuple[str, ...]

    @abstractmethod
    def feed_url(self, feed: str) -> str:
        """The URL a track's `feed` name stands for."""

    @abstractmethod
    def page_state(self, page: Page) -> PageState:
        """Classify what a navigation landed on. Called before anything else."""

    @abstractmethod
    def expand(self, page: Page, want: int) -> int:
        """Grow the feed in place until it holds `want` listings or ends.

        Returns how many listings the page holds afterwards. How a site loads
        more (a button, infinite scroll, pagination) is its own business.
        """

    @abstractmethod
    def parse(self, html: str, now: datetime | None = None) -> list[Job]:
        """Turn a feed page's HTML into jobs, in page order.

        `now` anchors relative dates ("2 hours ago"); it defaults to the
        current time and is a parameter so tests and replays are deterministic.
        """

    def check_feed(self, feed: str) -> None:
        if feed not in self.feeds:
            raise ValueError(
                f"Source {self.name!r} has no feed {feed!r}; known feeds: {', '.join(self.feeds)}"
            )
