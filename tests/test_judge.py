from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from jobscout.judge import make_judge
from jobscout.judge.base import verdict_from
from jobscout.judge.openai_judge import DEFAULT_MODEL, OpenAIJudge
from jobscout.judge.prompt import (
    FORMAT_FOLLOW_CLIENT,
    FORMAT_THREE_LINES,
    SYSTEM_PROMPT,
    application_instructions,
    user_prompt,
)
from jobscout.judge.stub import StubJudge
from tests.helpers import make_job, make_track

GOOD = {"suitable": True, "duration": "an hour", "reason": "Clear scope.", "proposal": "A. B. C."}


def test_verdict_accepts_the_schema_and_lays_out_the_draft() -> None:
    verdict = verdict_from(GOOD)
    assert verdict is not None
    assert verdict.suitable is True
    assert verdict.proposal == "A.\nB.\nC."


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {k: v for k, v in GOOD.items() if k != "reason"},
        {**GOOD, "suitable": "false"},  # truthy string: must not become a yes
    ],
)
def test_off_schema_replies_are_no_verdict_not_false(payload: Any) -> None:
    assert verdict_from(payload) is None


@pytest.mark.parametrize(
    ("draft", "laid_out"),
    [
        # Numbered answers stay whole: the number ties them to the question.
        ("1. Yes. Really.\n2. No.", "1. Yes. Really.\n2. No."),
        # An opening word on its own line does not stop the rest being split.
        ("teapot\nI would build it. Then test it.", "teapot\nI would build it.\nThen test it."),
        # A TODO that names nothing is noise; one that names something stays.
        ("A.\n[TODO: ...]\n[TODO: your rate]", "A.\n[TODO: your rate]"),
    ],
)
def test_draft_layout(draft: str, laid_out: str) -> None:
    verdict = verdict_from({**GOOD, "proposal": draft})
    assert verdict is not None
    assert verdict.proposal == laid_out


def test_prompt_puts_the_rubric_after_the_generic_rules() -> None:
    prompt = user_prompt(make_job(), make_track(guidance="ONLY TINY JOBS"))
    assert prompt.index("Formatting rule") < prompt.index("ONLY TINY JOBS")
    assert "Facts read off the listing:\n- Applicants so far: 3" in prompt
    assert "ONLY TINY JOBS" not in SYSTEM_PROMPT


def test_client_instructions_switch_the_format_and_come_last() -> None:
    description = "Build a sheet.\n\nPlease answer the following:\n1. Tools?\n2. Timeline?"
    prompt = user_prompt(make_job(description=description), make_track())
    # The rules are alternatives: given both, a model splits the difference.
    assert FORMAT_THREE_LINES not in prompt
    assert FORMAT_FOLLOW_CLIENT in prompt
    assert prompt.rstrip().endswith("2. Timeline?")


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("Fix the sheet.", ""),
        (
            "Fix it.\nStart your reply with the word pineapple.",
            "Start your reply with the word pineapple.",
        ),
        ("Intro.\nIn your proposal, please answer: why?", "In your proposal, please answer: why?"),
    ],
)
def test_application_instructions(description: str, expected: str) -> None:
    assert application_instructions(description) == expected


def test_stub_is_deterministic_and_says_it_is_a_stub() -> None:
    track = make_track()
    crowded = make_job(facts={"applicants": "40", "client_verified": "Verified"})
    first, second = StubJudge().judge(crowded, track), StubJudge().judge(crowded, track)
    assert first == second
    assert first.suitable is False
    assert first.reason.startswith("Stub judge")
    assert StubJudge().judge(make_job(), track).suitable is True


def test_make_judge_picks_the_stub_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert make_judge().name == "stub"


class FakeCompletions:
    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        message = SimpleNamespace(content=self.reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _judge(reply: str | Exception) -> tuple[OpenAIJudge, FakeCompletions]:
    completions = FakeCompletions(reply)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return OpenAIJudge(client=client, model="test-model"), completions


def test_openai_judge_asks_for_the_schema_and_parses_the_reply() -> None:
    judge, completions = _judge(json.dumps(GOOD))
    verdict = judge.judge(make_job(), make_track())
    assert verdict is not None and verdict.suitable
    request = completions.calls[0]
    assert request["model"] == "test-model"
    assert request["response_format"]["json_schema"]["strict"] is True


@pytest.mark.parametrize("reply", [TimeoutError("slow"), "not json", json.dumps({"x": 1})])
def test_openai_failures_are_retries_not_rejections(reply: str | Exception) -> None:
    judge, _ = _judge(reply)
    assert judge.judge(make_job(), make_track()) is None


def test_a_blank_model_setting_falls_back_to_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "")
    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions("{}")))
    assert OpenAIJudge(client=client).model == DEFAULT_MODEL
