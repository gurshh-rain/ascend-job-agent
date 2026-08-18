"""
Append approved listings to the internships.csv spreadsheet.

Usage:
    from sheet.sheet import append_listing
    append_listing({"id": "...", "company": "...", ...})

Run directly to test:
    python sheet/sheet.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# Allow running this file directly.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import settings


def append_listing(listing: dict) -> None:
    """Append a single approved listing to internships.csv, avoiding duplicates."""
    settings.ensure_data_files_exist()

    if settings.CSV_FILE.exists():
        df = pd.read_csv(settings.CSV_FILE)
    else:
        df = pd.DataFrame(
            columns=["id", "company", "role", "location", "link", "deadline", "date_added", "status"]
        )

    listing_id = listing.get("id", "")
    if listing_id in df["id"].astype(str).values:
        return

    row = {
        "id": listing_id,
        "company": listing.get("company", ""),
        "role": listing.get("role", ""),
        "location": listing.get("location", ""),
        "link": listing.get("link", ""),
        "deadline": listing.get("deadline", ""),
        "date_added": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "approved",
    }

    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df.to_csv(settings.CSV_FILE, index=False)


if __name__ == "__main__":
    sample = {
        "id": "sample-333",
        "company": "Shopify",
        "role": "Software Engineering Intern",
        "location": "Toronto, ON, Canada",
        "link": "https://www.shopify.com/careers",
        "deadline": "",
        "date_posted": "1d",
    }
    append_listing(sample)
    print(f"[sheet] Appended sample listing to {settings.CSV_FILE}")
