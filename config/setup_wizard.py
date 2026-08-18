"""First-run setup wizard for internship-bot.

When the bot is run for the first time on a new device, this wizard prompts for
all the configuration it needs and writes it to config/.env. On later runs the
wizard is skipped and the saved config is used.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import questionary
import requests
from dotenv import load_dotenv, set_key

# Import settings after loading the current .env so we can update it in-place.
from config import settings


# Required fields that must contain real user values before the bot can run.
_REQUIRED = {
    "SMTP_USER": "Gmail address",
    "SMTP_PASSWORD": "Gmail App Password",
    "EMAIL_FROM": "From email",
    "EMAIL_TO": "To email",
}


def _looks_empty(value: Any) -> bool:
    """Return True if a setting still has a placeholder or is empty."""
    if value is None:
        return True
    text = str(value).strip()
    if not text:
        return True
    placeholders = {
        "youremail@gmail.com",
        "your-app-password",
        "replace-me",
        "http://localhost:8000",
    }
    if text in placeholders:
        return True
    # Also treat example/placeholder-ish Gmail as empty.
    if "youremail" in text.lower() or "example" in text.lower():
        return True
    return False


def _should_run_setup() -> bool:
    """Return True if critical settings are still missing or are placeholders."""
    for key, label in _REQUIRED.items():
        value = os.getenv(key)
        if _looks_empty(value):
            return True
    return False


def _save_env(key: str, value: str) -> None:
    """Write a key/value to config/.env and update the in-process settings."""
    env_path = settings.CONFIG_DIR / ".env"
    set_key(str(env_path), key, value, quote_mode="never")
    os.environ[key] = value
    setattr(settings, key, value)


def _save_list(key: str, values: list[str]) -> None:
    """Write a comma-separated list to config/.env and update settings."""
    value = ",".join(values)
    _save_env(key, value)

    # Update the corresponding settings attribute if it is a list.
    attr = key
    if hasattr(settings, attr):
        normalized = [v.strip() for v in values if v.strip()]
        if key in ("TARGET_ROLE_KEYWORDS", "TARGET_SKILLS"):
            normalized = [v.lower() for v in normalized]
        setattr(settings, attr, normalized)


def _save_int(key: str, value: int) -> None:
    """Write an integer to config/.env and update settings."""
    _save_env(key, str(value))
    if hasattr(settings, key):
        setattr(settings, key, value)


def _fetch_ollama_models(host: str) -> list[str]:
    """Return installed Ollama models, or an empty list if Ollama is not reachable."""
    try:
        response = requests.get(f"{host}/api/tags", timeout=5)
        response.raise_for_status()
        data = response.json()
        return [m.get("name", m.get("model", "")) for m in data.get("models", [])]
    except Exception:
        return []


def _prompt_ollama() -> tuple[str, str]:
    """Prompt for Ollama host and model."""
    print("\n[setup] LLM settings")
    host = questionary.text(
        "Ollama host URL:",
        default=settings.OLLAMA_HOST or "http://localhost:11434",
    ).unsafe_ask()

    models = _fetch_ollama_models(host)
    if models:
        print(f"[setup] Found {len(models)} Ollama model(s) on {host}.")
        model = questionary.select(
            "Choose the local model to use:",
            choices=models,
            default=settings.OLLAMA_MODEL if settings.OLLAMA_MODEL in models else None,
        ).unsafe_ask()
    else:
        print(
            "[setup] Could not reach Ollama. Make sure it is running and a model is pulled."
        )
        model = questionary.text(
            "Ollama model name:",
            default=settings.OLLAMA_MODEL or "qwen2.5:7b",
        ).unsafe_ask()

    return host, model


def _prompt_email() -> tuple[str, str, str, str]:
    """Prompt for Gmail / SMTP settings."""
    print("\n[setup] Email settings")
    smtp_user = questionary.text(
        "Your Gmail address:",
        default=os.getenv("SMTP_USER", ""),
        validate=lambda t: "@" in t or "Please enter a valid email address",
    ).unsafe_ask()

    smtp_password = questionary.password(
        "Gmail App Password (not your normal Gmail password):",
        default=os.getenv("SMTP_PASSWORD", ""),
        validate=lambda t: bool(t.strip()) or "Required",
    ).unsafe_ask()

    email_from = questionary.text(
        "From email address:",
        default=os.getenv("EMAIL_FROM", smtp_user),
    ).unsafe_ask()

    email_to = questionary.text(
        "Send digest to this email address:",
        default=os.getenv("EMAIL_TO", smtp_user),
    ).unsafe_ask()

    return smtp_user, smtp_password, email_from, email_to


def _prompt_filters() -> tuple[list[str], list[str], list[str], str]:
    """Prompt for role keywords, locations, skills, and season."""
    print("\n[setup] Search filters")
    role_keywords = questionary.text(
        "Target role keywords (comma-separated):",
        default=",".join(settings.TARGET_ROLE_KEYWORDS)
        or "software,swe,engineer,developer,ai,ml,machine learning,robotics",
    ).unsafe_ask()

    target_locations = questionary.text(
        "Target locations (comma-separated):",
        default=",".join(settings.TARGET_LOCATIONS)
        or "Greater Toronto Area,California,New York City",
    ).unsafe_ask()

    target_season = questionary.text(
        "Target season:",
        default=settings.TARGET_SEASON or "Summer 2027",
    ).unsafe_ask()

    target_skills = questionary.text(
        "Target skills (comma-separated, optional - used to prioritize listings):",
        default=",".join(settings.TARGET_SKILLS),
    ).unsafe_ask()

    return (
        [k.strip() for k in role_keywords.split(",") if k.strip()],
        [l.strip() for l in target_locations.split(",") if l.strip()],
        [s.strip().lower() for s in target_skills.split(",") if s.strip()],
        target_season.strip(),
    )


def _prompt_daily_settings() -> tuple[int, int, str]:
    """Prompt for daily caps and run time."""
    print("\n[setup] Daily run settings")
    max_daily = questionary.text(
        "Max listings to send per email:",
        default=str(settings.MAX_DAILY_LISTINGS or 25),
        validate=lambda t: t.isdigit() or "Please enter a number",
    ).unsafe_ask()

    max_llm = questionary.text(
        "Max LLM calls per run (0 = no cap):",
        default=str(settings.MAX_LLM_CALLS or 500),
        validate=lambda t: t.isdigit() or "Please enter a number",
    ).unsafe_ask()

    run_time = questionary.text(
        "Daily run time (HH:MM, 24h):",
        default=settings.DAILY_RUN_TIME or "09:00",
        validate=lambda t: bool(re.match(r"^\d{2}:\d{2}$", t)) or "Use HH:MM",
    ).unsafe_ask()

    return int(max_daily), int(max_llm), run_time


def _confirm_and_save(config: dict[str, Any]) -> None:
    """Persist the collected config to config/.env."""
    env_path = settings.CONFIG_DIR / ".env"

    # Load the example as a base if no .env exists yet, otherwise keep existing.
    if not env_path.exists() and (settings.CONFIG_DIR / ".env.example").exists():
        env_path.write_text(
            (settings.CONFIG_DIR / ".env.example").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    _save_env("OLLAMA_HOST", config["ollama_host"])
    _save_env("OLLAMA_MODEL", config["ollama_model"])
    _save_env("SMTP_USER", config["smtp_user"])
    _save_env("SMTP_PASSWORD", config["smtp_password"])
    _save_env("EMAIL_FROM", config["email_from"])
    _save_env("EMAIL_TO", config["email_to"])
    _save_env("TUNNEL_BASE_URL", "https://replace-me.trycloudflare.com")
    _save_env("SERVER_PORT", "8000")
    _save_list("SCRAPE_SOURCE_URLS", settings.DEFAULT_SCRAPE_SOURCES)
    _save_list("TARGET_ROLE_KEYWORDS", config["role_keywords"])
    _save_list("TARGET_LOCATIONS", config["target_locations"])
    _save_list("TARGET_SKILLS", config["target_skills"])
    _save_env("TARGET_SEASON", config["target_season"])
    _save_int("MAX_DAILY_LISTINGS", config["max_daily"])
    _save_int("MAX_LLM_CALLS", config["max_llm"])
    _save_env("DAILY_RUN_TIME", config["run_time"])

    # Reload dotenv so the file is fully up to date for the rest of the process.
    load_dotenv(env_path, override=True)


def run_setup() -> None:
    """Run the interactive first-time setup wizard."""
    print("=" * 60)
    print(" internship-bot first-time setup")
    print("=" * 60)
    print(
        "This wizard will ask for the settings needed to run the bot.\n"
        "Your answers will be saved to config/.env and you won't be asked again.\n"
    )

    ollama_host, ollama_model = _prompt_ollama()
    smtp_user, smtp_password, email_from, email_to = _prompt_email()
    role_keywords, target_locations, target_skills, target_season = _prompt_filters()
    max_daily, max_llm, run_time = _prompt_daily_settings()

    config = {
        "ollama_host": ollama_host,
        "ollama_model": ollama_model,
        "smtp_user": smtp_user,
        "smtp_password": smtp_password,
        "email_from": email_from,
        "email_to": email_to,
        "role_keywords": role_keywords,
        "target_locations": target_locations,
        "target_skills": target_skills,
        "target_season": target_season,
        "max_daily": max_daily,
        "max_llm": max_llm,
        "run_time": run_time,
    }

    _confirm_and_save(config)

    print("\n[setup] Configuration saved to config/.env.")
    print(f"[setup] Next run will use: {email_to}")
    print("=" * 60)


def ensure_setup() -> None:
    """Run setup only if the current config is missing critical values."""
    if _should_run_setup():
        run_setup()


if __name__ == "__main__":
    ensure_setup()
