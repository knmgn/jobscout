"""What a judge returns, and how a raw reply becomes one (or does not)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

from jobscout.models import Job
from jobscout.tracks import Track

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Verdict:
    suitable: bool
    # The judge's estimate of the work, in its own words ("about an hour").
    duration: str
    reason: str
    # A draft for a person to edit and send, never sent as-is.
    proposal: str


class Judge(Protocol):
    name: str

    def judge(self, job: Job, track: Track) -> Verdict | None:
        """A verdict, or None when there is no usable answer this time.

        None means "ask again next run", never "no". The pipeline leaves the
        listing unprocessed, so a timeout or a malformed reply costs a retry
        rather than a silently dropped listing.
        """
        ...


VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "suitable": {"type": "boolean"},
        "duration": {"type": "string"},
        "reason": {"type": "string"},
        "proposal": {"type": "string"},
    },
    "required": ["suitable", "duration", "reason", "proposal"],
    "additionalProperties": False,
}


def verdict_from(payload: object) -> Verdict | None:
    """Validate a decoded reply. Anything off-schema is no verdict, not False."""
    if not isinstance(payload, dict):
        logger.warning("Judge reply is a %s, not an object.", type(payload).__name__)
        return None
    missing = [key for key in VERDICT_SCHEMA["required"] if key not in payload]
    if missing:
        logger.warning("Judge reply is missing %s.", ", ".join(missing))
        return None
    if not isinstance(payload["suitable"], bool):
        # "false" as a string is truthy; guessing here is how a no becomes a yes.
        logger.warning("Judge reply has suitable=%r, not a boolean.", payload["suitable"])
        return None
    return Verdict(
        suitable=payload["suitable"],
        duration=str(payload["duration"]).strip(),
        reason=str(payload["reason"]).strip(),
        proposal=one_sentence_per_line(str(payload["proposal"])),
    )


def one_sentence_per_line(text: str) -> str:
    """Lay a short draft out one sentence per line.

    Asked for three lines, models often return three sentences run together,
    which is harder to read in Slack and to paste into a reply box. Drafts
    that already have line breaks (numbered answers, say) are left alone.
    """
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if len(lines) >= 2:
        return "\n".join(lines)
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", " ".join(lines)) if s]
    return "\n".join(sentences)
