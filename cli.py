"""
Main CLI dispatcher for the `internship-bot` command.

Supports:
- `internship-bot` / `internship-bot run` — daily scrape/filter/send pipeline
- `internship-bot --dry-run` / `internship-bot --limit N` — pipeline options
- `internship-bot remove` — uninstall the bot from this device
"""

from __future__ import annotations

import sys


def main() -> None:
    """Dispatch to the daily run or the uninstall flow."""
    if len(sys.argv) > 1 and sys.argv[1].lower() == "remove":
        from uninstall import main as remove_main

        remove_main()
    else:
        from run_daily import cli as run_cli

        run_cli()
