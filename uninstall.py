"""
Uninstall internship-bot from the local machine.

This is intentionally separate from the daily run. Running it will:
1. Remove the Windows scheduled task (if present).
2. Uninstall the installed package via pip.
3. Optionally remove local data files and config/.env.

The project source folder is NOT deleted unless the user explicitly asks for it,
because the user may want to keep the code, the CSV, or the .env file.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
ENV_FILE = ROOT_DIR / "config" / ".env"

console = Console()


def _run(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, shell=True, capture_output=True, text=True, check=False
    )


def _ask(prompt: str) -> bool:
    response = input(f"{prompt} (y/N): ").strip().lower()
    return response in ("y", "yes")


def main() -> None:
    console.print(
        "\n[bold red on white] WARNING [/bold red on white]",
        "This will remove the installed [bold]internship-bot[/] package, "
        "its console commands, and the Windows scheduled task.",
    )
    console.print(
        "Your data files ([bold]data/internships.csv[/], [bold]data/sent_log.json[/], "
        "[bold]data/pending.json[/]) and [bold]config/.env[/] will only be deleted "
        "if you explicitly say yes.\n"
    )

    confirm = input("Type [bold]uninstall[/] to continue, or anything else to abort: ")
    if confirm.strip().lower() != "uninstall":
        console.print("[yellow]Aborted. Nothing was changed.[/]")
        return

    # 1. Remove the Windows scheduled task if it exists.
    console.print("\n[run_daily] Removing Windows scheduled task...")
    result = _run('schtasks /delete /tn "internship-bot-daily" /f')
    if result.returncode == 0:
        console.print("[green]Scheduled task removed.[/]")
    else:
        console.print(
            "[yellow]Could not remove scheduled task (it may not exist or "
            "admin rights are required).[/]"
        )

    # 2. Uninstall the package from the current Python environment.
    console.print("\n[run_daily] Uninstalling internship-bot package...")
    result = _run("pip uninstall internship-bot -y")
    if result.returncode == 0:
        console.print("[green]Package uninstalled.[/]")
    else:
        console.print(
            "[yellow]pip uninstall returned an error. "
            "You may need to run: [bold]pip uninstall internship-bot -y[/][/]"
        )

    # 3. Optionally delete the source folder.
    if _ask("Delete the entire internship-bot project folder?"):
        console.print("\n[run_daily] Deleting project folder...")
        # On Windows, deleting a folder that contains running files can fail.
        # We delete as much as possible and ignore errors.
        shutil.rmtree(ROOT_DIR, ignore_errors=True)
        console.print("[green]Project folder deleted.[/]")
        console.print(
            "[dim]If any files remain, close this terminal and delete the folder "
            "manually.[/]"
        )
        return

    # 4. Optionally delete data files.
    if _ask("Delete data files (sent_log.json, pending.json, internships.csv)?"):
        for name in ("sent_log.json", "pending.json", "internships.csv"):
            path = DATA_DIR / name
            if path.exists():
                path.unlink()
                console.print(f"[green]Deleted {path}[/]")
            else:
                console.print(f"[dim]{path} not found, skipped.[/]")

    # 5. Optionally delete config/.env.
    if _ask("Delete config/.env?"):
        if ENV_FILE.exists():
            ENV_FILE.unlink()
            console.print("[green]Deleted config/.env[/]")
        else:
            console.print("[dim]config/.env not found, skipped.[/]")

    console.print(
        "\n[bold green]Uninstall complete.[/]\n"
        "[dim]The project source code is still on disk; delete the folder manually "
        "if you chose not to.[/]"
    )
