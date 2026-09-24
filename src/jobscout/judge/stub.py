"""A deterministic stand-in for the LLM, so the demo runs with no API key.

It does not read the rubric. It applies a few fixed rules to the card facts
and says so in every reason, so nobody mistakes its output for a real
judgement. Same input, same verdict, which also makes it the judge the
pipeline tests run against.
"""

from __future__ import annotations

import re

from jobscout.judge.base import Verdict
from jobscout.judge.prompt import application_instructions
from jobscout.models import Job
from jobscout.tracks import Track

MAX_APPLICANTS = 15
MIN_RATING = 4.0


def _number(text: str) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", text or "")
    return float(match.group(0)) if match else None


class StubJudge:
    name = "stub"

    def judge(self, job: Job, track: Track) -> Verdict:
        problems = []
        applicants = _number(job.facts.get("applicants", ""))
        if applicants is not None and applicants > MAX_APPLICANTS:
            problems.append(f"{applicants:.0f} applicants already")
        rating = _number(job.facts.get("client_rating", ""))
        if rating is not None and rating < MIN_RATING:
            problems.append(f"client rated {rating:.1f}")
        if job.facts.get("client_verified", "").lower().startswith("not"):
            problems.append("client not verified")

        suitable = not problems
        reason = "; ".join(problems) if problems else "few applicants and a verified client"

        proposal = [
            f'I can take on "{job.title}" and keep you updated as it progresses.',
            "I would start by confirming the expected result with you, then build and test it.",
            "[TODO: add a relevant past project and your availability.]",
        ]
        if application_instructions(job.description):
            proposal.append("[TODO: answer the client's questions quoted below.]")

        return Verdict(
            suitable=suitable,
            duration=job.facts.get("duration", "unknown"),
            reason=f"Stub judge (no OPENAI_API_KEY): {reason}.",
            proposal="\n".join(proposal),
        )
