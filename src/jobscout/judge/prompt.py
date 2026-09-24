"""The judge's prompt. The system half is fixed; the track's rubric is config.

Everything that is tuned often (what a track wants, what disqualifies a
listing) lives in tracks.toml as `guidance`. The system prompt only says how
to read a listing at all, so it can stay the same across tracks and a change
in behaviour can always be traced to the config file.
"""

from __future__ import annotations

import re

from jobscout.models import FACT_LABELS, Job
from jobscout.tracks import Track

# Long listings add cost, not signal, but screening questions sit at the very
# end, so the cut is generous and the questions are also carried separately.
MAX_DESCRIPTION_CHARS = 12_000
MAX_INSTRUCTION_CHARS = 1_500

SYSTEM_PROMPT = """\
You screen freelance job listings for a software engineer who builds \
automation, integrations and AI features. For each listing you decide whether \
it fits the rubric you are given, and you draft a short reply for the engineer \
to edit.

Reply with a JSON object with these fields:
- suitable: true if the listing fits the rubric, otherwise false.
- duration: your estimate of the work, in a few words (for example "about an hour").
- reason: one sentence explaining the decision, citing what in the listing drove it.
- proposal: a draft reply, following the formatting rule in the user message.

How to read a listing:
- Judge this listing, not its category. "Scraping" or "AI agent" can mean an \
hour or six months; only the described work tells you which.
- The described work is the evidence for how big the job is. The posted \
budget is not: clients often enter a placeholder and settle the price later.
- The facts listed under "Facts read off the listing" come from the page, not \
from the client's own description. Use them to decide whether the listing is \
worth answering at all, as the rubric directs.
- When the evidence is genuinely mixed, answer true. A wrong true costs the \
reader a glance; a wrong false loses the opportunity.
- You know nothing about the engineer's history, rates or portfolio. Never \
claim experience, past projects or availability in the proposal. Where the \
client asks for any of these, write "[TODO: ...]" naming what the engineer \
must fill in.\
"""

FORMAT_THREE_LINES = """\
Formatting rule for proposal: exactly three sentences, each on its own line, \
no greeting and no sign-off. Line 1: what you would build. Line 2: how you \
would approach it. Line 3: what you need from the client to start.\
"""

# An alternative to the rule above, not an addition to it: given both, a model
# splits the difference and answers three of ten questions.
FORMAT_FOLLOW_CLIENT = """\
Formatting rule for proposal: the client has said what a reply must contain \
(quoted at the end of this message). Follow it literally and completely: if \
it asks for an opening word, start with that word; if it asks questions, \
answer every one, numbered as the client numbered them, each on its own line. \
There is no length limit. No greeting and no sign-off. This affects the \
proposal only; decide suitable exactly as you would otherwise.\
"""

_APPLY_MARKERS = re.compile(
    r"""(?:
          (?:please\s+)?answer\s+(?:the\s+following|these|below)
        | please\s+answer\b
        | in\s+your\s+(?:proposal|application|reply|response)[,:]?\s+
          (?:please\s+)?(?:answer|include|tell|state|start|begin|mention)
        | (?:start|begin|open)\s+your\s+(?:proposal|application|reply|message)\s+with
        | include\s+the\s+(?:word|phrase|code)\b
        | screening\s+questions?\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def application_instructions(description: str) -> str:
    """The part of a listing that says what a reply must contain, or "".

    Taken from the start of the line that asks to the end of the listing,
    because what follows the ask is the list of questions itself.
    """
    text = description.strip()
    match = _APPLY_MARKERS.search(text)
    if match is None:
        return ""
    start = text.rfind("\n", 0, match.start()) + 1
    return text[start:].strip()[:MAX_INSTRUCTION_CHARS]


def facts_block(job: Job) -> str:
    lines = [
        f"- {label}: {job.facts[key]}" for key, label in FACT_LABELS.items() if job.facts.get(key)
    ]
    return "Facts read off the listing:\n" + "\n".join(lines) if lines else ""


def user_prompt(job: Job, track: Track) -> str:
    """Listing first, then the formatting rule, then the rubric, then any ask.

    Order is deliberate: models weight what they read last, so the track's
    rubric comes after the generic rules, and the client's own instructions
    (repeated from the description, where truncation would hit them first)
    come after that.
    """
    instructions = application_instructions(job.description)
    parts = [
        f"Title: {job.title}",
        f"Description:\n{job.description[:MAX_DESCRIPTION_CHARS]}",
        facts_block(job),
        FORMAT_FOLLOW_CLIENT if instructions else FORMAT_THREE_LINES,
        f"Rubric for this track ({track.label}):\n{track.guidance}",
    ]
    if instructions:
        parts.append(f"What the client asks a reply to contain:\n{instructions}")
    return "\n\n".join(part for part in parts if part)
