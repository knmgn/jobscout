"""`--report`: what the 👍 / 👎 on past cards say about where to cut.

Ratings are grouped by card facts only, because a rule written against a card
fact can run before the LLM ever sees the listing, which is where it saves
money. The report is a table for a person to read, not a rule the pipeline
applies by itself: whether to stop sending, say, listings with 20+ applicants
is a decision to make once, looking at the numbers.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from itertools import pairwise

from jobscout.models import FACT_LABELS
from jobscout.store import RatedNotification

_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _bucket(edges: tuple[float, ...], unit: str) -> Callable[[str], str]:
    """Group a numeric fact into ranges, so 3 and 4 applicants are one row."""

    def bucket(value: str) -> str:
        match = _NUMBER.search(value)
        if match is None:
            return value
        number = float(match.group(0))
        for low, high in pairwise(edges):
            if low <= number < high:
                return f"{low:g}-{high:g} {unit}".strip()
        return f"{edges[-1]:g}+ {unit}".strip()

    return bucket


def _first_word(value: str) -> str:
    # "Fixed price: $150" and "Hourly: $20-$40" group by kind, not by amount.
    return value.split(":", 1)[0].strip()


FACETS: tuple[tuple[str, Callable[[str], str]], ...] = (
    ("applicants", _bucket((0, 5, 10, 20), "")),
    ("client_verified", str),
    ("client_rating", _bucket((0, 4.0, 4.5, 4.8), "")),
    ("client_history", _bucket((0, 1, 5, 20), "")),
    ("experience", str),
    ("duration", str),
    ("budget", _first_word),
)


def _share(rows: list[RatedNotification]) -> str:
    good = sum(1 for row in rows if row.rating == "good")
    return f"{100 * good / len(rows):3.0f}% good ({good}/{len(rows)})"


def render(rows: list[RatedNotification], total_sent: int) -> str:
    lines = [f"Rated {len(rows)} of {total_sent} notification(s)."]
    if not rows:
        lines += [
            "",
            "Nothing rated yet. React to a card in Slack with :+1: or :-1:,",
            "and reply in its thread to say why. The next run reads them.",
        ]
        return "\n".join(lines)

    lines.append("")
    for track in sorted({row.track for row in rows}):
        subset = [row for row in rows if row.track == track]
        lines.append(f"[{track}] {_share(subset)}")

    bad = [row for row in rows if row.rating == "bad"]
    if bad:
        lines += ["", f"--- rated bad: {len(bad)} ---"]
        for row in bad:
            lines.append(f"  {row.notified_at[:10]}  [{row.track}] {row.title[:70]}")
            lines += [f"      > {line[:100]}" for line in row.note.splitlines()]
            if row.link:
                lines.append(f"      {row.link}")

    for key, bucket in FACETS:
        groups: dict[str, list[RatedNotification]] = defaultdict(list)
        for row in rows:
            if row.facts.get(key):
                groups[bucket(row.facts[key])].append(row)
        if len(groups) < 2:
            continue  # one value for everything says nothing about where to cut
        lines += ["", f"--- {FACT_LABELS[key]} ---"]
        for value, subset in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            lines.append(f"  {value[:30]:30s} {_share(subset)}")
    return "\n".join(lines)
