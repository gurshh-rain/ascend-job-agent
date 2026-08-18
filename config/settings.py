"""
Central settings for the internship bot.
Loads secrets/config from .env and exposes them as plain Python constants,
plus shared filesystem paths used across scraper/, llm/, email/, server/, sheet/.
"""

import os
import re
from pathlib import Path
from dotenv import load_dotenv


# --- Default multi-source scrape URLs ---
# You can override the full list via SCRAPE_SOURCE_URLS in .env (comma-separated).
# Each URL should point at a raw markdown file, an HTML table, or a JSON jobs feed.
DEFAULT_SCRAPE_SOURCES = [
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README.md",
    "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/main/README.md",
    "https://zshah101.github.io/Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships/api/jobs.json",
    "https://raw.githubusercontent.com/ApplyGuy/2027-Internships/main/data/internships.json",
    "https://raw.githubusercontent.com/sndsh404/summer-2027-internships/main/README.md",
    "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/README.md",
    "https://raw.githubusercontent.com/speedyapply/2027-AI-College-Jobs/main/README.md",
    "https://raw.githubusercontent.com/dreamworkhq/Tech-Internships-2027/main/README.md",
    "https://raw.githubusercontent.com/sonak11/internatlas/main/README.md",
    "https://raw.githubusercontent.com/aprameyak/2027-tech-jobs/main/README.md",
    "https://raw.githubusercontent.com/SuryaHarikrishnan/internship-tracker/master/listings/software-engineering.md",
    "https://raw.githubusercontent.com/SuryaHarikrishnan/internship-tracker/master/listings/data-science-ai-machine-learning.md",
    "https://raw.githubusercontent.com/jerrylin-23/2027-canada-internships/main/README.md",
    "https://raw.githubusercontent.com/zapplyjobs/Canada-Internships-2027/main/README.md",
    "https://raw.githubusercontent.com/zapplyjobs/Internships-2027/main/README.md",
    "https://raw.githubusercontent.com/negarprh/Canadian-Tech-Internships-2026/main/README-2027.md",
    "https://hn.algolia.com/api/v1/search_by_date?tags=story%2Cauthor_whoishiring&hitsPerPage=20",
    "https://devasheeshg.github.io/yc-api/companies/hiring.json",
]

# --- Paths ---
CONFIG_DIR = Path(__file__).resolve().parent
ROOT_DIR = CONFIG_DIR.parent
DATA_DIR = ROOT_DIR / "data"

PENDING_FILE = DATA_DIR / "pending.json"
SENT_LOG_FILE = DATA_DIR / "sent_log.json"
CSV_FILE = DATA_DIR / "internships.csv"

EMAIL_TEMPLATE_DIR = ROOT_DIR / "mailer" / "templates"
DIGEST_TEMPLATE_FILE = EMAIL_TEMPLATE_DIR / "digest_email.html"

# Load .env from config/.env
load_dotenv(CONFIG_DIR / ".env")


def _require(key: str, default: str | None = None) -> str:
    """Fetch an env var, raising a clear error if it's missing and no default given."""
    value = os.getenv(key, default)
    if value is None:
        raise RuntimeError(
            f"Missing required environment variable: {key}. "
            f"Did you copy config/.env.example to config/.env and fill it in?"
        )
    return value


# --- Ollama / LLM ---
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

# --- Email (SMTP) ---
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER)
EMAIL_TO = os.getenv("EMAIL_TO", SMTP_USER)

# --- Approve/Reject server ---
TUNNEL_BASE_URL = os.getenv("TUNNEL_BASE_URL", "http://localhost:8000")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))

# --- Scraper sources ---
INTERNSHIP_REPO_RAW_URL = os.getenv(
    "INTERNSHIP_REPO_RAW_URL",
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README.md",
)
SCRAPE_SOURCE_URLS = [
    u.strip()
    for u in os.getenv("SCRAPE_SOURCE_URLS", ",".join(DEFAULT_SCRAPE_SOURCES)).split(",")
    if u.strip()
]

# Optional extra sources for job boards, RSS feeds, company career pages, etc.
RSS_FEEDS = [
    u.strip()
    for u in os.getenv("RSS_FEEDS", "").split(",")
    if u.strip()
]
COMPANY_CAREER_URLS = [
    u.strip()
    for u in os.getenv("COMPANY_CAREER_URLS", "").split(",")
    if u.strip()
]

# LinkedIn search URLs are experimental and often rate-limited.
LINKEDIN_SEARCH_URLS = [
    u.strip()
    for u in os.getenv("LINKEDIN_SEARCH_URLS", "").split(",")
    if u.strip()
]

# --- Search filters ---
TARGET_ROLE_KEYWORDS = [
    kw.strip().lower()
    for kw in os.getenv("TARGET_ROLE_KEYWORDS", "software,swe,engineer,developer").split(",")
    if kw.strip()
]
TARGET_SEASON = os.getenv("TARGET_SEASON", "Summer 2027")

# --- Daily run settings ---
# Maximum number of listings to send per digest. 0 means no cap.
MAX_DAILY_LISTINGS = int(os.getenv("MAX_DAILY_LISTINGS", "0"))
# Maximum LLM calls per run. 0 means no cap.
MAX_LLM_CALLS = int(os.getenv("MAX_LLM_CALLS", "0"))
# Time of day to run (HH:MM, 24h). Used by the Task Scheduler setup in manage.py.
DAILY_RUN_TIME = os.getenv("DAILY_RUN_TIME", "09:00")

# Target locations (GTA, California, NYC) and aliases used for fast pre-filtering.
# The LLM does the final location classification, but these keywords let us skip
# obviously irrelevant listings before calling the local model.
TARGET_LOCATIONS = [
    loc.strip()
    for loc in os.getenv(
        "TARGET_LOCATIONS", "Greater Toronto Area,California,New York City"
    ).split(",")
    if loc.strip()
]

# Optional skill keywords. If set, the LLM will flag listings that mention or
# clearly require any of these skills. Matching listings are prioritized in the
# digest (not strictly filtered out unless there are enough matches to fill it).
TARGET_SKILLS = [
    skill.strip().lower()
    for skill in os.getenv("TARGET_SKILLS", "").split(",")
    if skill.strip()
]
TARGET_LOCATION_ALIASES = {
    "Greater Toronto Area": [
        "toronto", "gta", "greater toronto", "mississauga", "markham", "vaughan",
        "brampton", "north york", "scarborough", "etobicoke", "oakville",
        "richmond hill", "thornhill", "pickering", "ajax", "whitby", "oshawa",
        "burlington", "milton", "georgetown", "halton", "peel", "york region",
        "durham region", "ontario", "on, canada",
    ],
    "California": [
        "california", "ca", "san francisco", "sf", "bay area", "san jose",
        "palo alto", "mountain view", "sunnyvale", "menlo park", "cupertino",
        "los angeles", "la", "san diego", "irvine", "santa clara", "foster city",
        "redwood city", "belmont", "berkeley", "oakland", "pasadena", "santa monica",
        "culver city", "burbank", "glendale", "long beach", "orange county",
        "ventura", "thousand oaks", "san mateo", "menlo park", "saratoga",
        "campbell", "los gatos", "milpitas", "fremont", "hayward", "union city",
        "newark", "daly city", "south san francisco", "brisbane", "burlingame",
        "san bruno", "millbrae", " Pacific", "silicon valley",
    ],
    "New York City": [
        "new york", "nyc", "new york city", "manhattan", "brooklyn", "queens",
        "bronx", "staten island", "long island city", "williamsburg",
        "downtown manhattan", "midtown", "upper east side", "upper west side",
        "chelsea", "soho", "tribeca", "financial district", "fidi",
    ],
}


def location_matches_target(location: str) -> tuple[bool, str | None]:
    """Fast keyword-based location check. Returns (matches, canonical_region)."""
    if not location:
        return False, None
    loc_lower = location.lower()
    for region, aliases in TARGET_LOCATION_ALIASES.items():
        for alias in aliases:
            a = alias.strip()
            if not a:
                continue
            # Short aliases (CA, LA, SF, NYC, GTA, ON) can appear inside words,
            # so require word boundaries to avoid false positives.
            if len(a) <= 3:
                if re.search(r"\b" + re.escape(a) + r"\b", loc_lower):
                    return True, region
            else:
                if a in loc_lower:
                    return True, region
    return False, None


def deduplicate_listings(listings: list[dict]) -> list[dict]:
    """Remove duplicate listings based on company + role, ignoring season suffixes."""
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    season = re.escape(TARGET_SEASON.lower())

    for listing in listings:
        company = listing.get("company", "").strip().lower()
        role = listing.get("role", "").strip().lower()

        # Strip season suffixes like "- Summer 2027" or "(Summer 2027)" from the role.
        role = re.sub(rf"\s*[-–—]\s*{season}", " ", role, flags=re.IGNORECASE)
        role = re.sub(rf"\(\s*{season}\s*\)", " ", role, flags=re.IGNORECASE)
        role = re.sub(r"\s+", " ", role).strip(" -–—")

        key = (company, role)
        if key in seen:
            continue
        seen.add(key)
        unique.append(listing)

    return unique


def ensure_data_files_exist() -> None:
    """Create data/ dir and empty JSON/CSV files on first run if they don't exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not PENDING_FILE.exists():
        PENDING_FILE.write_text("{}")

    if not SENT_LOG_FILE.exists():
        SENT_LOG_FILE.write_text("{}")

    if not CSV_FILE.exists():
        CSV_FILE.write_text("id,company,role,location,link,deadline,date_added,status\n")


if __name__ == "__main__":
    # Quick sanity check you can run directly: python config/settings.py
    ensure_data_files_exist()
    print("Settings loaded OK.")
    print(f"OLLAMA_MODEL = {OLLAMA_MODEL}")
    print(f"DATA_DIR     = {DATA_DIR}")
    print(f"EMAIL_TO     = {EMAIL_TO}")