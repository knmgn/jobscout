"""The demo board over plain HTTP; no browser involved."""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from jobscout.demo_board.listings import feed, generate
from jobscout.demo_board.server import PAGE_SIZE, BoardServer


def _get(url: str) -> tuple[int, str, dict[str, str]]:
    try:
        with urllib.request.urlopen(url) as response:
            return response.status, response.read().decode(), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(), dict(error.headers)


def test_generation_is_deterministic_and_ids_are_unique() -> None:
    first, second = generate(), generate()
    assert first == second
    assert len({item.listing_id for item in first}) == len(first)


def test_newest_feed_is_chronological_and_for_you_is_not() -> None:
    listings = generate()
    newest = feed(listings, "newest")
    assert [i.minutes_ago for i in newest] == sorted(i.minutes_ago for i in newest)

    for_you = feed(listings, "for-you")
    assert all(item.theme != "other" for item in for_you)
    assert [i.minutes_ago for i in for_you] != sorted(i.minutes_ago for i in for_you)


def test_feed_page_holds_one_page_and_a_load_more_button(board_server: BoardServer) -> None:
    status, html, _ = _get(f"{board_server.url}/feeds/newest")
    assert status == 200
    assert html.count('class="listing"') == PAGE_SIZE
    assert "Load more listings" in html


def test_load_more_pages_through_to_the_end(board_server: BoardServer) -> None:
    total = len(feed(board_server.board.listings, "for-you"))
    seen, path = 0, f"/feeds/for-you/more?offset={PAGE_SIZE}"
    while path:
        status, html, headers = _get(board_server.url + path)
        assert status == 200
        seen += html.count('class="listing"')
        path = headers.get("X-Next-Page", "")
    assert PAGE_SIZE + seen == total


def test_expired_session_redirects_every_feed_to_sign_in(
    expired_board_server: BoardServer,
) -> None:
    status, html, _ = _get(f"{expired_board_server.url}/feeds/for-you")
    assert status == 200  # after following the redirect
    assert "data-page='signin'" in html


@pytest.mark.parametrize("path", ["/feeds/nope", "/listings/DB-0", "/elsewhere"])
def test_unknown_paths_are_404(board_server: BoardServer, path: str) -> None:
    status, _, _ = _get(board_server.url + path)
    assert status == 404
