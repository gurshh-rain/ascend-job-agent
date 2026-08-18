"""
Daily pipeline: scrape -> LLM filter -> queue/dedup -> send digest.

Run manually:
    python run_daily.py

Dry run (render only, no email):
    python run_daily.py --dry-run

Set up as a scheduled task / cron to run once per day.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from config import settings
from config.setup_wizard import ensure_setup
from mailer.emailer import send_digest
from llm.llm_filter import filter_listings
from scraper.scraper import get_raw_listings
from server.tunnel import ensure_server_and_tunnel
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _age_days(date_str: str) -> int:
    """Convert a date_posted string to an approximate age in days (newest = lowest)."""
    s = (date_str or "").strip().lower()

    m = re.match(r"(\d+)d$", s)
    if m:
        return int(m.group(1))

    m = re.match(r"(\d+)mo$", s)
    if m:
        return int(m.group(1)) * 30

    for fmt in ("%b %d", "%b %d, %y", "%b %d, %Y"):
        try:
            dt = datetime.strptime(s, fmt)
            dt = dt.replace(year=datetime.now().year)
            if dt > datetime.now():
                dt = dt.replace(year=datetime.now().year - 1)
            return (datetime.now() - dt).days
        except ValueError:
            pass

    # ISO dates like 2026-07-21.
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
        return (datetime.now() - dt).days
    except ValueError:
        pass

    # Relative age strings like "0d", "2d", "5 days".
    match = re.match(r"^(\d+)\s*d(?:ays?)?$", s, re.IGNORECASE)
    if match:
        return int(match.group(1))

    return 999


def _is_new(listing: dict[str, Any], sent_log: dict[str, Any]) -> bool:
    """Return False if this id has already been emailed, approved, or rejected.

    'deferred' entries are treated as still pending, so they can be re-queued.
    """
    entry = sent_log.get(listing["id"], {})
    if isinstance(entry, str):
        return entry not in ("emailed", "approved", "rejected")
    return entry.get("status") not in ("emailed", "approved", "rejected")


def _cleanup_stale_deferred(sent_log: dict[str, Any], relevant: list[dict[str, Any]]) -> None:
    """Remove deferred entries that are about to be refreshed by a new scrape."""
    relevant_ids = {l["id"] for l in relevant}
    for sid in list(sent_log.keys()):
        entry = sent_log[sid]
        if (
            isinstance(entry, dict)
            and entry.get("status") == "deferred"
            and sid in relevant_ids
        ):
            del sent_log[sid]


def _load_deferred(sent_log: dict[str, Any]) -> list[dict[str, Any]]:
    """Load queued (deferred) listings from the previous run."""
    deferred: list[dict[str, Any]] = []
    for entry in sent_log.values():
        if isinstance(entry, dict) and entry.get("status") == "deferred" and "listing" in entry:
            deferred.append(entry["listing"])
    return deferred


def _already_ran_today() -> bool:
    """Return True if the bot already successfully ran today."""
    sent_log = _load_json(settings.SENT_LOG_FILE)
    last_run = sent_log.get("_last_run")
    if isinstance(last_run, str):
        return last_run == datetime.now().strftime("%Y-%m-%d")
    return False


def _record_run_today() -> None:
    """Mark today's run as complete in the sent log."""
    sent_log = _load_json(settings.SENT_LOG_FILE)
    sent_log["_last_run"] = datetime.now().strftime("%Y-%m-%d")
    _save_json(settings.SENT_LOG_FILE, sent_log)


def main(dry_run: bool = False, limit: int | None = None) -> None:
    console = Console()
    settings.ensure_data_files_exist()

    console.print("=" * 60)
    console.print("internship-bot daily run started")
    if dry_run:
        console.print("(dry run — no email will be sent)")
    if limit:
        console.print(f"(limiting to first {limit} raw listings for testing)")
    console.print("=" * 60)

    # Skip duplicate runs on the same day unless explicitly doing a dry run.
    if not dry_run and _already_ran_today():
        console.print("\\[run_daily] Already ran today. Skipping.")
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        # 1. Scrape all configured sources.
        scrape_task = progress.add_task(
            "[cyan]Scraping sources...", total=len(settings.SCRAPE_SOURCE_URLS)
        )
        raw_listings = get_raw_listings(progress=progress, task_id=scrape_task)
        if limit:
            raw_listings = raw_listings[:limit]
        console.print(f"\\[run_daily] {len(raw_listings)} raw listings found from all sources")

        # 2. LLM filter: role, season, and target location (GTA / CA / NYC).
        # fast_pre_filter skips obvious non-matches before calling the local model.
        filter_task = progress.add_task(
            "[green]Filtering with LLM...", total=len(raw_listings)
        )
        relevant = filter_listings(
            raw_listings, fast_pre_filter=True, progress=progress, task_id=filter_task
        )
        console.print(f"\\[run_daily] {len(relevant)} listings passed LLM filter")

        # 3. Drop anything already emailed/approved/rejected and merge any
        #    deferred (queued) listings from the previous run.
        dedup_task = progress.add_task("[blue]Deduplicating...", total=None)
        sent_log = _load_json(settings.SENT_LOG_FILE)

        # Remove stale deferred entries that the fresh scrape is about to replace.
        _cleanup_stale_deferred(sent_log, relevant)
        if not dry_run:
            _save_json(settings.SENT_LOG_FILE, sent_log)

        new_relevant = [l for l in relevant if _is_new(l, sent_log)]
        deferred = _load_deferred(sent_log)

        # Combine fresh relevant listings first, then the deferred queue.
        candidates = settings.deduplicate_listings(new_relevant + deferred)
        # Sort by skill match (if configured), then newest first.
        candidates.sort(
            key=lambda l: (
                -int(l.get("matches_skills", True)),
                _age_days(l.get("date_posted", "")),
            )
        )
        progress.update(dedup_task, completed=1, total=1)

        console.print(f"\\[run_daily] {len(new_relevant)} fresh + {len(deferred)} queued = {len(candidates)} candidate listing(s)")

        # 4. Send digest if there are candidates.
        if not candidates:
            console.print("\\[run_daily] Zero listings to send. Skipping email.")
            if not dry_run:
                _record_run_today()
            return

        email_task = progress.add_task("[magenta]Preparing email...", total=None)
        max_listings = settings.MAX_DAILY_LISTINGS
        if max_listings:
            console.print(f"\\[run_daily] Daily cap is {max_listings}; sending up to that many.")
        send_digest(candidates, dry_run=dry_run, max_listings=max_listings or None)
        progress.update(email_task, completed=1, total=1)

    if not dry_run:
        _record_run_today()
    console.print("\\[run_daily] Done.")


def cli() -> None:
    """Parse CLI args and start the daily run."""
    parser = argparse.ArgumentParser(description="internship-bot daily run")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape, filter, and render the digest, but do not send email.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N raw listings (useful for testing).",
    )
    args = parser.parse_args()
    ensure_setup()
    ensure_server_and_tunnel()
    main(dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    cli()
