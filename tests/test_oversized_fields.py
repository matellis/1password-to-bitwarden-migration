"""Tests for Bitwarden's 5000-char field / 10000-char notes limits (lib/onepux.py)."""
import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from lib import onepux
import split as split_mod


def _login_item(title: str, extra_fields=None, notes_plain: str = "") -> dict:
    login_fields = [
        {"designation": "username", "value": "alice"},
        {"designation": "password", "value": "hunter2"},
    ]
    login_fields.extend(extra_fields or [])
    return {
        "state": "active",
        "categoryUuid": "001",
        "overview": {"title": title, "urls": []},
        "details": {
            "notesPlain": notes_plain,
            "loginFields": login_fields,
            "sections": [],
            "passwordHistory": [],
        },
    }


class TestOversizedLoginFields(unittest.TestCase):
    def _convert(self, raw_item):
        result = onepux.convert_vault_items([raw_item], "org-1", "coll-1", {})
        self.assertEqual(len(result.bulk_items), 1)
        return result, result.bulk_items[0]

    def test_field_under_limit_untouched(self):
        value = "x" * 100
        raw = _login_item("Small", [{"name": "Form Dump", "value": value}])
        result, item = self._convert(raw)
        field = next(f for f in item["fields"] if f["name"] == "Form Dump")
        self.assertEqual(field["value"], value)
        self.assertEqual(result.oversized_fields, [])
        self.assertIsNone(item["notes"])

    def test_oversized_field_moved_to_notes_when_it_fits(self):
        value = "x" * 5455
        raw = _login_item("Old TOS Dump", [{"name": "Form Dump", "value": value}])
        result, item = self._convert(raw)

        field = next(f for f in item["fields"] if f["name"] == "Form Dump")
        self.assertEqual(
            field["value"],
            "[full value moved to notes: exceeded Bitwarden's 5000-character field limit]",
        )
        self.assertIn(f"Form Dump:\n{value}", item["notes"])
        self.assertEqual(
            result.oversized_fields,
            ["Old TOS Dump: Form Dump (5455 chars, moved to notes)"],
        )

    def test_oversized_field_truncated_when_notes_budget_is_full(self):
        # Pre-fill notes so there is no room left to relocate the oversized field.
        filler_notes = "n" * 9800
        value = "x" * 5455
        raw = _login_item("Full Notes", [{"name": "Form Dump", "value": value}], notes_plain=filler_notes)
        result, item = self._convert(raw)

        field = next(f for f in item["fields"] if f["name"] == "Form Dump")
        self.assertTrue(field["value"].startswith("x" * 4900))
        self.assertIn("…[truncated from 5455 characters: exceeded Bitwarden limits]", field["value"])
        self.assertEqual(
            result.oversized_fields,
            ["Full Notes: Form Dump (5455 chars, truncated)"],
        )
        # The filler notes plain text must still be present, untruncated by this path.
        self.assertIn(filler_notes, item["notes"])

    def test_notes_over_limit_truncated_with_marker(self):
        huge_notes = "n" * 12000
        raw = _login_item("Huge Notes", notes_plain=huge_notes)
        result, item = self._convert(raw)

        self.assertLessEqual(
            len(item["notes"]),
            9900 + len("…[truncated: exceeded Bitwarden's 10000-character notes limit]"),
        )
        self.assertTrue(item["notes"].endswith(
            "…[truncated: exceeded Bitwarden's 10000-character notes limit]"
        ))
        self.assertEqual(result.oversized_fields, [])


class TestOversizedFieldsReporting(unittest.TestCase):
    @patch("split.onepux.vault_slug", return_value="vault-slug")
    @patch("split.onepux.make_collection", return_value=("coll-id", {"id": "coll-id", "name": "Shared"}))
    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_split_prints_warning_for_oversized_field(
        self, mock_parse, mock_vaults, mock_coll, mock_slug
    ):
        raw_item = _login_item("Old TOS Dump", [{"name": "Form Dump", "value": "x" * 5455}])
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [{"attrs": {"type": "U", "name": "Shared"}, "items": [raw_item]}]
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "bitwardenOrgId": "org-123",
            "vaultRename": {},
            "vaultDestination": {"Shared": "shared"},
        }
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            (work_dir / "files").mkdir()
            stderr_buf = io.StringIO()
            with redirect_stderr(stderr_buf):
                split_mod._process_account(account, work_dir, False)
        self.assertIn("Old TOS Dump: Form Dump (5455 chars, moved to notes)", stderr_buf.getvalue())


if __name__ == "__main__":
    unittest.main()
