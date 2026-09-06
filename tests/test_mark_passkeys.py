#!/usr/bin/env python3
"""Tests for passkey inventory loading and split.py --mark-passkeys logic."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from lib import passkey_inventory as pkinv


# --- helpers ---

def _bw_login(name: str, username: str, url: str) -> dict:
    return {
        "type": 1,
        "name": name,
        "login": {
            "username": username,
            "uris": [{"uri": url}] if url else [],
        },
    }


class TestLoadInventoryMarkdown(unittest.TestCase):

    def _write(self, text: str) -> Path:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
        f.write(text)
        f.flush()
        return Path(f.name)

    def test_full_line(self):
        p = self._write("- Google | alice@example.com | https://accounts.google.com\n")
        entries = pkinv.load_inventory(p)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["site"], "Google")
        self.assertEqual(entries[0]["username"], "alice@example.com")
        self.assertEqual(entries[0]["url"], "https://accounts.google.com")

    def test_site_only(self):
        p = self._write("- GitHub\n")
        entries = pkinv.load_inventory(p)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["username"], "")
        self.assertEqual(entries[0]["url"], "")

    def test_blank_lines_ignored(self):
        p = self._write("# header\n\n- Google | alice | https://google.com\n\nNot a passkey\n")
        entries = pkinv.load_inventory(p)
        self.assertEqual(len(entries), 1)

    def test_empty_site_skipped(self):
        p = self._write("- | user | https://example.com\n")
        entries = pkinv.load_inventory(p)
        self.assertEqual(len(entries), 0)


class TestLoadInventoryBridgeJSON(unittest.TestCase):

    def _write(self, data: dict) -> Path:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(data, f)
        f.flush()
        return Path(f.name)

    def test_entries_key(self):
        p = self._write({"entries": [
            {"title": "Google", "username": "alice@example.com", "url": "https://accounts.google.com"},
            {"title": "GitHub", "username": "alice", "url": "https://github.com"},
        ]})
        entries = pkinv.load_inventory(p)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["site"], "Google")
        self.assertEqual(entries[1]["site"], "GitHub")

    def test_site_key_as_fallback(self):
        p = self._write({"entries": [{"site": "Slack", "username": "bob", "url": ""}]})
        entries = pkinv.load_inventory(p)
        self.assertEqual(entries[0]["site"], "Slack")

    def test_list_format(self):
        p = self._write([{"title": "Dropbox", "username": "carol", "url": "https://dropbox.com"}])
        entries = pkinv.load_inventory(p)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["site"], "Dropbox")

    def test_empty_title_skipped(self):
        p = self._write({"entries": [{"title": "", "username": "u"}]})
        entries = pkinv.load_inventory(p)
        self.assertEqual(len(entries), 0)


class TestMatchesInventory(unittest.TestCase):

    def test_exact_match(self):
        inv = [{"site": "Google", "username": "alice@example.com", "url": "https://accounts.google.com"}]
        item = _bw_login("Google", "alice@example.com", "https://accounts.google.com")
        self.assertTrue(pkinv.matches_inventory(item, inv))

    def test_host_only_url_match(self):
        inv = [{"site": "Google", "username": "alice@example.com", "url": "accounts.google.com"}]
        item = _bw_login("Google", "alice@example.com", "https://accounts.google.com/signin")
        self.assertTrue(pkinv.matches_inventory(item, inv))

    def test_no_url_in_inventory_matches_on_title_username(self):
        inv = [{"site": "GitHub", "username": "alice", "url": ""}]
        item = _bw_login("GitHub", "alice", "https://github.com/login")
        self.assertTrue(pkinv.matches_inventory(item, inv))

    def test_different_username_no_match(self):
        inv = [{"site": "Google", "username": "bob@example.com", "url": "https://accounts.google.com"}]
        item = _bw_login("Google", "alice@example.com", "https://accounts.google.com")
        self.assertFalse(pkinv.matches_inventory(item, inv))

    def test_different_title_no_match(self):
        inv = [{"site": "Gmail", "username": "alice@example.com", "url": "https://accounts.google.com"}]
        item = _bw_login("Google", "alice@example.com", "https://accounts.google.com")
        self.assertFalse(pkinv.matches_inventory(item, inv))

    def test_non_login_type_never_matches(self):
        inv = [{"site": "Google", "username": "", "url": ""}]
        item = {"type": 2, "name": "Google", "login": {}}
        self.assertFalse(pkinv.matches_inventory(item, inv))

    def test_case_insensitive_title(self):
        inv = [{"site": "google", "username": "alice@example.com", "url": "https://accounts.google.com"}]
        item = _bw_login("Google", "alice@example.com", "https://accounts.google.com")
        self.assertTrue(pkinv.matches_inventory(item, inv))

    def test_different_host_no_match(self):
        inv = [{"site": "Google", "username": "alice@example.com", "url": "https://google.com"}]
        item = _bw_login("Google", "alice@example.com", "https://accounts.google.com")
        self.assertFalse(pkinv.matches_inventory(item, inv))


class TestAppendMarker(unittest.TestCase):

    def test_no_notes_becomes_marker(self):
        result = pkinv.append_marker(None)
        self.assertEqual(result, pkinv.MARKER)

    def test_empty_notes_becomes_marker(self):
        result = pkinv.append_marker("")
        self.assertEqual(result, pkinv.MARKER)

    def test_existing_notes_gets_marker_appended(self):
        result = pkinv.append_marker("Keep this note.")
        self.assertTrue(result.startswith("Keep this note.\n"))
        self.assertIn(pkinv.MARKER, result)

    def test_marker_content(self):
        self.assertIn("[1Password migration]", pkinv.MARKER)
        self.assertIn("passkey", pkinv.MARKER.lower())
        self.assertIn("Re-register", pkinv.MARKER)
        self.assertIn("iOS Credential Exchange", pkinv.MARKER)


class TestMarkItemsInSplit(unittest.TestCase):
    """Verify that _mark_items in split.py modifies items correctly."""

    def _mark_items(self, items, inventory):
        import split
        return split._mark_items(items, inventory)

    def test_matching_item_gets_marker(self):
        item = _bw_login("Google", "alice@example.com", "https://accounts.google.com")
        item["notes"] = None
        inv = [{"site": "Google", "username": "alice@example.com", "url": "https://accounts.google.com"}]
        count = self._mark_items([item], inv)
        self.assertEqual(count, 1)
        self.assertEqual(item["notes"], pkinv.MARKER)

    def test_existing_notes_preserved(self):
        item = _bw_login("Google", "alice@example.com", "https://accounts.google.com")
        item["notes"] = "Original note."
        inv = [{"site": "Google", "username": "alice@example.com", "url": "https://accounts.google.com"}]
        self._mark_items([item], inv)
        self.assertIn("Original note.", item["notes"])
        self.assertIn(pkinv.MARKER, item["notes"])

    def test_non_matching_item_unchanged(self):
        item = _bw_login("GitHub", "alice@example.com", "https://github.com")
        item["notes"] = "Unchanged."
        inv = [{"site": "Google", "username": "alice@example.com", "url": "https://accounts.google.com"}]
        count = self._mark_items([item], inv)
        self.assertEqual(count, 0)
        self.assertEqual(item["notes"], "Unchanged.")

    def test_count_multiple_matches(self):
        items = [
            {**_bw_login("Google", "alice@example.com", "https://accounts.google.com"), "notes": None},
            {**_bw_login("GitHub", "alice", "https://github.com"), "notes": None},
            {**_bw_login("Slack", "alice@corp.example", "https://slack.com"), "notes": "work"},
        ]
        inv = [
            {"site": "Google", "username": "alice@example.com", "url": "https://accounts.google.com"},
            {"site": "GitHub", "username": "alice", "url": "https://github.com"},
        ]
        count = self._mark_items(items, inv)
        self.assertEqual(count, 2)
        self.assertEqual(items[2]["notes"], "work")

    def test_host_url_match_marks_item(self):
        item = _bw_login("Google", "alice@example.com", "https://accounts.google.com/o/oauth2")
        item["notes"] = None
        inv = [{"site": "Google", "username": "alice@example.com", "url": "accounts.google.com"}]
        count = self._mark_items([item], inv)
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
