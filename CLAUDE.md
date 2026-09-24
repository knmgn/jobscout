# CLAUDE.md

## What this project is

A scheduled job-listing scout. A real browser (Playwright) reads listings from
a signed-in feed, a keyword pass narrows them, an LLM judges each one against a
per-track rubric, and the survivors are posted to Slack. Reactions left on
those Slack posts are collected and turned into a report that shows where the
rubric and the keyword filter need tuning.

It is a rewrite of a private tool that has run on a 30-minute cron for weeks.
This repository is the **public, site-agnostic version, published as a
portfolio piece.**

## Who reads this repository

Everything here is written for two readers:

1. **A prospective client or employer** who spends about three minutes on the
   README. They should come away knowing what the project does, that it is
   production-minded, and which engineering decisions stand out. A screenshot
   and a diagram do more for them than prose.
2. **An engineer who clones it.** `make demo` has to work on a fresh machine
   with no accounts and no API keys. After that, the code should be readable
   module by module.

When a choice trades one against the other, favour what a reviewer can verify
by running the project over claims made in the README.

## Non-negotiables

- **Source-agnostic.** Everything a real site would need (URLs, selectors,
  login handling, ID formats, UI wording) sits behind the `Source` interface.
  This repository ships exactly one source: the local demo board. No real job
  site may be identifiable anywhere: code, tests, fixtures, docs, comments,
  commit messages, branch names or screenshots. The concrete denylist lives in
  `CLAUDE.local.md`, which is gitignored and never quoted here.
- **No bot-detection evasion.** No stealth flags, fingerprint tuning,
  challenge solving, cookie import or other session tricks. The browser
  detects that a session has expired, saves a snapshot and raises an alert.
  It does nothing more.
- **No real data.** No real postings, job titles, client details, verdicts,
  logs or captured HTML. Every fixture is invented, and it should look
  invented. Prefer an obviously fictional company over a plausible real one.
- **No secrets.** `.env.example` holds names only. Keep `.env`, `*.db`, logs,
  snapshots and the browser profile out of git.
- **Run `scripts/check_sanitized.sh` before every commit.** It greps the
  working tree and the full history against the local denylist. A pre-commit
  hook calls it. Never bypass the hook.

## Quality bar

- **One-command demo.** `make demo` starts the demo board, a local HTTP server
  serving generated listing pages with a "load more" button and a
  session-expiry page. It then runs the full pipeline against the board.
  - Without `OPENAI_API_KEY`, a deterministic stub judge is used.
  - Without Slack credentials, the Block Kit payload is printed or written to
    `out/`.
  - Any real credentials that are set get used.
- **Tests.** pytest suites that run offline and fast. The unit tests need no
  browser. One integration test drives Playwright against the demo board.
- **CI.** A GitHub Actions workflow runs ruff, the sanitization check, the
  unit tests and the Playwright integration test. Put its badge in the README.
- **Code.** Python 3.12, type hints throughout, small modules, and a
  `pyproject.toml`. Docstrings say *why*, not *what*. Match the comment
  density of the code you write around.
- **Config.** Tracks, feeds, keyword queries and rubric text live in
  `tracks.toml`, not in code.
- **README structure.**
  1. A one-line pitch.
  2. A screenshot of a Slack card rendered from the demo, not a real run.
  3. A mermaid architecture diagram.
  4. A quickstart.
  5. "Design decisions", short, each linking to `docs/design.md`.
  6. A configuration reference.
  7. "Writing a source for a real site", which covers the interface only and
     names no site.
  8. Limitations.
- **Commits.** A short imperative subject that says why. Each commit is one
  logical step, so the history reads well too.

## Design decisions worth telling

These came from running the original for real. Carry them over in generic
form and explain them in `docs/design.md`.

- **Two tracks, one pass.** One track looks for quick wins: small, clearly
  scoped, few competitors, newest first. The other looks for strategic fit:
  larger work that matches the genre, where recency matters less. They differ
  in feed, query and rubric, not in code; the pipeline does not know which
  track it is running.
- **Keyed by (job_id, track).** The same listing can be judged by each track
  independently.
- **Mark a job processed only after success.** A job counts as processed only
  once it has a verdict and, if it was suitable, has been delivered to Slack.
  A failed LLM call or a failed Slack post is retried on the next run instead
  of being lost.
- **Throttled failure alerts.** A persistent failure, such as an expired
  session, alerts at most once every 6 hours per track instead of on every
  cron tick. This came from a real outage that went unnoticed for three days.
- **Snapshot on failure.** When a page is not what was expected, its HTML and
  a PNG are saved, so that the parser can be retuned offline
  (`--parse-file`).
- **Filter keywords on our side.** The feed has no search, so the keyword
  query (quoted phrases joined with OR) is applied locally. It is a cheap
  filter before the LLM, and `limit` per track caps the LLM cost of each run.
- **Rubric text is config.** It is the part tuned most often, so it lives in
  `tracks.toml` as `guidance`, and the system prompt stays stable.
- **Structured LLM output.** Each verdict carries the fields suitable,
  duration, reason and a three-line proposal draft. Output that does not match
  is treated as "no verdict, retry", not as `false`.
- **Feedback loop.** Posts go out through `chat.postMessage`, so each message
  timestamp maps back to a job. A later run reads the reactions (👍 / 👎) and
  any note in the thread, stores them, and `--report` groups the ratings by
  the card facts a pre-filter could be written against.
- **One-off modes.** `--dry-run`, `--dump`, `--seed` (so the first run does
  not flood the channel), `--snapshot`, `--parse-file` and `--list-tracks`.

## Out of scope

Anything that exists only because a particular real site is hostile to
automation. When in doubt, leave it out and say so in "Limitations".
