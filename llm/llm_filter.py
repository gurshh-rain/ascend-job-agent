"""
LLM-based filter and normalizer for raw internship listings.

For each raw listing (optionally after a fast keyword pre-filter), it asks the
local Ollama model to:
    1. decide if it is a genuine TARGET_SEASON software/AI/ML/robotics internship;
    2. decide if it is located in one of the target regions (GTA, CA, NYC);
    3. extract/normalize company, role, location, link, and deadline.

Usage:
    from llm.llm_filter import filter_listings
    relevant = filter_listings(raw_listings)

Run directly to test on a few samples:
    python -m llm.llm_filter
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

# Allow running this file directly.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import settings
from llm.ollama_client import chat


SYSTEM_PROMPT = """You are an internship listing classifier.

Your job is to inspect one raw internship listing at a time and return a JSON
object with the following exact keys and no other output:

- "relevant": bool — true only if the role is a genuine Summer 2027 software
  engineering, software development, SWE, developer, AI, ML, machine learning,
  robotics, or computer-science internship OR co-op. Co-op positions count as
  internships. If the season is not stated, assume the target season from the
  source. Do not reject a role just because the title mentions a business unit
  (e.g. 'Asset Management') — if it says 'Software Developer' or 'Software
  Engineer' or 'Engineering Intern', it is relevant. Product management, sales,
  marketing, HR, pure data analytics, quant trading, and non-technical roles
  should be false.

- "in_target_location": bool — true only if the posting is physically located
  in, or explicitly remote within, one of these three target regions:
    * Greater Toronto Area (GTA), Ontario, Canada
    * California, USA
    * New York City (NYC), USA
  Generic "Remote" or "United States" without specifying one of those regions
  is NOT enough. If multiple locations are listed, the listing is in the target
  location if at least one of them is.

- "matched_region": string or null — one of "Greater Toronto Area",
  "California", "New York City", or null. If multiple regions match, pick the
  first one you find in the location text.

- "company": string — the clean company name.
- "role": string — the clean role title.
- "location": string — the clean location(s); use commas between multiple
  locations. Keep the original text as concise as possible.
- "link": string — the best direct application URL (prefer the employer's
  career site over aggregator/tracker links).
- "deadline": string or null — the application deadline if visible, else null.
- "matches_skills": bool — true if the role explicitly mentions or clearly
  requires any of the user's target skills. Check the "skills" field, the role
  title, and any description. If no target skills are provided, set this to true.

Return ONLY the JSON object, with no markdown code fences and no explanation."""

USER_PROMPT_TEMPLATE = """Target season: {season}
Target role keywords: {role_keywords}
Target locations: {target_locations}
{skills_line}

Raw listing:
{listing_json}

Return the JSON object."""


def _pre_filter(raw: dict[str, Any]) -> tuple[bool, str | None]:
    """Fast keyword pre-filter to skip obviously irrelevant listings."""
    role = f"{raw.get('company', '')} {raw.get('role', '')} {raw.get('raw_text', '')}"
    location = raw.get("location", "")

    role_match = any(kw in role.lower() for kw in settings.TARGET_ROLE_KEYWORDS)
    loc_match, _ = settings.location_matches_target(location)

    if not role_match:
        return False, "role keyword"
    if not loc_match:
        return False, "location"
    return True, None


def _stable_id(company: str, role: str, link: str | None) -> str:
    payload = (
        f"{company.strip().lower()}|"
        f"{role.strip().lower()}|"
        f"{(link or '').strip().lower()}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _extract_json(text: str) -> dict[str, Any] | None:
    """Defensively pull a JSON object out of the model response."""
    text = text.strip()

    # Strip markdown code fences if the model added them.
    if text.startswith("```"):
        # Could be ```json or just ```
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def filter_listings(
    raw_listings: list[dict[str, Any]],
    fast_pre_filter: bool = True,
    progress_every: int = 10,
) -> list[dict[str, Any]]:
    """
    Run each raw listing through the local LLM and return the ones that are
    relevant and in a target location.

    Args:
        raw_listings: Output from scraper.get_raw_listings().
        fast_pre_filter: If True, skip rows that don't contain any target role
            keyword or target location alias before calling the LLM.
        progress_every: Print a progress log every N LLM calls.
    """
    results: list[dict[str, Any]] = []
    llm_calls = 0
    max_llm_calls = settings.MAX_LLM_CALLS

    for idx, raw in enumerate(raw_listings, start=1):
        if fast_pre_filter:
            passes, reason = _pre_filter(raw)
            if not passes:
                continue

        if 0 < max_llm_calls <= llm_calls:
            print(f"[llm_filter] reached MAX_LLM_CALLS cap ({max_llm_calls}), stopping")
            break

        skills = settings.TARGET_SKILLS
        skills_line = (
            f"Target skills: {', '.join(skills)}"
            if skills
            else "Target skills: (none configured)"
        )

        prompt = USER_PROMPT_TEMPLATE.format(
            season=settings.TARGET_SEASON,
            role_keywords=", ".join(settings.TARGET_ROLE_KEYWORDS),
            target_locations=", ".join(settings.TARGET_LOCATIONS),
            skills_line=skills_line,
            listing_json=json.dumps(raw, ensure_ascii=False, indent=2),
        )

        try:
            response = chat(
                prompt,
                system=SYSTEM_PROMPT,
                options={"temperature": 0.0, "seed": 42},
            )
            data = _extract_json(response)
            if data is None:
                print(f"[llm_filter] row {idx}: malformed JSON, skipping")
                continue

            if data.get("relevant") and data.get("in_target_location"):
                company = (data.get("company") or raw.get("company", "")).strip()
                role = (data.get("role") or raw.get("role", "")).strip()
                location = (data.get("location") or raw.get("location", "")).strip()
                link = (data.get("link") or raw.get("link") or "").strip()
                deadline = data.get("deadline") if data.get("deadline") not in (None, "null") else ""

                # If the LLM lost the link, fall back to the scraper's link.
                if not link:
                    link = raw.get("link", "") or ""

                # Use the scraper's pre-computed id for stable dedup across runs,
                # falling back to a hash of the LLM-extracted fields if needed.
                listing_id = raw.get("_id") or _stable_id(company, role, link)

                # If target skills are configured, default a missing answer to false.
                default_match = not settings.TARGET_SKILLS
                matches_skills = data.get("matches_skills")
                if matches_skills is None:
                    matches_skills = default_match

                listing = {
                    "id": listing_id,
                    "company": company,
                    "role": role,
                    "location": location,
                    "link": link,
                    "deadline": deadline,
                    "date_posted": raw.get("date_posted", ""),
                    "source": raw.get("source", ""),
                    "matched_region": data.get("matched_region"),
                    "matches_skills": matches_skills,
                    "skills": raw.get("skills", []),
                }
                results.append(listing)

            llm_calls += 1
            if llm_calls % progress_every == 0:
                print(f"[llm_filter] processed {llm_calls} LLM calls, kept {len(results)} so far")

        except RuntimeError as exc:
            print(f"[llm_filter] Ollama error on row {idx}: {exc}")
            break
        except Exception as exc:
            print(f"[llm_filter] unexpected error on row {idx}: {exc}")
            continue

    results = settings.deduplicate_listings(results)
    print(f"[llm_filter] finished: {llm_calls} LLM calls, {len(results)} relevant listings")
    return results


if __name__ == "__main__":
    samples = [
        {
            "company": "Notion",
            "role": "Software Engineer Intern - Summer 2027",
            "location": "San Francisco, CA",
            "link": "https://jobs.ashbyhq.com/notion/3fba1c39-c5cb-47d7-9ad2-1cec4d7e9d0c",
            "date_posted": "1d",
            "source": "test",
            "raw_text": "Company: Notion | Role: Software Engineer Intern - Summer 2027 | Location: San Francisco, CA",
            "_id": "test1",
        },
        {
            "company": "KPMG",
            "role": "Software Developer Intern Co-op - Asset Management Digital Solutions",
            "location": "Toronto, ON, Canada",
            "link": "https://careers.kpmg.ca/jobs/33306",
            "date_posted": "1d",
            "source": "test",
            "raw_text": "Company: KPMG | Role: Software Developer Intern Co-op | Location: Toronto, ON, Canada",
            "_id": "test2",
        },
        {
            "company": "Jane Street",
            "role": "Quantitative Researcher Intern",
            "location": "New York, NY",
            "link": "https://www.janestreet.com/join-jane-street/position/12345/",
            "date_posted": "2d",
            "source": "test",
            "raw_text": "Company: Jane Street | Role: Quantitative Researcher Intern | Location: New York, NY",
            "_id": "test3",
        },
    ]

    # Run with fast pre-filter disabled so the LLM classifies every sample.
    filtered = filter_listings(samples, fast_pre_filter=False, progress_every=1)
    print("\nFiltered listings:")
    print(json.dumps(filtered, indent=2, ensure_ascii=False))
