"""The one shape every source hands to the rest of the pipeline.

Filtering, judging, storage and Slack only ever see a `Job`, so none of them
learns which site a listing came from. Anything a site phrases its own way
(ID format, "posted 3 hours ago", how it labels a budget) is turned into this
shape inside the source, not downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# The card facts a source may fill in, with the label used when they are shown
# to a person or to the judge. Fixed keys rather than free-form ones, because
# `--report` groups feedback by them: two sources that spelled "budget"
# differently would split one pre-filter candidate into two.
FACT_LABELS: dict[str, str] = {
    "budget": "Budget",
    "duration": "Duration",
    "experience": "Experience level",
    "applicants": "Applicants so far",
    "skills": "Skills requested",
    "client_rating": "Client rating",
    "client_verified": "Client verified",
    "client_history": "Client history",
    "client_location": "Client location",
}


@dataclass(frozen=True, slots=True)
class Job:
    """One listing as read off a feed.

    `job_id` only has to be stable for the same listing on the same source;
    the pipeline keys on (job_id, track), never on the link, because links
    tend to carry tracking parameters that change between page loads.
    """

    job_id: str
    title: str
    link: str
    description: str
    # None when the page does not say. Sources convert relative wording into
    # an absolute time at read time, so sorting never has to know the phrasing.
    posted_at: datetime | None = None
    facts: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        unknown = set(self.facts) - set(FACT_LABELS)
        if unknown:
            raise ValueError(f"Unknown fact keys {sorted(unknown)}; see FACT_LABELS.")
