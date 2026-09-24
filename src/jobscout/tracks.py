"""Tracks: what to look for, how to judge it, and how much of it to judge.

A track bundles the settings that have to agree with each other: the feeds
to read, the keyword query that narrows them, the rubric the judge applies
and the per-run cap on judging. Two tracks differ only in these values; the
pipeline never branches on which track it is running.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from jobscout.models import Job

_OR = re.compile(r"\s+OR\s+")


class ConfigError(RuntimeError):
    """tracks.toml is missing, malformed, or describes nothing to run."""


@cache
def _term_pattern(term: str) -> re.Pattern[str]:
    """A case-insensitive whole-word matcher for one query term.

    Word boundaries matter: without them "RAG" matches "storage" and
    "average", and every false match is an LLM call spent saying no. They are
    only added at ends that are word characters, so "n8n" still matches and
    "C++" does not become unmatchable.
    """
    prefix = r"\b" if re.match(r"\w", term) else ""
    suffix = r"\b" if re.search(r"\w$", term) else ""
    return re.compile(prefix + re.escape(term) + suffix, re.IGNORECASE)


def parse_query(query: str) -> tuple[str, ...]:
    """Split `"quoted phrase" OR word OR ...` into its terms.

    The same syntax most job-search boxes accept, so a query can be pasted
    from wherever it was first tried out. Only OR is supported: the query is
    a cheap pre-filter, not a search engine.
    """
    terms = []
    for part in _OR.split(query.strip()):
        term = part.strip().strip("()").strip().strip('"').strip()
        if term:
            terms.append(term)
    return tuple(dict.fromkeys(terms))


@dataclass(frozen=True)
class Track:
    name: str
    label: str
    emoji: str
    feeds: tuple[str, ...]
    guidance: str
    query: str = ""
    # Listings to load per feed before filtering. Feeds are not keyword-
    # filtered, so this has to be generous or nothing survives the query.
    max_jobs: int = 30
    # Listings judged per run: the LLM cost ceiling for one cron tick.
    limit: int = 8
    enabled: bool = True
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def keywords(self) -> tuple[str, ...]:
        return parse_query(self.query)

    @property
    def heading(self) -> str:
        return f"{self.emoji} {self.label}".strip()

    def matches(self, job: Job) -> bool:
        """Whether a listing mentions any of the track's terms.

        A track without a query takes the whole feed, which is the right
        reading for a feed the site already ranks for you.
        """
        terms = self.keywords
        if not terms:
            return True
        haystack = f"{job.title}\n{job.description}\n{job.facts.get('skills', '')}"
        return any(_term_pattern(term).search(haystack) for term in terms)


_KNOWN_KEYS = {
    "name", "label", "emoji", "feeds", "guidance", "query", "max_jobs", "limit", "enabled",
}  # fmt: skip


def _track(raw: dict[str, Any], index: int, path: Path) -> Track:
    name = str(raw.get("name", "")).strip()
    if not name:
        raise ConfigError(f"Track #{index + 1} in {path} has no name.")

    feeds = raw.get("feeds", [])
    if isinstance(feeds, str):
        feeds = [feeds]
    feeds = tuple(dict.fromkeys(str(f).strip() for f in feeds if str(f).strip()))
    if not feeds:
        raise ConfigError(f"Track {name!r} lists no feeds.")

    guidance = str(raw.get("guidance", "")).strip()
    if not guidance:
        # The rubric is what makes a track a track; without it the judge
        # would be answering a question nobody asked.
        raise ConfigError(f"Track {name!r} has no guidance for the judge.")

    try:
        max_jobs, limit = int(raw.get("max_jobs", 30)), int(raw.get("limit", 8))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Track {name!r}: max_jobs and limit must be integers.") from exc

    return Track(
        name=name,
        label=str(raw.get("label", name)),
        emoji=str(raw.get("emoji", "")),
        feeds=feeds,
        guidance=guidance,
        query=str(raw.get("query", "")).strip(),
        max_jobs=max_jobs,
        limit=limit,
        enabled=bool(raw.get("enabled", True)),
        # Kept rather than rejected, so a typo shows up in --list-tracks
        # instead of silently doing nothing.
        extras={k: v for k, v in raw.items() if k not in _KNOWN_KEYS},
    )


@dataclass(frozen=True)
class Config:
    tracks: tuple[Track, ...]
    # "package.module:ClassName" of the Source to read with.
    source_class: str
    source_options: dict[str, Any]
    profile_dir: str | None

    def select(self, names: list[str] | None = None) -> list[Track]:
        """The tracks to run: those named, or every enabled one."""
        if not names:
            chosen = [track for track in self.tracks if track.enabled]
            if not chosen:
                raise ConfigError("Every track is disabled; nothing to do.")
            return chosen
        by_name = {track.name: track for track in self.tracks}
        unknown = [name for name in names if name not in by_name]
        if unknown:
            raise ConfigError(
                f"Unknown track(s): {', '.join(unknown)}. Available: {', '.join(by_name)}"
            )
        # Naming a disabled track runs it: an explicit request beats the default.
        return [by_name[name] for name in names]


def load(path: Path) -> Config:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"No configuration at {path}.") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

    source = raw.get("source", {})
    source_class = str(source.get("class", "")).strip()
    if ":" not in source_class:
        raise ConfigError(f'{path}: [source] needs class = "package.module:ClassName".')

    entries = raw.get("track", [])
    if not entries:
        raise ConfigError(f"{path} defines no [[track]] entries.")
    tracks = tuple(_track(entry, i, path) for i, entry in enumerate(entries))

    names = [track.name for track in tracks]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ConfigError(f"Duplicate track name(s) in {path}: {', '.join(duplicates)}")

    profile_dir = source.get("profile_dir")
    return Config(
        tracks=tracks,
        source_class=source_class,
        source_options=dict(source.get("options", {})),
        profile_dir=str(profile_dir) if profile_dir else None,
    )
