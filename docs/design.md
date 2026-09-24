# Design notes

jobscout is a rewrite of a private tool that ran on a 30-minute cron for
weeks. Most of the decisions below were not made up front: each one exists
because the original broke, cost money, or went quiet in a way that nobody
noticed. This page explains them in generic form.

- [Two tracks, one pass](#two-tracks-one-pass)
- [Keyed by (job_id, track)](#keyed-by-job_id-track)
- [Mark processed only after success](#mark-processed-only-after-success)
- [Throttled failure alerts](#throttled-failure-alerts)
- [Snapshot on failure](#snapshot-on-failure)
- [Keywords are filtered on our side](#keywords-are-filtered-on-our-side)
- [The rubric is config](#the-rubric-is-config)
- [Structured LLM output](#structured-llm-output)
- [The feedback loop](#the-feedback-loop)
- [One-off modes](#one-off-modes)
- [The Source boundary](#the-source-boundary)
- [What the browser does not do](#what-the-browser-does-not-do)

## Two tracks, one pass

Someone building a freelance practice is playing two games at once. One
looks for **quick wins**: small, clearly scoped jobs with few competitors,
where being early matters. The other looks for **strategic fit**: larger
work in the genre worth building a reputation on, where recency matters less.

These want opposite things from the same feed. A two-hour spreadsheet fix is
a yes for the first and a no for the second; a three-month automation build
is the reverse. So they are separate tracks, each with its own feeds, query,
rubric and cost limit, all in [`tracks.toml`](../tracks.toml).

They differ in configuration only. `Pipeline` never branches on which track it
is running, so adding a third track is a config change, not a code change.
Both tracks run in one browser session, because starting Chromium is the
most expensive part of a run.

## Keyed by (job_id, track)

Every table is keyed by `(job_id, track)`, never by `job_id` alone. A listing
one track rejected must still reach the other: the extra LLM call costs a
fraction of a cent, while losing the strategic track's best candidate
because the quick-win track saw it first costs the job.

Within one run, a listing that appears in two of a track's feeds is merged
before judging (`pipeline.collect`), so it is not paid for twice.

## Mark processed only after success

A listing is recorded as processed only when it has a verdict and, if that
verdict was "suitable", the Slack post has succeeded. Everything else is
left for the next run:

| What happened                   | Recorded? | Next run         |
| ------------------------------- | --------- | ---------------- |
| Judged unsuitable               | yes       | skipped          |
| Judged suitable, posted         | yes       | skipped          |
| LLM call failed / off-schema    | no        | judged again     |
| Judged suitable, post failed    | no        | judged and sent  |

The alternative, marking a listing as seen before doing the work, is simpler
and loses listings every time an API hiccups. This design was tested for
real on the first run against OpenAI: a configuration mistake made every
call fail, all 14 listings stayed unprocessed, and the next run judged them
all once the mistake was fixed.

The per-track `limit` counts judging attempts, failed ones included, so a
run that keeps failing cannot run up an unbounded bill either.

## Throttled failure alerts

Silence is ambiguous. A channel with no new cards might mean there were no
good listings, or that the scout has been broken for days. The original ran
into exactly this: a login expired and nothing was reported for three days.

So a track that cannot read its feeds posts an alert. But a cron job that
alerts on every tick gets muted within a day, so alerts are throttled to
once per six hours **per track** (`pipeline.ALERT_INTERVAL`): one broken
track does not silence news about the other. The throttle resets as soon as
the track reads successfully, so a new outage alerts straight away.

A failing track never stops the others, and a run with any failed track exits
with status 1, so cron wrappers and monitoring can see it too.

## Snapshot on failure

When a page is not what the source expected (a sign-in redirect, an error
page, or a feed that loads but parses to nothing), `BrowserSession` saves the
page's HTML and a PNG under `out/snapshots/` before raising.

The HTML is the useful half. Sites change their markup without warning, and
the fix is always "update the selectors". `--parse-file` runs a source's
parser against a saved snapshot without starting a browser, so selectors can
be retuned in a loop that takes a fraction of a second and touches nothing
live. Relative dates in a snapshot are resolved against the file's mtime, so
a week-old snapshot still parses "3 hours ago" correctly.

A feed that parses to zero listings is treated as an error (`NothingParsed`),
not an empty result. From the outside, a broken parser looks exactly like a
quiet day, and that is the case where an alert matters most.

## Keywords are filtered on our side

The feeds this is built for have no search, and the ranked ones show
everything the site thinks you might like. Each track's `query` (quoted
phrases and bare words joined by `OR`, the syntax most job-search boxes take)
is matched locally against the title, description and skills.

Terms match as whole words, case-insensitively. Without word boundaries,
`RAG` matches "storage" and "average", and each false match is an LLM call
spent saying no. Boundaries are only applied at word-character ends, so
`n8n` and `C++` still work.

The filter is deliberately cheap and crude. It runs before the LLM so the
LLM only sees plausible candidates, and `limit` then caps how many of those
are judged per run.

## The rubric is config

The system prompt is fixed and only says *how* to read a listing: judge the
listing, not its category; the posted budget is not evidence of size; card
facts are facts, not claims; when unsure, say yes; never invent the
applicant's experience. *What a track wants* lives in `tracks.toml` as
`guidance`, and is appended last, where the model weighs it most.

The rubric is the part that gets tuned most often, so it should be editable
without touching code. And because the system prompt never changes, a change
in behaviour can always be traced to a change in `tracks.toml`.

Two further details:

- When a listing tells applicants what their reply must contain (an opening
  word, numbered questions), those instructions are extracted and repeated
  at the very end of the prompt, where truncation cannot reach them. The
  formatting rule is then *swapped*, not overridden: given both "write three
  lines" and "answer all ten questions", a model answers three of them.
- The draft reply is shown in Slack under a label that says to check every
  claim. The prompt tells the model it knows nothing about the applicant,
  but a draft sent unread is how an invented claim reaches a client.

## Structured LLM output

Every verdict has four fields: `suitable`, `duration`, `reason` and
`proposal`. The OpenAI judge asks for a strict JSON schema, so the API itself
refuses off-schema output.

Anything that still does not validate is **no verdict**, not `false`.
`suitable: "false"` as a string is rejected rather than coerced, because
`bool("false")` is `True`. No verdict means the listing is retried next run.
A malformed reply therefore costs a retry, and never a silently lost listing.

Without `OPENAI_API_KEY`, a deterministic `StubJudge` applies fixed rules to
the card facts and says so in every reason. It is what `make demo` and the
tests use, so both run offline and give the same result every time.

## The feedback loop

Cards are posted with `chat.postMessage` rather than a webhook, for one
reason: it returns the message timestamp, which is Slack's ID for the
message. Each timestamp is stored next to the listing and a frozen copy of
its card facts.

At the start of every run, the scout reads recent channel history. A 👍 or 👎
on a card is stored as a rating, and any reply in its thread is stored as the
reason. Polling was chosen over the Events API or Socket Mode, because the
scout already wakes up on a schedule and one history call is cheap. Nothing
has to be listening between runs.

`--report` groups the ratings by card facts only (applicant count, client
verification, rating, history, level, duration, budget type). That is on
purpose: a rule written against a card fact can run *before* the LLM, which
is where it saves money. The report is a table for a person to read, not a
rule the pipeline applies to itself. Whether to stop sending listings with
20+ applicants is a decision to make once, looking at the numbers.

The card facts are frozen at send time because they keep changing afterwards
(applicant counts rise), and a cut-off has to be argued from what was known
when the card went out.

## One-off modes

| Flag            | What it does                                                       |
| --------------- | ------------------------------------------------------------------ |
| `--dry-run`     | Judge and log verdicts; send nothing, write nothing.                |
| `--dump`        | Print each track's candidates after filtering; no LLM, no Slack.    |
| `--seed`        | Mark everything currently on the feeds as processed.                |
| `--snapshot`    | Save each selected feed as HTML and PNG.                            |
| `--parse-file`  | Parse a saved snapshot offline (with `--track`, apply its query).   |
| `--list-tracks` | Show tracks, feeds, keywords, limits, and unknown keys (typos).     |
| `--report`      | Read new reactions, then show the ratings grouped by card facts.    |

`--seed` exists because the first real run would otherwise judge and post
the whole backlog on the feed at once. Seeding once per new track means the
channel only ever sees listings that appeared after the scout started.

## The Source boundary

Everything that would identify or depend on a particular site sits behind
[`Source`](../src/jobscout/sources/base.py): URLs, selectors, how an expired
session shows itself, how the feed grows, the ID format and the wording of
dates and facts. The rest of the code only ever sees a
[`Job`](../src/jobscout/models.py).

A source is split into the part that needs a live page (`page_state`,
`expand`) and the part that does not (`parse`, which takes an HTML string).
That split is what makes `--parse-file` and fast, browser-free parser tests
possible. The generic browser work (navigation, the "load more" loop, the
snapshots, turning browser errors into per-track failures) is in
`BrowserSession` and shared by every source.

Card facts use a fixed set of keys (`models.FACT_LABELS`) instead of whatever
a site calls them. `--report` groups by those keys, and two sources that
spelled "budget" differently would split one pre-filter candidate in two.

This repository ships one source, for the local demo board. The README
explains how to write another.

## What the browser does not do

The browser is launched with Playwright's defaults. There are no stealth
flags, no fingerprint tuning, no challenge solving and no cookie import.
When a site stops serving the feed (a sign-in page, a verification
challenge, a block), the scout saves what it saw, alerts, and stops reading
that track until a person has looked. A persistent browser profile
(`[source] profile_dir`) keeps an ordinary signed-in session between runs,
the same way a person's own browser does. That is all.

This is a design decision, not a gap. A scout that fights the site it reads
is fragile and breaks the terms most sites set. One that stops and asks for a
person is predictable, and a scout running unattended has to be predictable.
