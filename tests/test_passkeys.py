#!/usr/bin/env python3
"""Unit tests for passkeys.py.

Covers:
  - parse_manual: valid lines, optional fields, blank/comment lines, pipes.
  - build_html: progress count, grouping, no password values in output,
    no secret fields leak, localStorage keys are unique.
  - from_pux: zero hits on a clean export, correct item grouping.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import passkeys as pk


class TestParseManual(unittest.TestCase):

    def _parse(self, text: str, account: str = "test") -> list[dict]:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(text)
            path = Path(f.name)
        try:
            return pk.parse_manual(path, account)
        finally:
            path.unlink(missing_ok=True)

    def test_full_line(self):
        entries = self._parse("- Google | alice@example.com | https://accounts.google.com\n")
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["site"], "Google")
        self.assertEqual(e["username"], "alice@example.com")
        self.assertEqual(e["url"], "https://accounts.google.com")
        self.assertEqual(e["source"], "manual")
        self.assertEqual(e["account"], "test")

    def test_site_only(self):
        entries = self._parse("- GitHub\n")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["site"], "GitHub")
        self.assertEqual(entries[0]["username"], "")
        self.assertEqual(entries[0]["url"], "")

    def test_site_and_username_no_url(self):
        entries = self._parse("- GitHub | bob\n")
        self.assertEqual(entries[0]["username"], "bob")
        self.assertEqual(entries[0]["url"], "")

    def test_blank_lines_and_headers_ignored(self):
        text = "# My passkeys\n\n- Google | alice | https://google.com\n\nNot a passkey line\n"
        entries = self._parse(text)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["site"], "Google")

    def test_multiple_entries(self):
        text = "- Google | alice | https://google.com\n- GitHub | bob | https://github.com\n"
        entries = self._parse(text)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[1]["site"], "GitHub")

    def test_extra_whitespace_trimmed(self):
        entries = self._parse("-  Google  |  alice@example.com  |  https://google.com  \n")
        self.assertEqual(entries[0]["site"], "Google")
        self.assertEqual(entries[0]["username"], "alice@example.com")
        self.assertEqual(entries[0]["url"], "https://google.com")

    def test_empty_site_line_skipped(self):
        entries = self._parse("- | user | https://example.com\n")
        # site is empty string after stripping — should be skipped
        self.assertEqual(len(entries), 0)

    def test_dash_without_content_skipped(self):
        entries = self._parse("-\n")
        self.assertEqual(len(entries), 0)


class TestBuildHtml(unittest.TestCase):

    def _entries(self) -> list[dict]:
        return [
            {"account": "family", "source": "manual", "site": "Google",
             "username": "alice@example.com", "url": "https://accounts.google.com"},
            {"account": "family", "source": "manual", "site": "GitHub",
             "username": "alice", "url": "https://github.com"},
            {"account": "team", "source": "bitwarden", "site": "Slack",
             "username": "alice@corp.example", "url": "https://slack.com"},
        ]

    def test_no_password_values_in_html(self):
        entries = self._entries()
        secret = "xK9mZqR2VwPbNsYt"  # unique secret that must not appear
        entries[0]["password"] = secret
        entries[0]["login"] = {"password": secret}
        html = pk.build_html(entries)
        self.assertNotIn(secret, html, "Secret password value must not appear in HTML")

    def test_progress_total_matches_entry_count(self):
        entries = self._entries()
        html = pk.build_html(entries)
        # The JS total variable must equal the number of entries.
        self.assertIn(f"var total={len(entries)}", html)

    def test_sites_appear_in_html(self):
        entries = self._entries()
        html = pk.build_html(entries)
        for e in entries:
            self.assertIn(e["site"], html)

    def test_urls_are_tappable_links(self):
        entries = self._entries()
        html = pk.build_html(entries)
        for e in entries:
            if e.get("url"):
                self.assertIn(f'href="{e["url"]}"', html)

    def test_localStorage_keys_are_unique(self):
        entries = self._entries()
        html = pk.build_html(entries)
        import re
        keys = re.findall(r'data-key="([^"]+)"', html)
        self.assertEqual(len(keys), len(entries), "Expected one checkbox per entry")
        self.assertEqual(len(set(keys)), len(keys), "localStorage keys must be unique")

    def test_grouped_by_account(self):
        entries = self._entries()
        html = pk.build_html(entries)
        family_pos = html.find("family")
        team_pos = html.find("team")
        self.assertGreater(family_pos, 0)
        self.assertGreater(team_pos, 0)

    def test_no_external_resources(self):
        html = pk.build_html(self._entries())
        import re
        # No src= pointing to external URLs and no link rel=stylesheet.
        external_src = re.findall(r'src=["\']https?://', html)
        external_link = re.findall(r'<link[^>]+href=["\']https?://', html)
        self.assertEqual(external_src, [], "HTML must not reference external scripts")
        self.assertEqual(external_link, [], "HTML must not reference external stylesheets")

    def test_empty_entries_produces_valid_html(self):
        html = pk.build_html([])
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("var total=0", html)

    def test_xss_escaping(self):
        entries = [
            {"account": 'evil"><script>alert(1)</script>', "source": "manual",
             "site": "<b>XSS</b>", "username": "", "url": ""},
        ]
        html = pk.build_html(entries)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<b>XSS</b>", html)


class TestFromPux(unittest.TestCase):
    """from_pux must return zero hits on a clean export and report hits correctly."""

    @classmethod
    def setUpClass(cls):
        # Regenerate fixture
        _make_fixture = importlib.util.spec_from_file_location(
            "make_fixture", _ROOT / "tests" / "make_fixture.py"
        )
        _mf_mod = importlib.util.module_from_spec(_make_fixture)
        _make_fixture.loader.exec_module(_mf_mod)
        _mf_mod.build_fixture()
        cls.fixture_path = _ROOT / "tests" / "fixture.1pux"

    def test_no_passkey_hits_on_clean_fixture(self):
        entries = pk.from_pux(self.fixture_path, "test")
        self.assertEqual(entries, [],
                         f"Clean fixture should have no passkey hits; got {entries}")

    def test_returns_list(self):
        result = pk.from_pux(self.fixture_path, "test")
        self.assertIsInstance(result, list)

    def test_pux_with_webauthn_key_detected(self):
        """Inject a fake webauthn field and verify it is caught."""
        import io, zipfile, json as _json

        fake_item = {
            "uuid": "webauthn000000000000000000000001",
            "favIndex": 0,
            "createdAt": 1700000000,
            "updatedAt": 1700000000,
            "state": "active",
            "categoryUuid": "001",
            "details": {
                "loginFields": [
                    {"value": "tester@example.com", "id": "username", "name": "username",
                     "fieldType": "T", "designation": "username"},
                ],
                "notesPlain": "",
                "sections": [
                    {
                        "title": "webauthn credentials",
                        "name": "webauthn_section",
                        "fields": [],
                    }
                ],
                "passwordHistory": [],
            },
            "overview": {"title": "Webauthn Test Site", "urls": [], "tags": []},
        }

        export_data = {
            "accounts": [{
                "attrs": {"accountName": "Test", "name": "Test", "email": "t@t.com",
                          "uuid": "u1", "domain": "t.1password.com"},
                "vaults": [{
                    "attrs": {"uuid": "v1", "name": "Personal", "type": "P"},
                    "items": [fake_item],
                }]
            }]
        }

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("export.attributes", _json.dumps({"version": "3"}))
            zf.writestr("export.data", _json.dumps(export_data))
        buf.seek(0)

        with tempfile.NamedTemporaryFile(suffix=".1pux", delete=False) as f:
            f.write(buf.getvalue())
            fake_path = Path(f.name)

        try:
            entries = pk.from_pux(fake_path, "test-account")
            self.assertEqual(len(entries), 1, f"Expected 1 hit, got {entries}")
            self.assertEqual(entries[0]["site"], "Webauthn Test Site")
            self.assertEqual(entries[0]["account"], "test-account")
            self.assertTrue(entries[0]["hits"], "hits must be non-empty")
            # No secrets: no password values in the entry
            for e in entries:
                self.assertNotIn("password", e)
        finally:
            fake_path.unlink(missing_ok=True)


class TestScanJson(unittest.TestCase):

    def test_finds_key_match(self):
        hits: list = []
        pk._scan_json({"webauthn_id": "abc123"}, "root", hits)
        self.assertEqual(len(hits), 1)
        kind, path, snippet = hits[0]
        self.assertEqual(kind, "key")
        self.assertIn("webauthn_id", path)

    def test_finds_value_match(self):
        hits: list = []
        pk._scan_json({"type": "fido2"}, "root", hits)
        self.assertEqual(len(hits), 1)
        kind, path, snippet = hits[0]
        self.assertEqual(kind, "value")
        self.assertEqual(snippet, "fido2")

    def test_no_false_positives_on_clean_dict(self):
        hits: list = []
        pk._scan_json({"username": "alice", "password": "s3cr3t", "url": "https://example.com"}, "root", hits)
        self.assertEqual(hits, [])

    def test_recursive_list(self):
        hits: list = []
        pk._scan_json([{"a": "passkey here"}, {"b": "nothing"}], "root", hits)
        self.assertEqual(len(hits), 1)
        self.assertIn("value", hits[0][0])


if __name__ == "__main__":
    unittest.main()
