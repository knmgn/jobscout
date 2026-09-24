"""`make demo`, end to end: the CLI, a real browser, the board, the outbox."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobscout.cli import main

pytestmark = pytest.mark.browser

CONFIG = str(Path(__file__).parents[2] / "tracks.toml")


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ("OPENAI_API_KEY", "SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID", "SLACK_WEBHOOK_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # so a developer's own .env is not picked up


def test_demo_run_writes_cards_and_a_second_run_sends_nothing_new(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert main(["--config", CONFIG, "--demo", "--out", str(out)]) == 0

    cards = sorted((out / "slack").glob("*.json"))
    assert cards, "the demo should produce at least one card"
    payload = json.loads(cards[0].read_text())
    assert payload["blocks"][0]["type"] == "header"

    assert main(["--config", CONFIG, "--demo", "--out", str(out), "--track", "quick"]) == 0
    assert sorted((out / "slack").glob("quick-*.json")) == [
        c for c in cards if c.name.startswith("quick-")
    ]


def test_expired_demo_session_alerts_and_fails(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert main(["--config", CONFIG, "--demo", "expired", "--out", str(out)]) == 1
    assert list((out / "slack").glob("alert-*.json"))
    assert list((out / "snapshots").glob("*session_expired*.html"))
