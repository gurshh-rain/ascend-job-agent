"""
Multi-source internship scraper.

Fetches raw listings from a list of URLs in config/settings.py and normalizes them
into a single list of dicts with these keys:
    company, role, location, link, date_posted, source, raw_text

Supported source formats:
    - JSON job feeds (zshah101, ApplyGuy, YC API, and similar)
    - HTML tables embedded in markdown (SimplifyJobs)
    - Markdown pipe tables (vanshb03, internatlas, zapplyjobs, and similar)
    - Markdown tables with image-in-link apply buttons (negarprh, zapplyjobs)
    - Hacker News "Who is hiring?" thread via Algolia

Run directly to test:
    python -m scraper.scraper
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests
import warnings
from bs4 import BeautifulSoup
from bs4 import MarkupResemblesLocatorWarning

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

# Allow running this file directly from the repo root or from its own folder.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import settings


TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref", "source", "src"}

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
}


def _log(msg: str) -> None:
    print(f"[scraper] {msg}")


def normalize_url(url: str | None) -> str | None:
    """Remove common tracking query params so the same job isn't duplicated across sources."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return url
        query = parse_qs(parsed.query, keep_blank_values=True)
        filtered = {k: v for k, v in query.items() if k.lower() not in TRACKING_PARAMS}
        new_query = urlencode(filtered, doseq=True)
        return urlunparse(parsed._replace(query=new_query))
    except Exception:
        return url


def listing_id(company: str, role: str, link: str | None) -> str:
    """Stable short id for a listing."""
    safe_link = normalize_url(link) or link or ""
    payload = f"{company.strip().lower()}|{role.strip().lower()}|{safe_link.lower()}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def fetch(url: str, timeout: int = 30, headers: dict[str, str] | None = None) -> str | None:
    """Fetch a URL, logging any failures but not raising."""
    try:
        _log(f"Fetching {url}")
        response = requests.get(url, timeout=timeout, headers=headers)
        response.raise_for_status()
        return response.text
    except Exception as exc:
        _log(f"Failed to fetch {url}: {exc}")
        return None


def guess_source_type(text: str, url: str) -> str:
    """Guess whether the content is a JSON feed, RSS/Atom, HTML, or markdown table."""
    stripped = text.strip().lower()
    if url.endswith(".json") or stripped.startswith(("{", "[")):
        return "json"
    if stripped.startswith("<?xml") or stripped.startswith(("<rss", "<feed")):
        return "rss"
    # Markdown tables have pipe-separated rows and a separator like |---|---|.
    if re.search(r"^\s*\|[-:\s|]+\|\s*$", text, re.MULTILINE):
        return "markdown"
    if stripped.startswith(("<html", "<!doctype html", "<table")) or "<table" in text.lower():
        return "html"
    return "markdown"


def _cell_text(cell: Any, separator: str = ", ") -> str:
    """Extract clean text from a table cell, expanding <details> and <br>."""
    if isinstance(cell, str):
        # If the string contains HTML-ish tags, parse it so we get real text.
        if "<" in cell and ">" in cell:
            # Normalize common br variants so get_text separates them cleanly.
            cell = re.sub(r"<\s*/?\s*br\s*/?\s*>", "<br/>", cell, flags=re.IGNORECASE)
            cell = BeautifulSoup(cell, "html.parser")
            text = cell.get_text(separator=separator, strip=True)
        else:
            text = cell
    else:
        # get_text with separator flattens <br>, <details>, etc. into readable text.
        text = cell.get_text(separator=separator, strip=True)

    # Collapse multiple spaces/newlines.
    text = re.sub(r"\s+", " ", text)
    # Strip markdown link syntax and keep just the display text: [text](url) -> text.
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Strip bold/italic markdown markers.
    text = re.sub(r"\*\*", "", text)
    return text.strip()


def _extract_links(cell_text: str) -> list[str]:
    """Return all real http(s) links found in a cell (HTML or markdown)."""
    links: list[str] = []
    if not cell_text:
        return links

    # Try parsing as HTML first.
    soup = BeautifulSoup(cell_text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("http"):
            links.append(href)

    # Fallback for plain markdown links: [text](url)
    if not links:
        # Find every `](url)` so image-in-link buttons like
        # [![Apply](shield)](real-url) or [<img ...>](real-url) resolve to real-url.
        for match in re.finditer(r"\]\((https?://[^\s)]+)\)", cell_text):
            links.append(match.group(1))

    return links


def _best_link(cell_text: str) -> str | None:
    """Pick the most useful link from a cell, ignoring tracker/ image links."""
    links = _extract_links(cell_text)
    if not links:
        return None

    def _is_bad(href: str) -> bool:
        h = href.lower()
        return (
            "imgur.com" in h
            or "img.shields.io" in h
            or "applyguy.ai/jobs" in h
            or h.startswith("https://simplify.jobs/p/")
            or h.startswith("https://simplify.jobs/c/")
            or h.endswith((".svg", ".png", ".jpg", ".jpeg", ".gif"))
        )

    for href in links:
        if not _is_bad(href):
            return href
    return links[0]


def _clean_company(name: str) -> str:
    """Strip leading emoji/symbols and normalize whitespace in company names."""
    name = re.sub(r"[\ue000-\uf8ff]|[🔥🛂🇺🇸🔒🎓↳]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _header_map(headers: list[str]) -> dict[str, int]:
    """Map common table header names to canonical keys and their column index."""
    canonical = {}
    for idx, h in enumerate(headers):
        text = re.sub(r"[^a-z0-9]", "", h.lower()).strip()
        if text in ("company", "companies", "org", "organization", "employer"):
            canonical.setdefault("company", idx)
        elif text in ("role", "roles", "title", "position", "jobtitle", "job", "jobtitleposition"):
            canonical.setdefault("role", idx)
        elif text in ("location", "locations", "place", "city"):
            canonical.setdefault("location", idx)
        elif text in ("application", "applicationlink", "link", "url", "apply", "actions", "app", "posting", "postings"):
            canonical.setdefault("link", idx)
        elif text in ("age", "posted", "dateposted", "date", "datepostedage", "season", "postedon", "added", "dateadded", "posteddate"):
            canonical.setdefault("date_posted", idx)
        elif text in ("skills", "skill", "techstack", "technologies", "requirements"):
            canonical.setdefault("skills", idx)
    return canonical


def _row_to_dict(
    cells: list[str],
    header_map: dict[str, int],
    source_name: str,
    last_company: str = "",
) -> dict[str, Any]:
    """Convert a parsed row into a canonical listing dict."""
    row: dict[str, Any] = {}
    for key, idx in header_map.items():
        if 0 <= idx < len(cells):
            row[key] = cells[idx]
        else:
            row[key] = ""

    company_raw = row.get("company", "")
    role_raw = row.get("role", "")
    location_raw = row.get("location", "")
    link_raw = row.get("link", "")

    # Some sources put the application link in the role or company cell.
    link = _best_link(str(link_raw)) if link_raw else None
    if not link:
        link = _best_link(str(role_raw))
    if not link:
        link = _best_link(str(company_raw))

    company_text = _cell_text(company_raw)
    company = _clean_company(company_text)
    if not company or company == "↳":
        company = last_company

    role = _cell_text(role_raw)
    location = _cell_text(location_raw)

    date_posted = _cell_text(row.get("date_posted", "")) if "date_posted" in row else ""

    skills_raw = row.get("skills", "")
    skills: list[str] = []
    if skills_raw:
        skills = [s.strip() for s in re.split(r"[,;/]", _cell_text(skills_raw)) if s.strip() and s.strip().lower() not in ("no skills listed", "", "n/a")]

    raw_text = f"Company: {company} | Role: {role} | Location: {location} | Link: {link} | Posted: {date_posted} | Skills: {', '.join(skills) if skills else 'none'}"

    return {
        "company": company,
        "role": role,
        "location": location,
        "link": link,
        "date_posted": date_posted,
        "skills": skills,
        "source": source_name,
        "raw_text": raw_text,
        "_id": listing_id(company, role, link),
    }


# ---------------------------------------------------------------------------
# Source-specific parsers
# ---------------------------------------------------------------------------


def _parse_json_jobs(data: dict[str, Any] | list[Any], source_name: str) -> list[dict[str, Any]]:
    """Parse a JSON jobs feed. Handles zshah101 and ApplyGuy shapes."""
    listings: list[dict[str, Any]] = []

    jobs: list[dict[str, Any]] = []
    if isinstance(data, list):
        jobs = data
    elif isinstance(data, dict):
        # Many feeds nest the list under "jobs".
        for key in ("jobs", "listings", "positions", "data"):
            if key in data and isinstance(data[key], list):
                jobs = data[key]
                break

    for job in jobs:
        if not isinstance(job, dict):
            continue

        company = job.get("company") or ""
        role = job.get("title") or job.get("role") or ""
        location = job.get("location") or ""

        # Prefer the original employer URL over tracker/redirect URLs.
        link = job.get("listingUrl") or job.get("url") or job.get("link") or job.get("applicationUrl") or ""

        date_posted = (
            job.get("posted")
            or job.get("posted_at")
            or job.get("date_posted")
            or job.get("age")
            or ""
        )

        skills = job.get("skills") or []
        if not isinstance(skills, list):
            skills = [str(skills).strip()] if str(skills).strip() else []

        raw_text = json.dumps(job, default=str, ensure_ascii=False)

        listings.append({
            "company": str(company).strip(),
            "role": str(role).strip(),
            "location": str(location).strip(),
            "link": str(link).strip() or None,
            "date_posted": str(date_posted).strip(),
            "skills": skills,
            "source": source_name,
            "raw_text": raw_text[:2000],
            "_id": listing_id(str(company), str(role), str(link)),
        })

    return listings


def _parse_yc_api(data: list[dict[str, Any]], source_name: str) -> list[dict[str, Any]]:
    """Parse the YC Companies API (companies/hiring.json).

    Each company object has a `jobs` list. We keep internship/co-op titles
    (title contains 'intern', 'co-op', 'work term', etc.).
    """
    listings: list[dict[str, Any]] = []

    for company in data:
        if not isinstance(company, dict):
            continue

        company_name = (company.get("name") or "").strip()
        jobs = company.get("jobs") or []
        if not jobs:
            continue

        for job in jobs:
            if not isinstance(job, dict):
                continue

            title = (job.get("title") or "").strip()
            if not title:
                continue

            # Only keep internship/co-op listings. Some YC job titles use
            # "Summer 2026/2027 Intern" or "Intern" or "Co-op".
            if not re.search(r"intern|co-op|coop|work[-\s]?term", title, re.IGNORECASE):
                continue

            location = (job.get("location") or "").strip()
            link = (job.get("url") or "").strip()
            skills = job.get("skills") or []
            skills_text = ", ".join(str(s).strip() for s in skills if s) if skills else ""

            raw_text = f"Company: {company_name} | Role: {title} | Location: {location} | Skills: {skills_text} | Link: {link}"

            listings.append({
                "company": company_name,
                "role": title,
                "location": location,
                "link": link or None,
                "date_posted": "",
                "skills": skills,
                "source": source_name,
                "raw_text": raw_text,
                "_id": listing_id(company_name, title, link),
            })

    return listings


def _parse_html_tables(text: str, source_name: str) -> list[dict[str, Any]]:
    """Parse HTML tables (e.g. SimplifyJobs README)."""
    listings: list[dict[str, Any]] = []
    soup = BeautifulSoup(text, "html.parser")

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue

        header_row = rows[0]
        ths = header_row.find_all("th") or header_row.find_all("td")
        headers = [_cell_text(th) for th in ths]
        hmap = _header_map(headers)

        if "company" not in hmap or "role" not in hmap:
            # Not a role listing table; skip.
            continue

        last_company = ""
        for tr in rows[1:]:
            tds = tr.find_all("td")
            if not tds:
                continue

            # Pass the Tag objects so _cell_text can extract clean text.
            row = _row_to_dict(tds, hmap, source_name, last_company)
            if row["company"]:
                last_company = row["company"]
            if row["role"]:
                listings.append(row)

    return listings


def _parse_markdown_tables(text: str, source_name: str) -> list[dict[str, Any]]:
    """Parse markdown pipe tables (e.g. vanshb03, ApplyGuy READMEs)."""
    listings: list[dict[str, Any]] = []

    # Find blocks of consecutive lines that start with '|'
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip().startswith("|"):
            current.append(line)
        else:
            if current:
                blocks.append(current)
                current = []
    if current:
        blocks.append(current)

    for block in blocks:
        if len(block) < 3:
            continue

        # First line is header, second should be separator like |---|---|.
        header_line = block[0]
        separator = block[1]
        data_lines = block[2:]

        # Validate separator contains dashes.
        if not re.search(r"[-]{2,}", separator):
            continue

        headers = [h.strip() for h in header_line.split("|")]
        # Remove leading/trailing empty cells caused by leading/trailing pipes.
        if headers and headers[0] == "":
            headers = headers[1:]
        if headers and headers[-1] == "":
            headers = headers[:-1]

        hmap = _header_map(headers)
        if "company" not in hmap or "role" not in hmap:
            continue

        last_company = ""
        for line in data_lines:
            cells = [c.strip() for c in line.split("|")]
            if cells and cells[0] == "":
                cells = cells[1:]
            if cells and cells[-1] == "":
                cells = cells[:-1]

            row = _row_to_dict(cells, hmap, source_name, last_company)
            if row["company"]:
                last_company = row["company"]
            if row["role"]:
                listings.append(row)

    return listings


def _parse_hn_algolia(story_search_text: str, source_name: str) -> list[dict[str, Any]]:
    """Parse the latest Hacker News 'Who is hiring?' thread via Algolia.

    Finds the most recent monthly hiring thread, then searches its comments
    for 'intern'. HN comments are unstructured, so we extract a best-guess
    company/role from the first line and let the LLM do the final classification.
    """
    listings: list[dict[str, Any]] = []

    try:
        data = json.loads(story_search_text)
    except json.JSONDecodeError:
        _log("Could not parse HN Algolia story search JSON")
        return listings

    hits = data.get("hits") or []
    if not hits:
        return listings

    # Pick the most recent 'Ask HN: Who is hiring?' story.
    latest = None
    latest_ts = 0
    for hit in hits:
        title = (hit.get("title") or "").strip().lower()
        if title.startswith("ask hn: who is hiring"):
            ts = hit.get("created_at_i") or 0
            if ts > latest_ts:
                latest = hit
                latest_ts = ts

    if latest is None:
        _log("Could not find latest HN 'Who is hiring?' story")
        return listings

    story_id = latest.get("objectID") or latest.get("story_id")
    if not story_id:
        return listings

    # Search the comments for 'intern'. URL-encode the comma so it does not
    # break any CSV source-list formatting.
    comments_url = (
        f"https://hn.algolia.com/api/v1/search?"
        f"tags=comment%2Cstory_{story_id}&query=intern&hitsPerPage=1000"
    )
    comments_text = fetch(comments_url)
    if not comments_text:
        return listings

    try:
        comments_data = json.loads(comments_text)
    except json.JSONDecodeError:
        _log("Could not parse HN Algolia comments JSON")
        return listings

    for comment in comments_data.get("hits") or []:
        text = (comment.get("comment_text") or "").strip()
        if not text:
            continue

        # Decode HTML entities and strip tags.
        text = html.unescape(text)
        soup = BeautifulSoup(text, "html.parser")
        plain = soup.get_text("\n", strip=True)

        # Keep only comments that mention internships.
        if not re.search(r"\bintern(?:ship)?\b", plain, re.IGNORECASE):
            continue

        # Try the common HN format: Company | Role | Location | ...
        first_line = plain.splitlines()[0]
        parts = [p.strip() for p in first_line.split("|")]
        company = parts[0] if len(parts) > 0 else ""
        role = parts[1] if len(parts) > 1 else ""
        location = parts[2] if len(parts) > 2 else ""

        # If the first-line split didn't give us a real company/role, fall back
        # to the whole comment and let the LLM figure it out.
        if not company or not role:
            company = ""
            role = ""
            location = ""

        # Look for a target location anywhere in the plain text.
        loc_match, canonical = settings.location_matches_target(plain)
        if loc_match:
            location = canonical or location

        link = _best_link(text) or ""
        date_posted = ""
        created_at = comment.get("created_at")
        if created_at:
            try:
                date_posted = datetime.fromisoformat(created_at.replace("Z", "+00:00")).strftime("%Y-%m-%d")
            except Exception:
                date_posted = created_at[:10] if isinstance(created_at, str) else ""

        if not company and not role:
            # Use the first non-empty line as a placeholder role so the row is not dropped.
            role = first_line[:160]

        raw_text = (
            f"Company: {company} | Role: {role} | Location: {location} "
            f"| Link: {link} | Posted: {date_posted} | Skills: none"
        )

        listings.append({
            "company": company,
            "role": role,
            "location": location,
            "link": link,
            "date_posted": date_posted,
            "skills": [],
            "source": source_name,
            "raw_text": raw_text,
            "_id": listing_id(company, role, link),
        })

    return listings


# ---------------------------------------------------------------------------
# New source parsers (RSS, generic HTML/JSON-LD, LinkedIn, Indeed, etc.)
# ---------------------------------------------------------------------------


def _parse_rss(text: str, source_name: str) -> list[dict[str, Any]]:
    """Parse RSS 2.0 or Atom feeds into raw listings."""
    listings: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        _log(f"RSS parse error for {source_name}: {exc}")
        return listings

    if root.tag.endswith("feed"):
        # Atom feed
        entries = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    else:
        # RSS 2.0
        entries = root.findall(".//item")

    for entry in entries:
        title = ""
        title_el = entry.find("title")
        if title_el is not None and title_el.text:
            title = html.unescape(title_el.text.strip())

        link = ""
        if root.tag.endswith("feed"):
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            if link_el is not None:
                link = link_el.get("href", "") or ""
        else:
            link_el = entry.find("link")
            if link_el is not None:
                link = (link_el.text or "").strip()

        description = ""
        desc_el = entry.find("description") or entry.find("summary") or entry.find("content")
        if desc_el is not None and desc_el.text:
            description = BeautifulSoup(desc_el.text, "html.parser").get_text(" ", strip=True)
            description = html.unescape(description)[:500]

        date_posted = ""
        date_el = entry.find("pubDate") or entry.find("published") or entry.find("updated")
        if date_el is not None and date_el.text:
            date_posted = date_el.text.strip()[:30]

        # Try to split "Role at Company" or "Company - Role" from the title.
        company, role = "", ""
        if " at " in title:
            role, company = title.split(" at ", 1)
        elif title and " - " in title:
            parts = title.split(" - ", 1)
            # Heuristic: if second part is short/capitalized, it's the company.
            company, role = parts[1], parts[0]
        else:
            role = title

        company = _clean_company(company)
        if not company and description:
            # Fallback: look for "Company: ..." in description.
            m = re.search(r"(?:company|employer|organization)\s*[:\-]\s*([^\n\r,]+)", description, re.IGNORECASE)
            if m:
                company = _clean_company(m.group(1))

        location = ""
        loc_match, canonical = settings.location_matches_target(description or title)
        if loc_match:
            location = canonical or ""

        raw_text = (
            f"Company: {company} | Role: {role} | Location: {location} "
            f"| Link: {link} | Posted: {date_posted} | Description: {description[:200]}"
        )

        listings.append({
            "company": company,
            "role": role or title,
            "location": location,
            "link": link or None,
            "date_posted": date_posted,
            "skills": [],
            "source": source_name,
            "raw_text": raw_text,
            "_id": listing_id(company, role or title, link),
        })

    return listings


def _extract_jsonld_job_postings(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Extract job postings from schema.org JSON-LD embedded in HTML."""
    listings: list[dict[str, Any]] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.get_text(strip=True))
        except (json.JSONDecodeError, TypeError):
            continue

        items = data if isinstance(data, list) else [data]
        for item in items:
            types = item.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if not any("JobPosting" in t for t in types):
                continue

            role = (item.get("title") or "").strip()
            if not role:
                continue

            company = ""
            org = item.get("hiringOrganization") or {}
            if isinstance(org, dict):
                company = (org.get("name") or "").strip()
            elif isinstance(org, str):
                company = org.strip()

            location = ""
            loc = item.get("jobLocation") or {}
            if isinstance(loc, list):
                loc = loc[0] if loc else {}
            if isinstance(loc, dict):
                address = loc.get("address") or {}
                if isinstance(address, dict):
                    parts = [
                        address.get("addressLocality"),
                        address.get("addressRegion"),
                        address.get("addressCountry"),
                    ]
                    location = ", ".join(p for p in parts if p)
                elif isinstance(address, str):
                    location = address

            link = ""
            if item.get("url"):
                link = item.get("url").strip()
            elif item.get("directApply"):
                link = str(item.get("directApply")).strip()

            date_posted = (item.get("datePosted") or "").strip()[:30]
            deadline = (item.get("validThrough") or "").strip()[:30] or None

            raw_text = json.dumps(item, ensure_ascii=False, indent=2)[:2000]

            listings.append({
                "company": company,
                "role": role,
                "location": location,
                "link": link or None,
                "date_posted": date_posted,
                "skills": [],
                "source": "JSON-LD",
                "raw_text": raw_text,
                "_id": listing_id(company, role, link),
            })

    return listings


def _extract_html_job_cards(soup: BeautifulSoup, url: str, source_name: str) -> list[dict[str, Any]]:
    """Heuristic fallback: find links and headings that mention intern/coop."""
    listings: list[dict[str, Any]] = []
    parsed = urlparse(url)
    site = parsed.netloc.replace("www.", "")

    # Find all clickable job-like titles.
    intern_pattern = re.compile(r"\b(intern|co-op|coop|work term|workterm)\b", re.IGNORECASE)

    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        if not text or not intern_pattern.search(text):
            continue

        href = a["href"].strip()
        if not href.startswith("http"):
            href = urlunparse(parsed._replace(path=href if href.startswith("/") else "/" + href))
        href = normalize_url(href)
        if not href or href in seen:
            continue
        seen.add(href)

        role = text[:160]
        company = _clean_company(site)

        # Try to find a nearby heading/company name in the page.
        parent = a.find_parent(["li", "div", "tr", "article"])
        snippet = ""
        if parent:
            snippet = parent.get_text(" ", strip=True)[:400]
            m = re.search(r"(?:at|@)\s+([A-Z][A-Za-z0-9\s&]+)", snippet)
            if m:
                company = _clean_company(m.group(1))

        loc_match, canonical = settings.location_matches_target(snippet or text)
        location = canonical or ""

        raw_text = (
            f"Company: {company} | Role: {role} | Location: {location} "
            f"| Link: {href} | Snippet: {snippet[:200]}"
        )

        listings.append({
            "company": company,
            "role": role,
            "location": location,
            "link": href,
            "date_posted": "",
            "skills": [],
            "source": source_name,
            "raw_text": raw_text,
            "_id": listing_id(company, role, href),
        })

    return listings


def _parse_generic_html(text: str, url: str, source_name: str) -> list[dict[str, Any]]:
    """Parse generic career pages / job board HTML.

    Tries schema.org JSON-LD first, then falls back to heuristic link extraction.
    """
    soup = BeautifulSoup(text, "html.parser")
    listings = _extract_jsonld_job_postings(soup)
    if listings:
        return listings
    return _extract_html_job_cards(soup, url, source_name)


def _parse_linkedin(url: str) -> list[dict[str, Any]]:
    """Scrape LinkedIn job search via the public guest API.

    Experimental: LinkedIn rate-limits heavily, so this may return few or no
    results. Supports both /jobs/search?... URLs and the guest API directly.
    """
    listings: list[dict[str, Any]] = []
    parsed = urlparse(url)
    if parsed.netloc != "www.linkedin.com":
        return listings

    query = parse_qs(parsed.query)
    if parsed.path.startswith("/jobs-guest/jobs/api/seeMoreJobPostings"):
        base_url = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
    else:
        # Convert a normal search URL into the guest API.
        keywords = query.get("keywords", [""])[0]
        location = query.get("location", [""])[0]
        geo_id = query.get("geoId", [""])[0]
        f_e = query.get("f_E", [""])[0]
        f_i = query.get("f_I", [""])[0]
        qd = {k: v[0] for k, v in query.items() if k not in ("start",) and v[0]}
        qd["keywords"] = keywords or "software engineering intern"
        if location:
            qd["location"] = location
        base_url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?" + urlencode(qd, doseq=True)

    for start in [0, 25, 50, 75, 100]:
        page_url = f"{base_url}&start={start}" if "?" in base_url else f"{base_url}?start={start}"
        if "&start=" in base_url:
            page_url = re.sub(r"[?&]start=\d+", f"&start={start}", base_url)

        text = fetch(page_url, headers=DEFAULT_HEADERS)
        if not text:
            break

        if "HTTP 999" in text or "captcha" in text.lower():
            _log("LinkedIn returned a bot/captcha block. Stopping LinkedIn scrape.")
            break

        soup = BeautifulSoup(text, "html.parser")
        cards = soup.find_all("li")
        if not cards:
            break

        found_on_page = 0
        for card in cards:
            title_el = card.select_one("h3.base-search-card__title")
            company_el = card.select_one("h4.base-search-card__subtitle")
            loc_el = card.select_one("span.base-search-card__metadata")
            link_el = card.select_one("a.base-card__full-link") or card.find("a", href=True)

            if not title_el:
                continue

            role = title_el.get_text(strip=True)
            company = _clean_company(company_el.get_text(strip=True)) if company_el else ""
            location = loc_el.get_text(strip=True) if loc_el else ""
            link = link_el["href"].strip() if link_el and link_el.get("href") else ""

            if not company:
                company = _clean_company("LinkedIn")

            raw_text = (
                f"Company: {company} | Role: {role} | Location: {location} "
                f"| Link: {link} | Source: LinkedIn"
            )

            listings.append({
                "company": company,
                "role": role,
                "location": location,
                "link": link or None,
                "date_posted": "",
                "skills": [],
                "source": "LinkedIn",
                "raw_text": raw_text,
                "_id": listing_id(company, role, link),
            })
            found_on_page += 1

        if found_on_page < 5:
            # Likely at the end of results or blocked.
            break

        time.sleep(0.5)

    return listings


def _parse_indeed(url: str) -> list[dict[str, Any]]:
    """Attempt to parse Indeed job search results.

    Indeed uses bot protection, so this is best-effort and often returns nothing.
    """
    listings: list[dict[str, Any]] = []
    text = fetch(url, headers=DEFAULT_HEADERS)
    if not text:
        return listings

    # Try the embedded JSON payload first.
    match = re.search(
        r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]=(\{.+?\});',
        text,
    )
    if match:
        try:
            data = json.loads(match.group(1))
            results = data.get("metaData", {}).get("mosaicProviderJobCardsModel", {}).get("results", [])
            for job in results:
                role = (job.get("title") or "").strip()
                company = (job.get("company") or "").strip()
                location = (job.get("formattedLocation") or "").strip()
                job_key = (job.get("jobkey") or "").strip()
                link = f"https://www.indeed.com/viewjob?jk={job_key}" if job_key else ""

                listings.append({
                    "company": company,
                    "role": role,
                    "location": location,
                    "link": link or None,
                    "date_posted": "",
                    "skills": [],
                    "source": "Indeed",
                    "raw_text": json.dumps(job, ensure_ascii=False, indent=2)[:2000],
                    "_id": listing_id(company, role, link),
                })
            if listings:
                return listings
        except Exception as exc:
            _log(f"Indeed JSON parse failed: {exc}")

    # Fallback to HTML selectors.
    soup = BeautifulSoup(text, "html.parser")
    for card in soup.find_all("div", class_=re.compile(r"jobCard|jobSeen|slider_container")):
        title_el = card.find("h2", class_="jobTitle") or card.find("a", id=re.compile(r"job_"))
        if not title_el:
            continue
        role = title_el.get_text(strip=True)
        company_el = card.find(attrs={"data-testid": "company-name"}) or card.find("span", class_=re.compile(r"companyName"))
        company = company_el.get_text(strip=True) if company_el else ""
        loc_el = card.find(attrs={"data-testid": "text-location"}) or card.find("div", class_=re.compile(r"companyLocation"))
        location = loc_el.get_text(strip=True) if loc_el else ""
        link_el = title_el if title_el.name == "a" else title_el.find("a", href=True)
        link = link_el["href"].strip() if link_el and link_el.get("href") else ""

        listings.append({
            "company": company,
            "role": role,
            "location": location,
            "link": link or None,
            "date_posted": "",
            "skills": [],
            "source": "Indeed",
            "raw_text": f"Company: {company} | Role: {role} | Location: {location}",
            "_id": listing_id(company, role, link),
        })

    return listings


def _parse_unsupported(url: str, name: str, reason: str) -> list[dict[str, Any]]:
    """Log a clear warning for sources that need authentication or heavy bot work."""
    _log(f"{name} is not directly supported ({reason}): {url}")
    return []


def scrape_one_source(url: str) -> list[dict[str, Any]]:
    """Fetch and parse one source URL, returning canonical raw listings."""
    parsed = urlparse(url)

    # LinkedIn job search / guest API.
    if parsed.netloc == "www.linkedin.com":
        return _parse_linkedin(url)

    # Indeed job search.
    if parsed.netloc.endswith("indeed.com"):
        return _parse_indeed(url)

    # Glassdoor and Handshake are too bot/login-heavy for simple requests.
    if parsed.netloc.endswith("glassdoor.com"):
        return _parse_unsupported(url, "Glassdoor", "requires login / heavy bot protection")
    if parsed.netloc.endswith("joinhandshake.com"):
        return _parse_unsupported(url, "Handshake", "requires .edu login")

    text = fetch(url)
    if text is None:
        return []

    # Hacker News "Who is hiring?" thread (non-GitHub source).
    if parsed.netloc == "hn.algolia.com" and "whoishiring" in url.lower():
        return _parse_hn_algolia(text, "HN Who is hiring")

    if parsed.netloc.endswith("github.com") or parsed.netloc.endswith("githubusercontent.com"):
        parts = parsed.path.strip("/").split("/")
        source_name = "/".join(parts[:2]) if len(parts) >= 2 else parsed.netloc
    elif "/yc-api/" in parsed.path or "ycombinator" in url.lower():
        source_name = "Y Combinator"
    else:
        source_name = parsed.netloc or url
    source_type = guess_source_type(text, url)

    if source_type == "rss":
        return _parse_rss(text, source_name)

    if source_type == "json":
        try:
            data = json.loads(text)

            # YC Companies API has a list of company objects with nested jobs.
            if "/yc-api/" in parsed.path or "ycombinator" in url.lower():
                if isinstance(data, list):
                    return _parse_yc_api(data, source_name)
                return []

            return _parse_json_jobs(data, source_name)
        except json.JSONDecodeError as exc:
            _log(f"JSON parse error for {url}: {exc}")
            return []

    if source_type == "html":
        # HTML tables (e.g. SimplifyJobs README) take priority.
        table_listings = _parse_html_tables(text, source_name)
        if table_listings:
            return table_listings
        # Career pages and job boards are usually generic HTML, not tables.
        return _parse_generic_html(text, url, source_name)

    return _parse_markdown_tables(text, source_name)


def get_raw_listings(
    source_urls: list[str] | None = None,
    progress: Any = None,
    task_id: Any = None,
) -> list[dict[str, Any]]:
    """Fetch and normalize raw listings from all configured sources."""
    if source_urls is None:
        source_urls = list(settings.SCRAPE_SOURCE_URLS)
        source_urls.extend(settings.RSS_FEEDS)
        source_urls.extend(settings.COMPANY_CAREER_URLS)
        source_urls.extend(settings.LINKEDIN_SEARCH_URLS)

    if progress is not None and task_id is not None:
        progress.update(task_id, total=len(source_urls))

    all_listings: list[dict[str, Any]] = []
    for i, url in enumerate(source_urls, start=1):
        listings = scrape_one_source(url)
        _log(f"{url} -> {len(listings)} raw rows")
        all_listings.extend(listings)
        if progress is not None and task_id is not None:
            progress.advance(task_id)

    # Deduplicate by id, then by (company, role, location) for same posting with different links.
    by_id: dict[str, dict[str, Any]] = {}
    by_key: dict[str, dict[str, Any]] = {}
    for listing in all_listings:
        if not listing.get("role"):
            continue

        key = f"{listing['company'].lower().strip()}|{listing['role'].lower().strip()}|{listing.get('location', '').lower().strip()}"
        if listing["_id"] in by_id or key in by_key:
            continue
        by_id[listing["_id"]] = listing
        by_key[key] = listing

    return list(by_id.values())


if __name__ == "__main__":
    settings.ensure_data_files_exist()
    raw = get_raw_listings()
    print(f"\nTotal raw listings from all sources: {len(raw)}\n")
    if raw:
        print("First listing sample:")
        print(json.dumps(raw[0], indent=2, ensure_ascii=False))
