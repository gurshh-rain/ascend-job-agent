"""
Interactive terminal UI for managing internship-bot settings.

Run:
    python manage.py

You can edit search filters, the daily run time, the number of listings
per email, and install a Windows scheduled task.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import questionary
from config.setup_wizard import ensure_setup
import requests
from dotenv import get_key, set_key
from prompt_toolkit.styles import Style as PTStyle
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

ROOT_DIR = Path(__file__).resolve().parent
ENV_PATH = ROOT_DIR / "config" / ".env"

console = Console()


def _qstyle() -> PTStyle:
    """Custom prompt_toolkit style for questionary."""
    return PTStyle(
        [
            ("qmark", "fg:#2563eb bold"),       # blue question mark
            ("question", "fg:#111827 bold"),    # dark prompt text
            ("answer", "fg:#059669 bold"),      # green selected answer
            ("pointer", "fg:#2563eb bold"),     # blue pointer
            ("highlighted", "fg:#2563eb bold"), # blue highlighted item
            ("selected", "fg:#059669 bold"),    # green selected item
            ("separator", "fg:#6b7280"),        # gray separators
            ("instruction", "fg:#6b7280"),      # gray instructions
        ]
    )


SETTINGS: dict[str, dict[str, str]] = {
    "OLLAMA_HOST": {
        "label": "Ollama host",
        "help": "Base URL for the local Ollama server, e.g. http://localhost:11434",
    },
    "OLLAMA_MODEL": {
        "label": "Ollama model",
        "help": "Local LLM model used to classify listings. Pick from the list of installed models.",
    },
    "TARGET_ROLE_KEYWORDS": {
        "label": "Job title keywords",
        "help": "Comma-separated. Listings whose title/description matches any keyword are kept.",
    },
    "TARGET_LOCATIONS": {
        "label": "Target locations",
        "help": "Comma-separated regions. The bot keeps listings located in any of these.",
    },
    "TARGET_SKILLS": {
        "label": "Target skills",
        "help": "Optional comma-separated skills. Matching listings are shown first (e.g. python,c++,pytorch,react).",
    },
    "TARGET_SEASON": {
        "label": "Target season",
        "help": "The internship season to look for, e.g. Summer 2027.",
    },
    "MAX_DAILY_LISTINGS": {
        "label": "Max listings per email",
        "help": "Max listings sent each day. 0 means no cap. Extras are queued for the next day.",
    },
    "MAX_LLM_CALLS": {
        "label": "Max LLM calls per run",
        "help": "Limits how many listings the local LLM classifies each run. 0 means no cap.",
    },
    "DAILY_RUN_TIME": {
        "label": "Daily run time",
        "help": "24-hour time (HH:MM) used when installing the Windows scheduled task.",
    },
}


def _get_current() -> dict[str, str]:
    return {key: (get_key(str(ENV_PATH), key) or "") for key in SETTINGS}


def _save(key: str, value: str) -> None:
    set_key(str(ENV_PATH), key, value.strip(), quote_mode="never")


def _is_int(text: str) -> bool:
    return text.isdigit()


def _is_time(text: str) -> bool:
    return bool(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", text))


def _fetch_local_models(host: str) -> list[str]:
    """Ask Ollama for the list of installed models."""
    try:
        response = requests.get(f"{host.rstrip('/')}/api/tags", timeout=5)
        response.raise_for_status()
        data = response.json()
        names = [m.get("name") for m in data.get("models", []) if m.get("name")]
        return sorted(set(names))
    except Exception as exc:
        console.print(f"[yellow]Could not reach Ollama at {host}: {exc}[/]")
        return []


def _select_model(current: dict[str, str]) -> None:
    """Interactive picker for an installed Ollama model."""
    host = current.get("OLLAMA_HOST") or "http://localhost:11434"
    current_model = current.get("OLLAMA_MODEL", "qwen2.5:7b")

    console.print(Rule("[bold #2563eb]Select Ollama model[/]"))
    console.print(Panel(
        "The bot will ask your local Ollama server for installed models. "
        "Make sure Ollama is running on this machine.",
        border_style="#6b7280",
    ))
    console.print(f"[dim]Current model:[/] [bold #111827]{current_model}[/]")
    console.print(f"[dim]Ollama host:[/] [bold #111827]{host}[/]\n")

    models = _fetch_local_models(host)

    if models:
        # Ensure the current model is in the list so it can be the default.
        if current_model not in models:
            models.insert(0, current_model)

        choices = [questionary.Choice(name, value=name) for name in models]
        choices.append(questionary.Separator())
        choices.append(questionary.Choice("Type a model manually", value="__manual__"))

        chosen = questionary.select(
            "Choose a model",
            choices=choices,
            default=current_model if current_model in [c.value for c in choices] else None,
            qmark=">",
            style=_qstyle(),
        ).ask()

        if chosen == "__manual__" or chosen is None:
            chosen = questionary.text(
                "Enter model name (e.g. qwen2.5:7b)",
                default=current_model,
                qmark=">",
                style=_qstyle(),
            ).ask()
    else:
        console.print("[yellow]No models found. Enter the model name manually.[/]")
        chosen = questionary.text(
            "Enter model name (e.g. qwen2.5:7b)",
            default=current_model,
            qmark=">",
            style=_qstyle(),
        ).ask()

    if chosen and chosen.strip():
        chosen = chosen.strip()
        _save("OLLAMA_MODEL", chosen)
        current["OLLAMA_MODEL"] = chosen
        console.print(f"[bold green]Updated[/] Ollama model to [bold]{chosen}[/]")
    else:
        console.print("[dim]No change made.[/]")

    input("\nPress Enter to continue...")


def _header() -> None:
    console.print()
    console.print(
        Panel.fit(
            "[bold #2563eb]internship-bot[/]\n"
            "[dim]Settings manager[/]",
            border_style="#2563eb",
        ),
        justify="center",
    )


def _print_settings(current: dict[str, str]) -> None:
    table = Table(
        title="Current settings",
        show_header=True,
        header_style="bold #2563eb",
        border_style="#e5e7eb",
        title_style="bold #2563eb",
    )
    table.add_column("Setting", style="#111827", min_width=22)
    table.add_column("Value", style="#059669")

    for key, meta in SETTINGS.items():
        table.add_row(meta["label"], current[key])

    console.print(table)


def _edit(key: str, current: dict[str, str]) -> None:
    if key == "OLLAMA_MODEL":
        _select_model(current)
        return

    meta = SETTINGS[key]
    current_value = current[key]

    console.print(Rule(f"[bold #2563eb]Edit {meta['label']}[/]"))
    console.print(Panel(meta["help"], border_style="#6b7280"))
    console.print(f"[dim]Current value:[/] [bold #111827]{current_value}[/]")

    if key in ("MAX_DAILY_LISTINGS", "MAX_LLM_CALLS"):
        new = questionary.text(
            "Enter new value",
            default=current_value,
            validate=lambda text: _is_int(text) or "Please enter a whole number (0 for no cap).",
            qmark=">",
            style=_qstyle(),
        ).ask()
    elif key == "DAILY_RUN_TIME":
        new = questionary.text(
            "Enter new value",
            default=current_value,
            validate=lambda text: _is_time(text) or "Please use 24-hour time HH:MM, e.g. 09:00.",
            qmark=">",
            style=_qstyle(),
        ).ask()
    else:
        new = questionary.text(
            "Enter new value",
            default=current_value,
            qmark=">",
            style=_qstyle(),
        ).ask()

    if new is not None and new.strip() != current_value:
        _save(key, new)
        current[key] = new.strip()
        console.print(f"[bold green]Updated[/] {meta['label']} to [bold]{new.strip()}[/]")
    else:
        console.print("[dim]No change made.[/]")

    input("\nPress Enter to continue...")


def _view_env() -> None:
    console.print(Rule("[bold #2563eb]config/.env[/]"))
    if ENV_PATH.exists():
        console.print(ENV_PATH.read_text(encoding="utf-8"))
    else:
        console.print("[red].env not found[/]")
    input("\nPress Enter to continue...")


def _install_task(current: dict[str, str]) -> None:
    run_time = current["DAILY_RUN_TIME"]
    python_exe = sys.executable
    run_daily = ROOT_DIR / "run_daily.py"

    console.print(Rule("[bold #2563eb]Install Windows scheduled task[/]"))
    console.print(
        Panel(
            f"[bold]Task:[/] internship-bot-daily\n"
            f"[bold]Runs:[/] {python_exe}\n"
            f"[bold]Script:[/] {run_daily}\n"
            f"[bold]Schedule:[/] daily at {run_time}",
            border_style="#6b7280",
        )
    )

    if not questionary.confirm(
        "Create this scheduled task? (may require running as Administrator)",
        default=False,
        qmark=">",
        style=_qstyle(),
    ).ask():
        console.print("[dim]Task not created.[/]")
        input("\nPress Enter to continue...")
        return

    # schtasks command with quoted paths.
    command = (
        f'schtasks /create /tn "internship-bot-daily" '
        f'/tr "\\"{python_exe}\\" \\"{run_daily}\\"" '
        f'/sc daily /st {run_time} /f'
    )

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            console.print(
                f"[bold green]Success![/] Scheduled task created. It will run daily at {run_time}."
            )
        else:
            console.print("[bold red]Could not create the scheduled task.[/]")
            if result.stdout:
                console.print(result.stdout)
            if result.stderr:
                console.print(result.stderr)
            console.print(
                "\n[yellow]You can create it manually by running this as Administrator:[/]"
            )
            console.print(f"[dim]{command}[/]")
    except FileNotFoundError:
        console.print("[red]schtasks not found. Please create the task manually.[/]")

    input("\nPress Enter to continue...")


def main() -> None:
    ensure_setup()

    current = _get_current()

    while True:
        _header()
        _print_settings(current)

        choice = questionary.select(
            "What do you want to change?",
            choices=[
                questionary.Choice(meta["label"], value=key)
                for key, meta in SETTINGS.items()
            ]
            + [
                questionary.Separator(),
                questionary.Choice("Install Windows scheduled task", "install"),
                questionary.Choice("View .env file", "view"),
                questionary.Separator(),
                questionary.Choice("Exit", "exit"),
            ],
            qmark=">",
            style=_qstyle(),
        ).ask()

        if choice == "exit":
            console.print(
                Panel.fit(
                    "[bold green]Settings saved to config/.env.[/]\n"
                    "[dim]Goodbye![/]",
                    border_style="#059669",
                ),
                justify="center",
            )
            break
        elif choice == "install":
            _install_task(current)
        elif choice == "view":
            _view_env()
        else:
            _edit(choice, current)


if __name__ == "__main__":
    main()
