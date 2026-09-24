"""A local listings site for the demo, the integration test and screenshots.

It behaves like the kind of feed the scout is built for, in the ways that
matter to the browser code: only the first page of listings is in the HTML,
the rest arrive through a "Load more" button, dates are relative ("posted 12
minutes ago"), and when the session is "expired" every feed redirects to a
sign-in page. It has no search, which is why keyword filtering happens on our
side.

Standard library only, so `make demo` needs nothing beyond the package.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import time
from urllib.parse import parse_qs, quote, urlsplit

from jobscout.demo_board.listings import FEEDS, Listing, feed, generate

PAGE_SIZE = 8

_STYLE = """
:root { color-scheme: light dark; --fg: #1d2330; --muted: #5b6475; --line: #d9dde5;
        --card: #ffffff; --bg: #f3f4f7; --accent: #3b5bdb; }
@media (prefers-color-scheme: dark) {
  :root { --fg: #e6e8ee; --muted: #a0a8b8; --line: #343b4a; --card: #1c212c;
          --bg: #12161e; --accent: #8ea6ff; } }
* { box-sizing: border-box; }
body { margin: 0; font: 15px/1.5 system-ui, sans-serif; color: var(--fg); background: var(--bg); }
header.site { padding: 16px; border-bottom: 1px solid var(--line); background: var(--card); }
header.site strong { font-size: 18px; }
header.site p { margin: 4px 0 0; color: var(--muted); font-size: 13px; }
nav a { margin-right: 16px; color: var(--accent); }
main { max-width: 760px; margin: 0 auto; padding: 16px; }
.listing { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
           padding: 16px; margin-bottom: 12px; }
.listing h2 { font-size: 17px; margin: 0; }
.listing h2 a { color: var(--accent); text-decoration: none; }
.posted { color: var(--muted); font-size: 13px; margin: 2px 0 8px; }
.summary { margin: 0 0 10px; white-space: pre-line; }
dl.facts { display: flex; flex-wrap: wrap; gap: 4px 16px; margin: 0 0 8px; font-size: 13px; }
dl.facts div { display: flex; gap: 4px; }
dl.facts dt { color: var(--muted); }
dl.facts dd { margin: 0; }
ul.skills { list-style: none; padding: 0; margin: 0; display: flex; flex-wrap: wrap; gap: 6px; }
ul.skills li { border: 1px solid var(--line); border-radius: 999px; padding: 1px 10px;
               font-size: 12px; }
button.more { display: block; margin: 16px auto; padding: 8px 20px; font: inherit; cursor: pointer;
              border: 1px solid var(--accent); color: var(--accent); background: none;
              border-radius: 6px; }
form.signin { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
              padding: 16px; }
form.signin label { display: block; margin-bottom: 8px; }
"""

_LOAD_MORE_SCRIPT = """
document.querySelector('button.more')?.addEventListener('click', async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  const response = await fetch(button.dataset.next);
  document.querySelector('#listings').insertAdjacentHTML('beforeend', await response.text());
  const next = response.headers.get('X-Next-Page');
  if (next) { button.dataset.next = next; button.disabled = false; } else { button.remove(); }
});
"""


def _ago(minutes: int) -> str:
    """The board's own wording for age; the demo source is what decodes it."""
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    return "yesterday" if days == 1 else f"{days} days ago"


def _fact(label: str, value: str) -> str:
    return f"<div><dt>{escape(label)}</dt><dd>{escape(value)}</dd></div>"


def render_card(item: Listing, minutes_ago: int) -> str:
    rating = "No ratings yet"
    if item.client_rating is not None:
        rating = f"{item.client_rating:.1f} of 5"
    facts = "".join(
        (
            _fact("Budget", item.budget),
            _fact("Length", item.duration),
            _fact("Level", item.experience),
            _fact("Applicants", str(item.applicants)),
            _fact("Client rating", rating),
            _fact("Client", "Verified" if item.client_verified else "Not verified"),
            _fact("Past hires", str(item.client_hires)),
            _fact("Based in", item.client_location),
        )
    )
    skills = "".join(f"<li>{escape(skill)}</li>" for skill in item.skills)
    return (
        f'<article class="listing" data-listing-id="{escape(item.listing_id)}">'
        f'<h2><a href="/listings/{quote(item.listing_id)}?ref=feed">{escape(item.title)}</a></h2>'
        f'<p class="posted">Posted {_ago(minutes_ago)} by {escape(item.company)}</p>'
        f'<p class="summary">{escape(item.description)}</p>'
        f'<dl class="facts">{facts}</dl>'
        f'<ul class="skills">{skills}</ul>'
        "</article>"
    )


def _page(title: str, body: str, page_kind: str, script: str = "") -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{escape(title)} · Demo Board</title><style>{_STYLE}</style></head>"
        f"<body data-page='{page_kind}'>"
        "<header class='site'><strong>Demo Board</strong>"
        "<p>Invented listings for trying out jobscout. Nothing here is real.</p>"
        "<nav><a href='/feeds/newest'>Newest</a><a href='/feeds/for-you'>For you</a></nav>"
        f"</header><main>{body}</main>"
        + (f"<script>{script}</script>" if script else "")
        + "</body></html>"
    )


@dataclass
class DemoBoard:
    """The board's state. `expired` makes every feed bounce to sign-in."""

    listings: list[Listing] = field(default_factory=generate)
    expired: bool = False
    # Ages are relative to this instant, so a board left running keeps aging
    # like a real feed instead of freezing at its first render.
    started_at: float = field(default_factory=time)

    def minutes_ago(self, item: Listing) -> int:
        return item.minutes_ago + int((time() - self.started_at) // 60)

    def cards(self, feed_name: str, offset: int) -> tuple[str, int | None]:
        """One page of cards, and the next offset (None at the end)."""
        items = feed(self.listings, feed_name)
        chunk = items[offset : offset + PAGE_SIZE]
        html = "".join(render_card(item, self.minutes_ago(item)) for item in chunk)
        next_offset = offset + PAGE_SIZE
        return html, (next_offset if next_offset < len(items) else None)

    def feed_page(self, feed_name: str) -> str:
        html, next_offset = self.cards(feed_name, 0)
        heading = {"newest": "Newest listings", "for-you": "Recommended for you"}[feed_name]
        more = (
            f"<button class='more' data-next='/feeds/{feed_name}/more?offset={next_offset}'>"
            "Load more listings</button>"
            if next_offset is not None
            else ""
        )
        body = f"<h1>{heading}</h1><section id='listings'>{html}</section>{more}"
        return _page(heading, body, "feed", _LOAD_MORE_SCRIPT)

    def listing_page(self, listing_id: str) -> str | None:
        item = next((i for i in self.listings if i.listing_id == listing_id), None)
        if item is None:
            return None
        return _page(item.title, render_card(item, self.minutes_ago(item)), "listing")

    def signin_page(self, next_path: str) -> str:
        body = (
            "<h1>Sign in to continue</h1>"
            "<p>Your session has expired. (This is the demo board's stand-in for a "
            "real site's sign-in page; there is nothing to sign in to.)</p>"
            f"<form class='signin' method='post' action='/signin?next={quote(next_path)}'>"
            "<label>Email <input name='email' type='email'></label>"
            "<label>Password <input name='password' type='password'></label>"
            "<button type='submit' disabled>Sign in</button></form>"
        )
        return _page("Sign in", body, "signin")


def _handler(board: DemoBoard) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            url = urlsplit(self.path)
            parts = [p for p in url.path.split("/") if p]
            query = parse_qs(url.query)

            if parts in ([], ["feeds"]):
                return self._redirect("/feeds/newest")

            if parts[:1] == ["feeds"] and len(parts) >= 2 and parts[1] in FEEDS:
                if board.expired:
                    return self._redirect(f"/signin?next={quote(url.path)}")
                if len(parts) == 2:
                    return self._send(board.feed_page(parts[1]))
                if parts[2:] == ["more"]:
                    try:
                        offset = max(0, int(query.get("offset", ["0"])[0]))
                    except ValueError:
                        return self._send("bad offset", HTTPStatus.BAD_REQUEST)
                    html, next_offset = board.cards(parts[1], offset)
                    headers = {}
                    if next_offset is not None:
                        headers["X-Next-Page"] = f"/feeds/{parts[1]}/more?offset={next_offset}"
                    return self._send(html, headers=headers)

            if parts[:1] == ["listings"] and len(parts) == 2:
                page = board.listing_page(parts[1])
                if page is not None:
                    return self._send(page)

            if parts == ["signin"]:
                return self._send(board.signin_page(query.get("next", ["/"])[0]))

            return self._send(
                _page("Not found", "<h1>Not found</h1>", "error"), HTTPStatus.NOT_FOUND
            )

        def _redirect(self, location: str) -> None:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _send(
            self,
            html: str,
            status: HTTPStatus = HTTPStatus.OK,
            headers: dict[str, str] | None = None,
        ) -> None:
            data = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            # Quiet by default: the scout's own log is the one worth reading.
            pass

    return Handler


class BoardServer:
    """The board on a background thread, for `make demo` and tests.

    Port 0 picks a free port, so parallel test runs do not collide.
    """

    def __init__(self, board: DemoBoard | None = None, host: str = "127.0.0.1", port: int = 0):
        self.board = board or DemoBoard()
        self._server = ThreadingHTTPServer((host, port), _handler(self.board))
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> BoardServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()

    def serve_forever(self) -> None:
        self._server.serve_forever()
