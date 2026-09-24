"""Slack: build the card, deliver it, and read back what people thought of it.

Three ways out, picked by what is configured:

- a bot token and channel: chat.postMessage. The only one that returns a
  message timestamp, and the timestamp is what a reaction is matched back to
  a listing by, so it is the only one that closes the feedback loop.
- an Incoming Webhook: delivers, but there is nothing to rate afterwards.
- neither: the Block Kit payload is written to a directory, so the demo can
  show exactly what would have been posted without any account.

Feedback is read by polling channel history at the start of each run. The
alternative (Events API, Socket Mode) needs something listening all the time;
this wakes up on a cron schedule anyway, and one history call is cheap.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from jobscout.judge.base import Verdict
from jobscout.judge.prompt import application_instructions
from jobscout.models import FACT_LABELS, Job
from jobscout.store import Feedback
from jobscout.tracks import Track

logger = logging.getLogger(__name__)

API_ROOT = "https://slack.com/api/"
TIMEOUT_SECONDS = 10
LOOKBACK_DAYS = 14
# Slack rejects a text object over 3000 characters.
MAX_TEXT = 2900

# What a reaction means. Anything else is ignored rather than guessed at:
# people react for their own reasons, and a stray 🎉 is not a verdict.
RATINGS = {"+1": "good", "-1": "bad"}
# Both on one message: keep the complaint, it is the one that turns into a rule.
_PRECEDENCE = ("bad", "good")

# Shown under the card, in this order: the facts that decide whether it is
# worth replying at all, readable without opening the listing.
_CARD_FACTS = ("applicants", "client_verified", "client_rating", "client_history", "budget")

_PARTIAL_ENTITY = re.compile(r"&[#0-9a-zA-Z]*$")

# (url, form fields or None, json body or None, headers) -> (status, body)
Transport = Callable[[str, dict[str, str] | None, object | None, dict[str, str]], tuple[int, str]]


class SlackError(RuntimeError):
    """Slack answered, and said no."""


def http_post(
    url: str,
    form: dict[str, str] | None,
    body: object | None,
    headers: dict[str, str],
) -> tuple[int, str]:
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers = {**headers, "Content-Type": "application/x-www-form-urlencoded"}
    else:
        data = json.dumps(body).encode()
        headers = {**headers, "Content-Type": "application/json; charset=utf-8"}
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(errors="replace")


# --- the card -----------------------------------------------------------------


def escape(text: str) -> str:
    """Neutralise the characters Slack's mrkdwn treats as markup."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def clip(text: str, limit: int = MAX_TEXT) -> str:
    """Escape, then cut to length without leaving half an entity behind."""
    text = escape(text.strip())
    if len(text) <= limit:
        return text
    return _PARTIAL_ENTITY.sub("", text[: limit - 1]).rstrip() + "…"


def _posted(job: Job) -> str:
    if job.posted_at is None:
        return "-"
    # Slack renders this in each reader's own timezone; the text after | is
    # the fallback for clients that cannot.
    epoch = int(job.posted_at.timestamp())
    fallback = job.posted_at.strftime("%Y-%m-%d %H:%M UTC")
    return f"<!date^{epoch}^{{date_short_pretty}} {{time}}|{fallback}>"


def build_blocks(job: Job, verdict: Verdict, track: Track) -> list[dict[str, Any]]:
    title = clip(job.title, 140)
    heading = f"<{escape(job.link)}|{title}>" if job.link else title
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": track.heading, "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{heading}*"}},
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Estimated work*\n{clip(verdict.duration, 200) or '-'}",
                },
                {"type": "mrkdwn", "text": f"*Posted*\n{_posted(job)}"},
            ],
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Why*\n{clip(verdict.reason)}"}},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                # The label says "draft" on purpose: the judge is told it knows
                # nothing about the applicant, but a model can still invent a
                # claim, and a reply sent unread is how that reaches a client.
                "text": f"*Draft reply (check every claim before sending)*\n"
                f"```{clip(verdict.proposal)}```",
            },
        },
    ]

    # After the draft, because it is what the draft is checked against.
    instructions = application_instructions(job.description)
    if instructions:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*:warning: The client asks replies to*\n{clip(instructions, 1200)}",
                },
            }
        )

    facts = " · ".join(
        f"{FACT_LABELS[key]}: {job.facts[key]}" for key in _CARD_FACTS if job.facts.get(key)
    )
    if facts:
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": clip(facts, 400)}]}
        )
    blocks.append(
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "React :+1: or :-1:; reply in thread to say why."}
            ],
        }
    )
    return blocks


def alert_blocks(track: Track, message: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*:warning: {clip(track.heading, 100)} cannot read listings*\n"
                f"```{clip(message)}```",
            },
        }
    ]


# --- delivery -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Delivery:
    """Whether the message went out, and the id a reaction will point back to.

    A webhook delivery succeeds with ts None, so `ok` is never inferred from ts.
    """

    ok: bool
    ts: str | None = None


class Notifier(Protocol):
    kind: str

    def post(self, job: Job, verdict: Verdict, track: Track) -> Delivery: ...

    def alert(self, track: Track, message: str) -> bool: ...


class BotNotifier:
    kind = "bot"

    def __init__(self, token: str, channel: str, transport: Transport = http_post) -> None:
        self.token = token
        self.channel = channel
        self._transport = transport

    def _call(self, method: str, **params: str) -> dict[str, Any]:
        status, text = self._transport(
            API_ROOT + method, params, None, {"Authorization": f"Bearer {self.token}"}
        )
        if status != 200:
            raise SlackError(f"{method}: HTTP {status}")
        payload = json.loads(text)
        if not payload.get("ok"):
            raise SlackError(f"{method}: {payload.get('error', 'unknown error')}")
        return payload

    def _post(self, blocks: list[dict[str, Any]], text: str) -> str:
        payload = self._call(
            "chat.postMessage",
            channel=self.channel,
            blocks=json.dumps(blocks, ensure_ascii=False),
            text=text,
            unfurl_links="false",
            unfurl_media="false",
        )
        return str(payload.get("ts", ""))

    def post(self, job: Job, verdict: Verdict, track: Track) -> Delivery:
        try:
            ts = self._post(build_blocks(job, verdict, track), f"{track.heading}: {job.title}")
        except (SlackError, OSError, ValueError) as exc:
            # No fallback to a webhook: a bad token or an uninvited bot is a
            # standing misconfiguration, and a silent fallback would hide it.
            logger.error("[%s] Slack post failed for %s: %s", track.name, job.job_id, exc)
            return Delivery(False)
        return Delivery(True, ts)

    def alert(self, track: Track, message: str) -> bool:
        try:
            self._post(alert_blocks(track, message), f"{track.heading} cannot read listings")
        except (SlackError, OSError, ValueError) as exc:
            logger.error("Could not send the failure alert: %s", exc)
            return False
        return True

    def harvest(
        self, lookback_days: int = LOOKBACK_DAYS, now: float | None = None
    ) -> dict[str, Feedback]:
        """Every rated message in the window, as {message ts: Feedback}.

        Messages with no recognised reaction are left out, so the result is
        "what has been judged", not a mix of that and silence. Threads are
        only read on rated messages: a note without a verdict is conversation.
        """
        oldest = (now if now is not None else time.time()) - lookback_days * 86_400
        found: dict[str, Feedback] = {}
        cursor = ""
        while True:
            params = {"channel": self.channel, "oldest": f"{oldest:.6f}", "limit": "200"}
            if cursor:
                params["cursor"] = cursor
            payload = self._call("conversations.history", **params)
            for message in payload.get("messages", []):
                rating = rating_from([r.get("name", "") for r in message.get("reactions", [])])
                if rating is None:
                    continue
                ts = str(message.get("ts", ""))
                note = self._thread_note(ts) if message.get("reply_count") else ""
                found[ts] = Feedback(rating, note)
            cursor = (payload.get("response_metadata") or {}).get("next_cursor", "")
            if not cursor:
                return found

    def _thread_note(self, thread_ts: str) -> str:
        try:
            payload = self._call("conversations.replies", channel=self.channel, ts=thread_ts)
        except (SlackError, OSError, ValueError) as exc:
            # A note that cannot be read must not cost the rating it came with.
            logger.warning("Could not read the thread on %s: %s", thread_ts, exc)
            return ""
        replies = [
            unescape(m.get("text") or "").strip()
            for m in payload.get("messages", [])
            if str(m.get("ts", "")) != thread_ts  # the first one is the card itself
        ]
        return "\n".join(r for r in replies if r)


def unescape(text: str) -> str:
    """Undo Slack's escaping of message text, so a note reads as it was typed."""
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def rating_from(reaction_names: list[str]) -> str | None:
    # Slack appends skin tones: "+1::skin-tone-3".
    seen = {RATINGS.get(name.split("::", 1)[0]) for name in reaction_names}
    return next((rating for rating in _PRECEDENCE if rating in seen), None)


class WebhookNotifier:
    kind = "webhook"

    def __init__(self, url: str, transport: Transport = http_post) -> None:
        self.url = url
        self._transport = transport

    def _send(self, blocks: list[dict[str, Any]], text: str) -> bool:
        try:
            status, body = self._transport(self.url, None, {"text": text, "blocks": blocks}, {})
        except OSError as exc:
            logger.error("Slack webhook failed: %s", exc)
            return False
        if status != 200:
            logger.error("Slack webhook rejected the message (%s): %s", status, body[:200])
        return status == 200

    def post(self, job: Job, verdict: Verdict, track: Track) -> Delivery:
        return Delivery(
            self._send(build_blocks(job, verdict, track), f"{track.heading}: {job.title}")
        )

    def alert(self, track: Track, message: str) -> bool:
        return self._send(alert_blocks(track, message), f"{track.heading} cannot read listings")


class OutboxNotifier:
    """Writes each payload to a JSON file instead of sending it.

    Paste a file's `blocks` into Slack's Block Kit Builder to see the card.
    """

    kind = "outbox"

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _write(self, name: str, text: str, blocks: list[dict[str, Any]]) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{re.sub(r'[^A-Za-z0-9_.-]+', '-', name)}.json"
        path.write_text(
            json.dumps({"text": text, "blocks": blocks}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info("Wrote Slack payload %s", path)
        return path

    def post(self, job: Job, verdict: Verdict, track: Track) -> Delivery:
        self._write(
            f"{track.name}-{job.job_id}",
            f"{track.heading}: {job.title}",
            build_blocks(job, verdict, track),
        )
        return Delivery(True)

    def alert(self, track: Track, message: str) -> bool:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        self._write(
            f"alert-{track.name}-{stamp}",
            f"{track.heading} cannot read listings",
            alert_blocks(track, message),
        )
        return True


def make_notifier(env: dict[str, str], outbox: Path) -> Notifier:
    """The best delivery the environment allows. Token and channel go together."""
    token = env.get("SLACK_BOT_TOKEN", "").strip()
    channel = env.get("SLACK_CHANNEL_ID", "").strip()
    if token and channel:
        return BotNotifier(token, channel)
    if token or channel:
        logger.warning(
            "SLACK_BOT_TOKEN and SLACK_CHANNEL_ID only work together; ignoring the one set."
        )
    webhook = env.get("SLACK_WEBHOOK_URL", "").strip()
    if webhook:
        return WebhookNotifier(webhook)
    logger.info("No Slack credentials; writing payloads to %s instead.", outbox)
    return OutboxNotifier(outbox)
