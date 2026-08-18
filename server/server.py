"""
FastAPI approve/reject webhook server.

Run locally:
    uvicorn server.server:app --port 8000 --reload

Expose to the internet with Cloudflare Tunnel:
    cloudflared tunnel --url http://localhost:8000

Then set the public URL as TUNNEL_BASE_URL in your .env.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

# Allow running from the repo root.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import settings
from sheet import sheet

app = FastAPI(title="internship-bot approve/reject server")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _confirmation_page(title: str, message: str, listing: dict[str, Any] | None = None) -> str:
    listing_html = ""
    if listing:
        listing_html = (
            f"<p><strong>Company:</strong> {listing.get('company', 'N/A')}<br>"
            f"<strong>Role:</strong> {listing.get('role', 'N/A')}<br>"
            f"<strong>Location:</strong> {listing.get('location', 'N/A')}</p>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{ font-family: Arial, sans-serif; background: #f4f4f7; margin: 0; padding: 0; display: flex; justify-content: center; align-items: center; min-height: 100vh; }}
        .box {{ background: #ffffff; padding: 36px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); text-align: center; max-width: 520px; }}
        h1 {{ margin: 0 0 12px; color: #111827; }}
        p {{ color: #4b5563; font-size: 15px; line-height: 1.5; }}
        a {{ color: #2563eb; text-decoration: none; }}
    </style>
</head>
<body>
    <div class="box">
        <h1>{message}</h1>
        {listing_html}
        <p><a href="/">Back to server home</a></p>
    </div>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return "<h1>internship-bot is running</h1><p>Use /approve?id=... or /reject?id=...</p>"


@app.get("/approve", response_class=HTMLResponse)
def approve(id: str) -> str:  # noqa: A002 - `id` matches the query param name
    settings.ensure_data_files_exist()
    pending = _load_json(settings.PENDING_FILE)
    sent_log = _load_json(settings.SENT_LOG_FILE)

    listing = pending.get(id)
    if not listing:
        return _confirmation_page(
            "Not found",
            "This listing was already approved or rejected, or the link is invalid.",
        )

    sheet.append_listing(listing)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sent_log[id] = {
        "status": "approved",
        "approved_at": now,
        "company": listing.get("company", ""),
        "role": listing.get("role", ""),
        "location": listing.get("location", ""),
    }

    del pending[id]
    _save_json(settings.PENDING_FILE, pending)
    _save_json(settings.SENT_LOG_FILE, sent_log)

    return _confirmation_page(
        "Approved",
        f"Added ✅",
        listing,
    )


@app.get("/reject", response_class=HTMLResponse)
def reject(id: str) -> str:  # noqa: A002
    settings.ensure_data_files_exist()
    pending = _load_json(settings.PENDING_FILE)
    sent_log = _load_json(settings.SENT_LOG_FILE)

    listing = pending.get(id)
    if not listing:
        return _confirmation_page(
            "Not found",
            "This listing was already approved or rejected, or the link is invalid.",
        )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sent_log[id] = {
        "status": "rejected",
        "rejected_at": now,
        "company": listing.get("company", ""),
        "role": listing.get("role", ""),
        "location": listing.get("location", ""),
    }

    del pending[id]
    _save_json(settings.PENDING_FILE, pending)
    _save_json(settings.SENT_LOG_FILE, sent_log)

    return _confirmation_page(
        "Rejected",
        f"Rejected ❌",
        listing,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server.server:app",
        host="0.0.0.0",
        port=settings.SERVER_PORT,
        reload=True,
    )
