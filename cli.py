"""
Main CLI dispatcher for the `internship-bot` command.

Supports:
- `internship-bot` / `internship-bot run` — daily scrape/filter/send pipeline
- `internship-bot --dry-run` / `internship-bot --limit N` — pipeline options
- `internship-bot remove` — uninstall the bot from this device
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import tempfile
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

console = Console()


def _nonnegative(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


def _read_listings(path: Path) -> tuple[bytes, list[str], list[dict[str, str]]]:
    original = path.read_bytes()
    reader = csv.DictReader(io.StringIO(original.decode("utf-8-sig")), strict=True)
    headers = reader.fieldnames or []
    rows = list(reader)
    if not {"id", "company", "role"}.issubset(headers):
        raise ValueError("CSV is missing the id, company, or role column")
    if any(None in row or None in row.values() for row in rows):
        raise ValueError("CSV contains a row with an unexpected number of fields")
    return original, headers, rows


def _listing_command(args: list[str]) -> None:
    parser = argparse.ArgumentParser(prog=f"internship-bot {args[0]}")
    if args[0] == "list":
        parser.add_argument("--search", default="", help="Find text in any CSV field")
        parser.add_argument("--limit", type=_nonnegative, default=0, help="Maximum rows to show (0 = all)")
        parser.add_argument("--details", action="store_true", help="Show all fields, including IDs and application links")
    else:
        parser.add_argument("scope", choices=["all"], help="Clear all saved CSV rows after confirmation")
    options = parser.parse_args(args[1:])

    from config import settings

    path = settings.CSV_FILE
    try:
        if not path.exists() or path.stat().st_size == 0:
            console.print("No saved listings yet.")
            return
        original, headers, rows = _read_listings(path)
        if not rows:
            console.print("No saved listings yet.")
            return
        if args[0] == "erase":
            console.print(f"Permanently erase all {len(rows)} saved listings from {path}?", markup=False)
            console.print("CSV headers will be kept. Email history, pending listings, and settings are unchanged.")
            try:
                confirmation = input("Type ERASE ALL to confirm (anything else cancels): ")
            except (EOFError, KeyboardInterrupt):
                confirmation = ""
            if confirmation.strip() != "ERASE ALL":
                console.print("Cancelled. Nothing was erased.")
                return
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    csv.writer(stream).writerow(headers)
                if path.read_bytes() != original:
                    console.print("CSV changed during confirmation. Nothing was erased; run the command again.")
                    return
                os.replace(temporary, path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            console.print(f"Erased {len(rows)} saved listings. CSV headers preserved.")
            return

        matches = [(index, row) for index, row in enumerate(rows, 1)
                   if options.search.casefold() in " ".join(row.values()).casefold()]
        shown = matches[:options.limit] if options.limit else matches
        console.print(f"Showing {len(shown)} of {len(matches)} matching listings ({len(rows)} saved total).")
        if not shown:
            return
        if options.details:
            for index, row in shown:
                table = Table(title=f"Listing {index}", show_header=False)
                table.add_column("Field", style="cyan", no_wrap=True)
                table.add_column("Value", overflow="fold")
                for key in headers:
                    table.add_row(Text(key.replace("_", " ").title()), Text(row[key]))
                console.print(table)
        else:
            columns = [key for key in ("company", "role", "location", "date_added", "status") if key in headers]
            table = Table(title="Saved listings", show_lines=True)
            table.add_column("#", justify="right")
            for key in columns:
                table.add_column(key.replace("_", " ").title(), overflow="fold")
            for index, row in shown:
                table.add_row(str(index), *(Text(row[key]) for key in columns))
            console.print(table)
            console.print("Use --details to see every field, including application links.")
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        console.print(f"Could not access saved listings: {exc}", markup=False)
        raise SystemExit(1) from exc


def main() -> None:
    """Dispatch to the daily run or the uninstall flow."""
    if len(sys.argv) == 2 and sys.argv[1] in ("--help", "-h"):
        console.print(
            "Usage: internship-bot [--dry-run] [--limit N]\n"
            "       internship-bot list [--search TEXT] [--limit N] [--details]\n"
            "       internship-bot erase all\n"
            "       internship-bot remove\n\n"
            "Without a subcommand, runs the daily pipeline. list shows saved CSV listings.\n"
            "erase all clears CSV rows after confirmation; remove uninstalls the bot.",
            markup=False,
        )
    elif len(sys.argv) > 1 and sys.argv[1].lower() in ("list", "erase"):
        _listing_command([sys.argv[1].lower(), *sys.argv[2:]])
    elif len(sys.argv) > 1 and sys.argv[1].lower() == "remove":
        from uninstall import main as remove_main

        remove_main()
    else:
        from run_daily import cli as run_cli

        run_cli()
