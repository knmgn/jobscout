"""Command line entry point: `jobscout` (or `python -m jobscout`)."""

from __future__ import annotations

import argparse
import contextlib
import importlib
import logging
import os
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from jobscout import report
from jobscout.judge import make_judge
from jobscout.pipeline import Options, Pipeline, sort_oldest_first, summarise
from jobscout.slack import OutboxNotifier, make_notifier
from jobscout.sources.base import Source
from jobscout.sources.browser import BrowserSession
from jobscout.store import Store
from jobscout.tracks import Config, ConfigError, Track, load

logger = logging.getLogger("jobscout")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="jobscout",
        description="Read listing feeds with a real browser, keep what matches each "
        "track, have an LLM judge it, and post the survivors to Slack.",
    )
    parser.add_argument(
        "--config", type=Path, default=Path(os.getenv("JOBSCOUT_CONFIG", "tracks.toml"))
    )
    parser.add_argument("--db", type=Path, help="SQLite state file (default: jobscout.db)")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("out"),
        help="where snapshots and unsent Slack payloads go (default: out/)",
    )
    parser.add_argument(
        "--track",
        action="append",
        metavar="NAME",
        help="run only this track (repeatable); naming a disabled track runs it",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="judge at most N listings per track this run (overrides tracks.toml)",
    )
    parser.add_argument(
        "--demo",
        nargs="?",
        const="ok",
        choices=("ok", "expired"),
        help="start the local demo board and read from it (state in "
        "out/demo.db); `--demo expired` serves an expired session instead",
    )
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument("--verbose", "-v", action="store_true")

    run = parser.add_argument_group("run variants")
    run.add_argument(
        "--dry-run", action="store_true", help="judge and log, but send nothing and write nothing"
    )
    run.add_argument(
        "--dump", action="store_true", help="print each track's candidates; no judging, no Slack"
    )
    run.add_argument(
        "--seed",
        action="store_true",
        help="mark everything on the feeds as processed, so a first real run "
        "does not flood the channel with the backlog",
    )

    modes = parser.add_argument_group("one-off modes")
    modes.add_argument("--list-tracks", action="store_true", help="show the configured tracks")
    modes.add_argument(
        "--report", action="store_true", help="show what Slack reactions say, grouped by card facts"
    )
    modes.add_argument(
        "--snapshot",
        action="store_true",
        help="save each selected track's feed pages as HTML and PNG",
    )
    modes.add_argument(
        "--parse-file",
        type=Path,
        metavar="HTML",
        help="parse a saved snapshot offline (with --track: also apply its query)",
    )
    return parser.parse_args(argv)


def load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env support: KEY=VALUE lines; the real environment wins."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def make_source(config: Config, overrides: dict[str, str]) -> Source:
    module_name, _, class_name = config.source_class.partition(":")
    try:
        cls = getattr(importlib.import_module(module_name), class_name)
    except (ImportError, AttributeError) as exc:
        raise ConfigError(f"Cannot load source class {config.source_class!r}: {exc}") from exc
    return cls(**{**config.source_options, **overrides})


def check_feeds(source: Source, tracks: list[Track]) -> None:
    for track in tracks:
        unknown = [feed for feed in track.feeds if feed not in source.feeds]
        if unknown:
            raise ConfigError(
                f"Track {track.name!r} asks for feed(s) {', '.join(unknown)}; "
                f"source {source.name!r} has {', '.join(source.feeds)}."
            )


@contextlib.contextmanager
def demo_board(mode: str | None) -> Iterator[str | None]:
    if mode is None:
        yield None
        return
    from jobscout.demo_board import BoardServer, DemoBoard

    with BoardServer(DemoBoard(expired=mode == "expired")) as server:
        logger.info("Demo board running at %s", server.url)
        yield server.url


def list_tracks(config: Config, store: Store) -> str:
    lines = []
    for track in config.tracks:
        state = "enabled" if track.enabled else "disabled"
        lines += [
            f"{track.heading}  [{track.name}, {state}]",
            f"    feeds={', '.join(track.feeds)}  max_jobs={track.max_jobs}  limit={track.limit}",
            f"    keywords={', '.join(track.keywords) or '(none: takes the whole feed)'}",
            f"    judged so far={store.count_processed(track.name)}",
        ]
        if track.extras:
            lines.append(f"    unknown keys (typo?): {', '.join(track.extras)}")
    return "\n".join(lines)


def parse_file(source: Source, path: Path, tracks: list[Track] | None) -> str:
    html = path.read_text(encoding="utf-8", errors="replace")
    # Relative dates in a snapshot are relative to when it was saved.
    saved_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    jobs = source.parse(html, now=saved_at)
    lines = [f"{len(jobs)} listing(s) parsed from {path}"]
    for track in tracks or []:
        jobs = [job for job in jobs if track.matches(job)]
        lines.append(f"{len(jobs)} match track {track.name!r}")
    return "\n".join([*lines, "", summarise(sort_oldest_first(jobs))])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    load_dotenv()

    try:
        config = load(args.config)
        tracks = config.select(args.track)
        db_path = args.db or (args.out / "demo.db" if args.demo else Path("jobscout.db"))
        store = Store(db_path)

        if args.list_tracks:
            print(list_tracks(config, store))
            return 0
        if args.report:
            notifier = make_notifier(dict(os.environ), args.out / "slack")
            Pipeline(store, make_judge(), notifier).harvest_feedback()
            only = args.track[0] if args.track else None
            print(report.render(store.rated(only), store.count_notified(only)))
            return 0

        with demo_board(args.demo) as board_url:
            overrides = {"base_url": board_url} if board_url else {}
            source = make_source(config, overrides)
            check_feeds(source, tracks)

            if args.parse_file:
                print(parse_file(source, args.parse_file, tracks if args.track else None))
                return 0

            session = BrowserSession(
                source,
                snapshot_dir=args.out / "snapshots",
                # The demo board has no sign-in, so it needs no profile.
                profile_dir=None
                if args.demo or not config.profile_dir
                else Path(config.profile_dir),
                headless=not args.headed,
            )
            with session:
                if args.snapshot:
                    for track in tracks:
                        for feed in track.feeds:
                            print(f"[{track.name}/{feed}] {session.snapshot(feed, track.max_jobs)}")
                    return 0

                notifier = make_notifier(dict(os.environ), args.out / "slack")
                options = Options(
                    dry_run=args.dry_run, seed=args.seed, dump=args.dump, limit=args.limit
                )
                pipeline = Pipeline(store, make_judge(), notifier, options)
                results = pipeline.run(session, tracks)
    except ConfigError as exc:
        logger.error("%s", exc)
        return 1

    for name, result in results.items():
        status = "FAILED" if result.failed else "ok"
        print(
            f"[{name}] {status}: {result.found} candidate(s), judged {result.judged}, "
            f"sent {result.notified}, to retry {result.retry}"
        )
    if isinstance(notifier, OutboxNotifier) and not (args.dump or args.dry_run):
        print(f"No Slack credentials set: payloads are in {notifier.directory}/")
    return 1 if any(result.failed for result in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
