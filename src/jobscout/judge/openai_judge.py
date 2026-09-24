"""The real judge: one chat completion per listing, constrained to a schema."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from jobscout.judge.base import VERDICT_SCHEMA, Verdict, verdict_from
from jobscout.judge.prompt import SYSTEM_PROMPT, user_prompt
from jobscout.models import Job
from jobscout.tracks import Track

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIJudge:
    name = "openai"

    def __init__(self, client: Any | None = None, model: str | None = None) -> None:
        if client is None:
            from openai import OpenAI  # optional dependency: `pip install .[openai]`

            client = OpenAI()  # reads OPENAI_API_KEY
        self._client = client
        self.model = model or os.getenv("OPENAI_MODEL", DEFAULT_MODEL)

    def judge(self, job: Job, track: Track) -> Verdict | None:
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                temperature=0.2,
                # Structured output: the API itself refuses to return anything
                # off-schema, so parsing failures are rare rather than routine.
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "verdict", "strict": True, "schema": VERDICT_SCHEMA},
                },
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt(job, track)},
                ],
            )
            content = response.choices[0].message.content or ""
        except Exception as exc:
            # Any failure here (network, quota, refusal) is a retry next run,
            # never a reason to stop judging the rest of the listings.
            logger.warning("[%s] judge call failed for %s: %s", track.name, job.job_id, exc)
            return None

        try:
            return verdict_from(json.loads(content))
        except json.JSONDecodeError:
            logger.warning("[%s] judge returned non-JSON for %s", track.name, job.job_id)
            return None
