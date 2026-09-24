from __future__ import annotations

from pathlib import Path

import pytest

from jobscout.tracks import ConfigError, load, parse_query
from tests.helpers import make_job, make_track

REPO_CONFIG = Path(__file__).parents[1] / "tracks.toml"

MINIMAL = """
[source]
class = "jobscout.sources.demo:DemoSource"
[source.options]
base_url = "http://board.test"

[[track]]
name = "quick"
feeds = ["newest"]
guidance = "g"
{extra}
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "tracks.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_shipped_config_loads() -> None:
    config = load(REPO_CONFIG)
    assert [t.name for t in config.tracks] == ["quick", "fit"]
    assert all(t.guidance for t in config.tracks)


def test_query_syntax_is_quoted_phrases_joined_by_or() -> None:
    assert parse_query('"google sheets" OR RAG OR (n8n) OR  "google sheets"') == (
        "google sheets",
        "RAG",
        "n8n",
    )


@pytest.mark.parametrize(
    ("text", "term", "expected"),
    [
        ("Build a RAG pipeline", "RAG", True),
        ("Cloud storage average", "RAG", False),  # whole words only
        ("Our n8n flows broke", "n8n", True),
        ("Needs C++ experience", "C++", True),
        ("Google   Sheets formula", "google sheets", False),  # phrase is literal
        ("GOOGLE SHEETS formula", "google sheets", True),  # but case-insensitive
    ],
)
def test_matching(text: str, term: str, expected: bool) -> None:
    track = make_track(query=f'"{term}"')
    assert track.matches(make_job(title=text, description="")) is expected


def test_skills_count_towards_a_match() -> None:
    track = make_track(query="zapier")
    job = make_job(title="Fix a flow", description="It broke.", facts={"skills": "Zapier"})
    assert track.matches(job)


def test_no_query_takes_everything() -> None:
    assert make_track(query="").matches(make_job())


def test_select_defaults_to_enabled_and_runs_a_named_disabled_track(tmp_path: Path) -> None:
    config = load(
        write(
            tmp_path,
            MINIMAL.format(
                extra='enabled = false\n[[track]]\nname = "fit"\nfeeds = ["newest"]\nguidance = "g"'
            ),
        )
    )
    assert [t.name for t in config.select()] == ["fit"]
    assert [t.name for t in config.select(["quick"])] == ["quick"]
    with pytest.raises(ConfigError, match="Unknown track"):
        config.select(["nope"])


def test_unknown_keys_are_kept_for_list_tracks(tmp_path: Path) -> None:
    config = load(write(tmp_path, MINIMAL.format(extra="limt = 3")))
    assert config.tracks[0].extras == {"limt": 3}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (MINIMAL.format(extra="").replace('guidance = "g"', ""), "no guidance"),
        (MINIMAL.format(extra="").replace('feeds = ["newest"]', "feeds = []"), "no feeds"),
        (MINIMAL.format(extra='limit = "many"'), "integers"),
        (
            MINIMAL.format(extra="") + '[[track]]\nname = "quick"\nfeeds = ["a"]\nguidance = "g"',
            "Duplicate",
        ),
        ("[[track]]\nname='x'", r"\[source\]"),
        (MINIMAL.format(extra="").split("[[track]]")[0], "no \\[\\[track\\]\\]"),
        ("not toml [", "not valid TOML"),
    ],
)
def test_config_errors_say_what_is_wrong(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load(write(tmp_path, text))
