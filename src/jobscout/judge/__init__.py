"""Deciding whether a listing fits a track."""

from __future__ import annotations

import logging
import os

from jobscout.judge.base import Judge, Verdict

logger = logging.getLogger(__name__)

__all__ = ["Judge", "Verdict", "make_judge"]


def make_judge() -> Judge:
    """The OpenAI judge when a key is set, otherwise the deterministic stub."""
    if os.getenv("OPENAI_API_KEY", "").strip():
        from jobscout.judge.openai_judge import OpenAIJudge

        return OpenAIJudge()
    logger.info("OPENAI_API_KEY is not set; using the deterministic stub judge.")
    from jobscout.judge.stub import StubJudge

    return StubJudge()
