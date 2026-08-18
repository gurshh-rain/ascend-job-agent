"""
Build and send the daily digest email.

send_digest(listings):
    - Renders mailer/templates/digest_email.html with Jinja2
    - Sends the email via SMTP
    - Writes listings into data/pending.json
    - Marks each listing as "emailed" in data/sent_log.json

Run directly to test rendering (dry-run, no email sent):
    python mailer/emailer.py
"""

from __future__ import annotations

import json
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

# Allow running this file directly.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import settings


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def render_digest(listings: list[dict[str, Any]]) -> str:
    """Render the HTML email template for the given listings."""
    env = Environment(
        loader=FileSystemLoader(settings.EMAIL_TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template(settings.DIGEST_TEMPLATE_FILE.name)
    return template.render(
        listings=listings,
        tunnel_base_url=settings.TUNNEL_BASE_URL.rstrip("/"),
    )


def send_digest(
    listings: list[dict[str, Any]],
    dry_run: bool = False,
    max_listings: int | None = None,
) -> None:
    """
    Render the digest, persist pending/sent state, and send the email.

    Args:
        listings: List of filtered listings from llm_filter.
        dry_run: If True, render and log but do not send or update files.
        max_listings: Optional cap on how many listings to send. Extra are
            queued as "deferred" in sent_log.json for the next day.
    """
    listings = settings.deduplicate_listings(listings)
    if not listings:
        print("[emailer] No listings to send.")
        return

    if max_listings and len(listings) > max_listings:
        to_send = listings[:max_listings]
        deferred = listings[max_listings:]
    else:
        to_send = listings
        deferred = []

    html = render_digest(to_send)

    if not dry_run:
        settings.ensure_data_files_exist()

        pending = _load_json(settings.PENDING_FILE)
        sent_log = _load_json(settings.SENT_LOG_FILE)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Queue deferred listings so they are re-sent on the next run.
        for listing in deferred:
            listing_id = listing["id"]
            sent_log[listing_id] = {
                "status": "deferred",
                "deferred_at": now,
                "listing": listing,
            }

        for listing in to_send:
            listing_id = listing["id"]
            pending[listing_id] = listing
            sent_log[listing_id] = {
                "status": "emailed",
                "emailed_at": now,
                "company": listing.get("company", ""),
                "role": listing.get("role", ""),
                "location": listing.get("location", ""),
                "listing": listing,
            }

        _save_json(settings.PENDING_FILE, pending)
        _save_json(settings.SENT_LOG_FILE, sent_log)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Internship digest: {len(to_send)} new listing(s)"
        msg["From"] = settings.EMAIL_FROM
        msg["To"] = settings.EMAIL_TO
        msg.attach(MIMEText(html, "html"))

        password = (settings.SMTP_PASSWORD or "").replace(" ", "")

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USER, password)
            server.sendmail(settings.EMAIL_FROM, settings.EMAIL_TO, msg.as_string())

        print(f"[emailer] Sent digest with {len(to_send)} listing(s) to {settings.EMAIL_TO}")
        if deferred:
            print(f"[emailer] Queued {len(deferred)} listing(s) for the next run.")
    else:
        print(f"[emailer] DRY RUN: would send {len(to_send)} listing(s), queue {len(deferred)}.")
        print("=" * 60)
        print(html[:1000])
        print("=" * 60)


if __name__ == "__main__":
    samples = [
        {
            "id": "sample-111",
            "company": "Notion",
            "role": "Software Engineer Intern",
            "location": "San Francisco, CA",
            "link": "https://jobs.ashbyhq.com/notion/3fba1c39-c5cb-47d7-9ad2-1cec4d7e9d0c",
            "deadline": "",
            "date_posted": "1d",
            "source": "test",
            "matched_region": "California",
        },
        {
            "id": "sample-222",
            "company": "KPMG",
            "role": "Software Developer Intern Co-op",
            "location": "Toronto, ON, Canada",
            "link": "https://careers.kpmg.ca/jobs/33306",
            "deadline": "",
            "date_posted": "1d",
            "source": "test",
            "matched_region": "Greater Toronto Area",
        },
    ]
    send_digest(samples, dry_run=True)
