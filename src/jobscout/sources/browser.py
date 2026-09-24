"""Site-agnostic Playwright plumbing: open, check, expand, snapshot, parse.

One browser session per run, however many tracks and feeds it reads, because
starting Chromium dominates the cost of a run. The browser is launched with
Playwright's defaults and nothing else: no stealth flags, no fingerprint
tuning, no cookie import. When a site stops serving the feed, this module
saves what it saw and raises. It does not try to get past it.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Self

from playwright.sync_api import Error as PlaywrightError

from jobscout.sources.base import (
    NothingParsed,
    PageState,
    SessionExpired,
    Source,
    UnexpectedPage,
)

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Locator, Page, Playwright

    from jobscout.models import Job

logger = logging.getLogger(__name__)

NAVIGATION_TIMEOUT_MS = 30_000
# How long one "load more" press may take to add listings before we call the
# feed finished.
GROW_TIMEOUT_MS = 10_000
# Enough presses for a few hundred listings; a feed that is still growing past
# that is a feed we should not be reading in full every 30 minutes anyway.
MAX_LOAD_MORE_PRESSES = 30


class BrowserSession:
    """A context manager around one Chromium and one source.

    `profile_dir` keeps cookies between runs, the way a person's own browser
    does, so a real source does not have to sign in on every cron tick. Leave
    it unset for a throwaway session, which is all the demo board needs.
    """

    def __init__(
        self,
        source: Source,
        *,
        snapshot_dir: Path,
        profile_dir: Path | None = None,
        headless: bool = True,
        always_snapshot: bool = False,
    ) -> None:
        self.source = source
        self.snapshot_dir = snapshot_dir
        self.profile_dir = profile_dir
        self.headless = headless
        self.always_snapshot = always_snapshot
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None

    def __enter__(self) -> Self:
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        chromium = self._playwright.chromium
        try:
            if self.profile_dir is not None:
                self.profile_dir.mkdir(parents=True, exist_ok=True)
                self._context = chromium.launch_persistent_context(
                    str(self.profile_dir), headless=self.headless
                )
            else:
                self._context = chromium.launch(headless=self.headless).new_context()
        except BaseException:
            self._playwright.stop()
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if self._context is not None:
                browser = self._context.browser
                self._context.close()
                if browser is not None:
                    browser.close()
        finally:
            if self._playwright is not None:
                self._playwright.stop()
            self._context = None
            self._playwright = None

    def read(self, feed: str, want: int = 0, now: datetime | None = None) -> list[Job]:
        """Open one feed, grow it to `want` listings, and parse it.

        Raises a `SourceError` subclass, with a snapshot attached, whenever
        the page is not a readable feed.
        """
        self.source.check_feed(feed)
        page = self._new_page()
        try:
            self._open(page, feed)
            if want:
                count = self.source.expand(page, want)
                logger.info(
                    "[%s/%s] feed holds %d listings (wanted %d)",
                    self.source.name,
                    feed,
                    count,
                    want,
                )

            html = page.content()
            jobs = self.source.parse(html, now=now)
            if not jobs:
                path = save_snapshot(page, self.snapshot_dir, f"{self.source.name}-{feed}-empty")
                raise NothingParsed(
                    f"{self.source.name}/{feed} loaded but no listings could be parsed. "
                    f"The markup has probably changed; re-parse the snapshot with "
                    f"--parse-file {path} while fixing the selectors.",
                    snapshot=str(path),
                )
            if self.always_snapshot:
                save_snapshot(page, self.snapshot_dir, f"{self.source.name}-{feed}")
            return jobs
        except PlaywrightError as exc:
            # A timeout or a dropped connection is this feed's failure, to be
            # reported like any other, not a crash that takes the run down.
            path = _try_snapshot(page, self.snapshot_dir, f"{self.source.name}-{feed}-error")
            raise UnexpectedPage(
                f"{self.source.name}/{feed}: browser error: {exc}", snapshot=path
            ) from exc
        finally:
            page.close()

    def snapshot(self, feed: str, want: int = 0) -> Path:
        """Save a feed page as served, whatever state it is in."""
        self.source.check_feed(feed)
        page = self._new_page()
        try:
            page.goto(self.source.feed_url(feed), wait_until="domcontentloaded")
            if want and self.source.page_state(page) is PageState.OK:
                self.source.expand(page, want)
            return save_snapshot(page, self.snapshot_dir, f"{self.source.name}-{feed}")
        finally:
            page.close()

    def _new_page(self) -> Page:
        if self._context is None:
            raise RuntimeError("BrowserSession used outside its `with` block.")
        page = self._context.new_page()
        page.set_default_timeout(NAVIGATION_TIMEOUT_MS)
        return page

    def _open(self, page: Page, feed: str) -> None:
        url = self.source.feed_url(feed)
        logger.info("[%s/%s] opening %s", self.source.name, feed, url)
        page.goto(url, wait_until="domcontentloaded")

        state = self.source.page_state(page)
        if state is PageState.OK:
            return

        tag = f"{self.source.name}-{feed}-{state.value}"
        path = save_snapshot(page, self.snapshot_dir, tag)
        if state is PageState.SESSION_EXPIRED:
            raise SessionExpired(
                f"{self.source.name}/{feed}: the site asked us to sign in. "
                "The saved session has expired and needs a person to renew it.",
                snapshot=str(path),
            )
        raise UnexpectedPage(
            f"{self.source.name}/{feed}: {page.url} is not the feed. See {path}.",
            snapshot=str(path),
        )


def save_snapshot(page: Page, directory: Path, tag: str) -> Path:
    """Write the page's HTML and a screenshot next to it; return the HTML path.

    The HTML is what matters: it is what `--parse-file` replays. The PNG is for
    the person reading the alert, and its failure is not worth failing over.
    """
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    safe_tag = re.sub(r"[^A-Za-z0-9_.-]+", "-", tag)
    html_path = directory / f"{safe_tag}-{stamp}.html"
    html_path.write_text(page.content(), encoding="utf-8")
    try:
        page.screenshot(path=str(html_path.with_suffix(".png")), full_page=True)
    except Exception as exc:
        logger.debug("Screenshot for %s failed: %s", html_path.name, exc)
    logger.info("Saved snapshot %s", html_path)
    return html_path


def _try_snapshot(page: Page, directory: Path, tag: str) -> str | None:
    try:
        return str(save_snapshot(page, directory, tag))
    except Exception as exc:
        logger.debug("Could not snapshot after a browser error: %s", exc)
        return None


def press_load_more(page: Page, listing_selector: str, button: Locator, want: int) -> int:
    """Press a "load more" control until `want` listings are on the page.

    Shared by sources whose feed grows by a button. Stops as soon as the button
    is gone or a press adds nothing, which is how a feed shows it has ended.
    """
    count = page.locator(listing_selector).count()
    for _ in range(MAX_LOAD_MORE_PRESSES):
        if count >= want or button.count() == 0 or not button.first.is_visible():
            break
        button.first.click()
        try:
            page.wait_for_function(
                "([sel, n]) => document.querySelectorAll(sel).length > n",
                arg=[listing_selector, count],
                timeout=GROW_TIMEOUT_MS,
            )
        except Exception:
            logger.info("Feed stopped growing at %d listings.", count)
            break
        count = page.locator(listing_selector).count()
    return count
