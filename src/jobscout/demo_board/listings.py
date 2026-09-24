"""Invented listings for the demo board.

Every company, person and place here is made up, and meant to look made up.
The mix is chosen to exercise the pipeline: small scoped jobs for the
quick-win track, larger automation work for the strategic track, and plenty
of unrelated work that the keyword filter should drop before any LLM sees it.
A few posts ask applicants to open with a code word, the way real posts do.

Generation is seeded, so the same board comes up on every machine and tests
can name listings by ID.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace

SEED = 20260924

# (title, description, skills, theme). Theme only decides which feed a listing
# shows up in; the pipeline never sees it.
_TEMPLATES: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    (
        "Fix a broken VLOOKUP in our Google Sheets inventory",
        "Our stock sheet at {company} returns #N/A for about a third of rows since "
        "someone added a column. Need the formula fixed and a short note on what "
        "went wrong. Should take an hour for someone who knows Google Sheets.",
        ("Google Sheets", "Spreadsheet formulas"),
        "quick",
    ),
    (
        "Python script to rename 2,000 scanned invoices by date",
        "{company} has a folder of PDFs named scan0001.pdf and so on. We want a "
        "small Python script that reads the invoice date from each one and renames "
        "the files to YYYY-MM-DD_vendor.pdf. Windows, run by hand once a month.",
        ("Python", "PDF"),
        "quick",
    ),
    (
        "Google Apps Script: email a weekly summary from a sheet",
        "Every Monday someone at {company} copies totals from a sheet into an email. "
        "Replace that with a Google Apps Script trigger. One sheet, one recipient "
        "list, plain-text email is fine.\n\nPlease start your reply with the word "
        '"teapot" so we know you read this far.',
        ("Google Apps Script", "Google Sheets"),
        "quick",
    ),
    (
        "Excel automation: merge 12 monthly workbooks into one",
        "Twelve workbooks, same columns, one per month. {company} needs them merged "
        "into a single table with a month column. A macro or a Python script is "
        "fine; you choose.",
        ("Excel", "VBA", "Python"),
        "quick",
    ),
    (
        "Small Zapier fix: new form entries stopped reaching our CRM",
        "A two-step Zapier zap that sends form entries to our CRM stopped working "
        "last week. {company} just needs it diagnosed and fixed.",
        ("Zapier", "CRM"),
        "quick",
    ),
    (
        "Clean up a CSV export and dedupe customer records",
        "About 4,000 rows exported from an old system at {company}. Names are in "
        "different cases, emails repeat. Need a cleaned CSV and the Python script "
        "that made it.",
        ("Python", "pandas", "Data cleaning"),
        "quick",
    ),
    (
        "Google Sheets dashboard for a bake sale fundraiser",
        "{company} tracks orders in a Google Sheets tab. We want a second tab with "
        "totals per item and per volunteer, plus a simple chart. Volunteer "
        "project, modest budget.",
        ("Google Sheets", "Charts"),
        "quick",
    ),
    (
        "Python script to check 300 links on our site each night",
        "Want a Python script that reads our sitemap, checks every link, and "
        "writes broken ones to a text file. {company} will run it from cron.",
        ("Python", "HTTP"),
        "quick",
    ),
    (
        "Build an AI agent that triages our support inbox",
        "{company} gets around 400 support emails a week. We want an AI agent "
        "that reads each one, tags it, drafts a reply for common cases, and "
        "hands the rest to a person. Must log every decision. Looking for "
        "someone who has shipped LLM features in production, not a demo.",
        ("LLM", "Python", "OpenAI API", "Email"),
        "strategic",
    ),
    (
        "RAG search over 10 years of internal meeting notes",
        "{company} has a decade of meeting notes in a shared drive. We want a RAG "
        "setup where staff can ask questions and get answers with links to the "
        "source notes. Evaluation plan required in your proposal.\n\nIn your "
        "proposal, please answer:\n1. How would you chunk the notes?\n2. How "
        "would you measure answer quality?",
        ("RAG", "Vector database", "Python", "LLM"),
        "strategic",
    ),
    (
        "Workflow automation for order fulfilment across three tools",
        "Orders arrive by email, stock lives in a spreadsheet, and shipping labels "
        "come from a web portal. {company} wants the steps between them automated "
        "end to end, with alerts when something fails. Ongoing work likely.",
        ("Workflow automation", "n8n", "APIs"),
        "strategic",
    ),
    (
        "n8n workflows to replace a pile of manual copy-paste",
        "Our operations team at {company} spends two days a month moving data "
        "between forms, sheets and our invoicing tool. Looking for someone to map "
        "the process and rebuild it as n8n workflows, then hand it over with docs.",
        ("n8n", "Workflow automation", "Documentation"),
        "strategic",
    ),
    (
        "LLM-based classifier for incoming grant applications",
        "{company} receives grant applications as PDFs. We'd like an LLM pipeline "
        "that extracts key fields, flags missing documents and scores fit against "
        "a written rubric. Human review stays in the loop.",
        ("LLM", "OpenAI API", "PDF", "Python"),
        "strategic",
    ),
    (
        "Business process automation audit for a 30-person firm",
        "{company} wants an outside look at which of our recurring tasks are worth "
        "automating first. Deliverable: a ranked list with rough effort, then "
        "build the top two. Business process automation experience essential.",
        ("Business process automation", "Consulting"),
        "strategic",
    ),
    (
        "AI agent to keep product listings in sync across two shops",
        "{company} sells the same 800 products in two online shops. An AI agent "
        "should watch for changes in one and propose matching edits in the other "
        "for a person to approve.",
        ("LLM", "APIs", "Python"),
        "strategic",
    ),
    (
        "Design a logo for a llama-trekking company",
        "{company} needs a friendly logo that works on a hat and a website. Three "
        "concepts, two rounds of changes.",
        ("Logo design", "Illustrator"),
        "other",
    ),
    (
        "Translate a board-game rulebook from English to Klingon",
        "Twenty pages of rules for {company}'s new board game. Must be a fluent "
        "speaker. Yes, really.",
        ("Translation",),
        "other",
    ),
    (
        "Voice-over for a 90-second explainer about moon cheese",
        "Warm, calm voice needed for {company}'s explainer video. Script is final.",
        ("Voice-over",),
        "other",
    ),
    (
        "Bookkeeping catch-up for the last two quarters",
        "{company} is six months behind on bookkeeping. Receipts are scanned and sorted by month.",
        ("Bookkeeping",),
        "other",
    ),
    (
        "Illustrate 12 postcards of imaginary lighthouses",
        "Watercolour style. {company} will print them for a gift shop.",
        ("Illustration", "Watercolour"),
        "other",
    ),
    (
        "Write product descriptions for 40 handmade umbrellas",
        "Short, playful copy. {company} will supply photos and materials for each.",
        ("Copywriting",),
        "other",
    ),
    (
        "Edit a podcast episode about competitive snail racing",
        "One hour of raw audio from {company}. Remove ums, level the audio, add "
        "the intro music we provide.",
        ("Audio editing",),
        "other",
    ),
    (
        "Build a WordPress site for a dragon-sitting service",
        "Five pages, a booking form and a gallery. {company} has the text ready.",
        ("WordPress", "Web design"),
        "other",
    ),
    (
        "Mobile app prototype for a teleporting-pet tracker",
        "Clickable prototype only, no code. {company} wants to test the idea with ten users.",
        ("Figma", "Prototyping"),
        "other",
    ),
    (
        "Social media posts for a haunted bakery, one month",
        "Twelve posts, captions and simple graphics. {company} will approve weekly.",
        ("Social media", "Canva"),
        "other",
    ),
    (
        "Proofread a 200-page novel about sentient teacups",
        "British English. {company} needs it back within three weeks.",
        ("Proofreading",),
        "other",
    ),
    (
        "Data entry: type up 500 handwritten recipe cards",
        "Scans provided. {company} wants a spreadsheet with one row per recipe.",
        ("Data entry",),
        "other",
    ),
    (
        "Research report on the history of left-handed scissors",
        "About 3,000 words with sources. For {company}'s museum newsletter.",
        ("Research", "Writing"),
        "other",
    ),
)

_COMPANIES = (
    "Moonbeam Bakery Collective",
    "Acme Rocket Skates",
    "Gizmo & Sprocket Ltd.",
    "The Very Real Llama Farm",
    "Duckpond Analytics",
    "Pickle Barrel Logistics",
    "Wobbly Table Games",
    "Cloudberry Umbrella Works",
    "Snailspeed Racing Club",
    "Teacup Press",
    "Lighthouse Keepers Guild of Nowhere",
    "Figment Foods",
)

# Fictional places, so no listing reads like it came from a real market.
_LOCATIONS = (
    "Ruritania",
    "Grand Fenwick",
    "Lower Tumbleton",
    "Isle of Pim",
    "Northwold",
    "Vexmoor",
)

_DURATIONS = ("Less than a week", "1 to 4 weeks", "1 to 3 months", "More than 3 months")
_EXPERIENCE = ("Entry", "Intermediate", "Expert")


@dataclass(frozen=True, slots=True)
class Listing:
    listing_id: str
    title: str
    description: str
    skills: tuple[str, ...]
    theme: str
    company: str
    minutes_ago: int
    budget: str
    duration: str
    experience: str
    applicants: int
    client_rating: float | None  # None: nobody has rated this client yet
    client_verified: bool
    client_hires: int
    client_location: str
    # Rank in the "for-you" feed; lower is shown first. None: not in that feed.
    recommended_rank: int | None


def generate(seed: int = SEED) -> list[Listing]:
    """Every listing on the board, newest first."""
    rng = random.Random(seed)
    listings: list[Listing] = []

    # Ages grow geometrically from a few minutes to about two days, so the
    # newest-first track has fresh posts and the other has a backlog.
    ages = [int(2 * 1.3**i) + i + rng.randint(0, 2) for i in range(len(_TEMPLATES))]
    order = list(range(len(_TEMPLATES)))
    rng.shuffle(order)

    for position, (template_index, minutes_ago) in enumerate(zip(order, ages, strict=True)):
        title, body, skills, theme = _TEMPLATES[template_index]
        company = rng.choice(_COMPANIES)
        is_quick = theme == "quick"

        if rng.random() < 0.5:
            low = rng.choice((40, 60, 80, 120, 150)) if is_quick else rng.choice((800, 1500, 3000))
            budget = f"Fixed price: ${low:,}"
        else:
            rate = rng.choice((15, 20, 25)) if is_quick else rng.choice((45, 60, 80))
            budget = f"Hourly: ${rate}-${rate + rng.choice((10, 20, 30))}"

        rated = rng.random() < 0.75
        listings.append(
            Listing(
                listing_id=f"DB-{1001 + position}",
                title=title,
                description=body.format(company=company),
                skills=skills,
                theme=theme,
                company=company,
                minutes_ago=minutes_ago,
                budget=budget,
                duration=rng.choice(_DURATIONS[:2] if is_quick else _DURATIONS),
                experience=rng.choice(_EXPERIENCE),
                applicants=rng.choice((0, 1, 3, 7, 12, 25, 40)),
                client_rating=round(rng.uniform(3.2, 5.0), 1) if rated else None,
                client_verified=rng.random() < 0.7,
                client_hires=rng.choice((0, 0, 1, 4, 11, 37)),
                client_location=rng.choice(_LOCATIONS),
                recommended_rank=None,
            )
        )

    # The recommended feed is ranked, not chronological, which is why the
    # pipeline sorts by posted time itself instead of trusting page order.
    relevant = [item for item in listings if item.theme != "other"]
    rng.shuffle(relevant)
    ranks = {item.listing_id: rank for rank, item in enumerate(relevant)}
    return [replace(item, recommended_rank=ranks.get(item.listing_id)) for item in listings]


FEEDS = ("newest", "for-you")


def feed(listings: list[Listing], name: str) -> list[Listing]:
    """The listings a feed shows, in the order it shows them."""
    if name == "newest":
        return sorted(listings, key=lambda item: item.minutes_ago)
    if name == "for-you":
        ranked = [item for item in listings if item.recommended_rank is not None]
        return sorted(ranked, key=lambda item: item.recommended_rank or 0)
    raise KeyError(name)
