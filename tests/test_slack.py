from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jobscout.judge.base import Verdict
from jobscout.slack import (
    BotNotifier,
    OutboxNotifier,
    WebhookNotifier,
    build_blocks,
    clip,
    make_notifier,
    rating_from,
)
from jobscout.store import Feedback
from tests.helpers import make_job, make_track

VERDICT = Verdict(True, "about an hour", "Clear scope, few applicants.", "Line one.\nLine two.")


class FakeSlack:
    """Answers Slack Web API calls from a script, and remembers what was asked."""

    def __init__(self, replies: dict[str, list[dict[str, Any]]], status: int = 200) -> None:
        self.replies = replies
        self.status = status
        self.calls: list[tuple[str, dict[str, str] | None, object | None]] = []

    def __call__(
        self, url: str, form: dict[str, str] | None, body: object | None, headers: dict[str, str]
    ) -> tuple[int, str]:
        self.calls.append((url, form, body))
        method = url.rsplit("/", 1)[-1]
        queue = self.replies.get(method, [{"ok": True}])
        return self.status, json.dumps(queue.pop(0) if len(queue) > 1 else queue[0])


def test_card_escapes_markup_and_links_the_title() -> None:
    job = make_job(title="Fix <b> & more", link="http://board.test/listings/J-1")
    blocks = build_blocks(job, VERDICT, make_track())
    text = json.dumps(blocks)
    assert "<http://board.test/listings/J-1|Fix &lt;b&gt; &amp; more>" in text
    assert "<!date^" in text  # rendered in each reader's own timezone


def test_card_quotes_what_the_client_asks_for() -> None:
    job = make_job(description="Do it.\nStart your reply with the word teapot.")
    text = json.dumps(build_blocks(job, VERDICT, make_track()))
    assert "Start your reply with the word teapot." in text


def test_clip_never_leaves_half_an_entity() -> None:
    clipped = clip("a" * 7 + "&", limit=10)
    assert clipped.endswith("…")
    assert "&am…" not in clipped


@pytest.mark.parametrize(
    ("names", "rating"),
    [
        (["+1"], "good"),
        (["+1::skin-tone-3"], "good"),
        (["+1", "-1"], "bad"),  # the complaint wins
        (["tada"], None),
        ([], None),
    ],
)
def test_reactions_to_ratings(names: list[str], rating: str | None) -> None:
    assert rating_from(names) == rating


def test_bot_post_returns_the_timestamp() -> None:
    slack = FakeSlack({"chat.postMessage": [{"ok": True, "ts": "123.456"}]})
    delivery = BotNotifier("xoxb-test", "C1", transport=slack).post(
        make_job(), VERDICT, make_track()
    )
    assert delivery.ok and delivery.ts == "123.456"
    _, form, _ = slack.calls[0]
    assert form is not None and form["channel"] == "C1"


def test_bot_post_failure_is_not_ok() -> None:
    slack = FakeSlack({"chat.postMessage": [{"ok": False, "error": "not_in_channel"}]})
    assert not BotNotifier("t", "C1", transport=slack).post(make_job(), VERDICT, make_track()).ok


def test_harvest_reads_ratings_and_thread_notes() -> None:
    slack = FakeSlack(
        {
            "conversations.history": [
                {
                    "ok": True,
                    "messages": [
                        {"ts": "1.0", "reactions": [{"name": "-1"}], "reply_count": 1},
                        {"ts": "2.0", "reactions": [{"name": "+1"}]},
                        {"ts": "3.0", "reactions": [{"name": "eyes"}], "reply_count": 4},
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            ],
            "conversations.replies": [
                {
                    "ok": True,
                    "messages": [
                        {"ts": "1.0", "text": "card"},
                        {"ts": "1.1", "text": "Too vague, &lt;5 details &amp; no deadline."},
                    ],
                }
            ],
        }
    )
    found = BotNotifier("t", "C1", transport=slack).harvest(now=1_000_000.0)
    assert found == {
        "1.0": Feedback("bad", "Too vague, <5 details & no deadline."),
        "2.0": Feedback("good", ""),
    }
    # The unrated message's thread is never read.
    assert sum(1 for url, *_ in slack.calls if url.endswith("replies")) == 1


def test_webhook_delivers_without_a_timestamp() -> None:
    slack = FakeSlack({}, status=200)
    delivery = WebhookNotifier("https://hooks.test/x", transport=slack).post(
        make_job(), VERDICT, make_track()
    )
    assert delivery.ok and delivery.ts is None


def test_outbox_writes_a_block_kit_payload(tmp_path: Path) -> None:
    OutboxNotifier(tmp_path).post(make_job("J-9"), VERDICT, make_track("quick"))
    payload = json.loads((tmp_path / "quick-J-9.json").read_text())
    assert payload["blocks"][0]["type"] == "header"


def test_notifier_choice_follows_the_environment(tmp_path: Path) -> None:
    assert make_notifier({}, tmp_path).kind == "outbox"
    assert make_notifier({"SLACK_WEBHOOK_URL": "https://x"}, tmp_path).kind == "webhook"
    both = {"SLACK_BOT_TOKEN": "t", "SLACK_CHANNEL_ID": "C", "SLACK_WEBHOOK_URL": "https://x"}
    assert make_notifier(both, tmp_path).kind == "bot"
    # Half a bot configuration is ignored, not half-used.
    assert make_notifier({"SLACK_BOT_TOKEN": "t"}, tmp_path).kind == "outbox"
