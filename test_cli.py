import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

import cli
from config import settings


class ListingCommandsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "internships.csv"
        self.header = ["id", "company", "role", "location", "link", "deadline", "date_added", "status"]
        with self.path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(self.header)
            writer.writerow(["001", "[red]Acme[/red]", "Mechanical Intern", "Toronto", "https://example.com/1", "", "2026-09-12", "approved"])
            writer.writerow(["002", "Beta", "Software Intern", "New York", "https://example.com/2", "", "2026-09-11", "approved"])
        self.output = io.StringIO()
        self.console = Console(file=self.output, width=160, color_system=None)
        self.addCleanup(patch.stopall)
        patch.object(settings, "CSV_FILE", self.path).start()
        patch.object(cli, "console", self.console).start()

    def invoke(self, *args):
        with patch.object(cli.sys, "argv", ["internship-bot", *args]):
            cli.main()

    def test_list_is_read_only_and_literal(self):
        original = self.path.read_bytes()
        self.invoke("list")
        self.assertIn("[red]Acme[/red]", self.output.getvalue())
        self.assertIn("Beta", self.output.getvalue())
        self.assertEqual(self.path.read_bytes(), original)

    def test_search_limit_and_details(self):
        self.invoke("list", "--search", "software", "--limit", "1", "--details")
        self.assertIn("Beta", self.output.getvalue())
        self.assertIn("https://example.com/2", self.output.getvalue())
        self.assertNotIn("Mechanical Intern", self.output.getvalue())

    def test_missing_and_empty_csv(self):
        self.path.unlink()
        self.invoke("list")
        self.assertFalse(self.path.exists())
        self.assertIn("No saved listings", self.output.getvalue())
        self.path.write_text(",".join(self.header) + "\n", encoding="utf-8")
        self.invoke("list")
        self.assertIn("No saved listings", self.output.getvalue())

    def test_cancel_erase_keeps_bytes(self):
        original = self.path.read_bytes()
        with patch("builtins.input", return_value="yes"):
            self.invoke("erase", "all")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertIn("Cancelled", self.output.getvalue())

    def test_confirmed_erase_preserves_header_and_other_files(self):
        other = self.path.with_name("sent_log.json")
        other.write_text('{"keep": true}', encoding="utf-8")
        with patch("builtins.input", return_value="ERASE ALL"):
            self.invoke("erase", "all")
        with self.path.open(newline="", encoding="utf-8") as stream:
            self.assertEqual(list(csv.reader(stream)), [self.header])
        self.assertEqual(other.read_text(), '{"keep": true}')

    def test_erase_aborts_when_file_changes_during_confirmation(self):
        def confirm(_):
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write("003,Gamma,Intern,,,,,approved\n")
            return "ERASE ALL"
        with patch("builtins.input", side_effect=confirm):
            self.invoke("erase", "all")
        self.assertIn("Gamma", self.path.read_text())
        self.assertIn("changed", self.output.getvalue())

    def test_eof_cancels_erase(self):
        original = self.path.read_bytes()
        with patch("builtins.input", side_effect=EOFError):
            self.invoke("erase", "all")
        self.assertEqual(self.path.read_bytes(), original)

    def test_malformed_csv_cannot_be_erased(self):
        self.path.write_text("company,role\nAcme,Intern\n", encoding="utf-8")
        original = self.path.read_bytes()
        with patch("builtins.input") as prompt, self.assertRaises(SystemExit):
            self.invoke("erase", "all")
        prompt.assert_not_called()
        self.assertEqual(self.path.read_bytes(), original)

    def test_replace_failure_preserves_original(self):
        original = self.path.read_bytes()
        with patch("builtins.input", return_value="ERASE ALL"), patch.object(cli.os, "replace", side_effect=PermissionError("File is open")):
            with self.assertRaises(SystemExit):
                self.invoke("erase", "all")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_help_lists_commands(self):
        self.invoke("--help")
        self.assertIn("internship-bot list", self.output.getvalue())
        self.assertIn("internship-bot erase all", self.output.getvalue())

    def test_invalid_arguments_do_not_touch_csv(self):
        original = self.path.read_bytes()
        for args in [("erase",), ("erase", "all", "--yes"), ("list", "--limit", "-1")]:
            with self.assertRaises(SystemExit):
                self.invoke(*args)
        self.assertEqual(self.path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
