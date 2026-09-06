#!/usr/bin/env python3
"""Regression tests for verify.py live-side verification bugs.

Bug 1: list_items_in_collection must not pass --organizationid alongside
       --collectionid (bw 2026.8.0 ignores --collectionid when both are set).
Bug 2: title-only matching mispairs same-title items; must match by
       fingerprint (type, normalized title, username, primary URI) instead.
Bug 3: TOTP comparison must normalize otpauth:// URIs against bare seeds.
Bug 4: an absent collection is a PASS when the source side has 0 items.
Bug 5: _op_check must exclude archived-state 1pux items from its count and
       name differing items when counts still disagree.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from lib import bwcli
import verify


def _login_item(name: str, username: str, uri: str = "", totp: str | None = None) -> dict:
    return {
        "type": 1,
        "name": name,
        "login": {
            "username": username,
            "password": "pw",
            "totp": totp,
            "uris": [{"uri": uri}] if uri else [],
        },
        "notes": "",
        "fields": [],
    }


class TestCollectionListingOmitsOrgId(unittest.TestCase):
    def test_list_items_in_collection_does_not_pass_organizationid(self):
        with patch("lib.bwcli._run") as mock_run:
            mock_run.return_value = "[]"
            bwcli.list_items_in_collection("coll-1", "org-1")
            args = mock_run.call_args[0][0]
            self.assertIn("--collectionid", args)
            self.assertNotIn("--organizationid", args)

    def test_fingerprints_in_collection_does_not_pass_organizationid(self):
        with patch("lib.bwcli._run") as mock_run:
            mock_run.return_value = "[]"
            bwcli.fingerprints_in_collection("coll-1", "org-1")
            args = mock_run.call_args[0][0]
            self.assertNotIn("--organizationid", args)


class TestFingerprintMatching(unittest.TestCase):
    def test_same_title_twins_compare_against_correct_counterpart(self):
        """Two 'Roblox' items with different usernames must not cross-compare."""
        source_items = [
            (_login_item("Roblox", "alice"), 0),
            (_login_item("Roblox", "bob"), 0),
        ]
        live_items = [
            _login_item("Roblox", "bob"),
            _login_item("Roblox", "alice"),
        ]
        issues = verify._verify_items(source_items, live_items, "refuse")
        self.assertEqual(issues, [])

    def test_field_mismatch_detected_on_correct_twin_only(self):
        source_items = [
            (_login_item("Roblox", "alice"), 0),
            (_login_item("Roblox", "bob"), 0),
        ]
        live_items = [
            _login_item("Roblox", "bob"),
            {**_login_item("Roblox", "alice"), "login": {**_login_item("Roblox", "alice")["login"], "password": "different"}},
        ]
        issues = verify._verify_items(source_items, live_items, "refuse")
        joined = "\n".join(issues)
        self.assertIn("alice", joined)
        self.assertNotIn("bob", joined.split("alice")[0])  # bob's entry unaffected

    def test_missing_and_unexpected_reported_by_fingerprint(self):
        source_items = [(_login_item("Only Source", "u1"), 0)]
        live_items = [_login_item("Only Live", "u2")]
        issues = verify._verify_items(source_items, live_items, "refuse")
        joined = "\n".join(issues)
        self.assertIn("Missing", joined)
        self.assertIn("Only Source", joined)
        self.assertIn("Unexpected", joined)
        self.assertIn("Only Live", joined)


class TestTotpNormalization(unittest.TestCase):
    def test_otpauth_uri_matches_bare_seed(self):
        source = _login_item("Atlassian", "u", totp="otpauth://totp/Atlassian:u?secret=ABC234XYZ&issuer=Atlassian")
        live = _login_item("Atlassian", "u", totp="ABC234XYZ")
        diffs = verify._compare_item(source, live)
        self.assertEqual(diffs, [])

    def test_real_mismatch_still_detected(self):
        source = _login_item("Reddit", "u", totp="otpauth://totp/Reddit:u?secret=AAAA1111&issuer=Reddit")
        live = _login_item("Reddit", "u", totp="BBBB2222")
        diffs = verify._compare_item(source, live)
        self.assertTrue(any("totp" in d for d in diffs))


class TestEmptyVaultPasses(unittest.TestCase):
    def _run_verify_vault(self, source_items_count: int):
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            bulk_path = work_dir / "Empty.json"
            attach_path = work_dir / "Empty.attachments.json"
            items = [_login_item(f"Item{i}", "u") for i in range(source_items_count)]
            bulk_path.write_text(json.dumps({"items": items}))

            with patch("lib.bwcli.list_org_collections", return_value=[]):
                return verify._verify_vault(
                    "Empty", "Empty Collection", "org-1", "refuse", None, bulk_path, attach_path
                )

    def test_absent_collection_with_zero_source_items_passes(self):
        passed, issues = self._run_verify_vault(0)
        self.assertTrue(passed)

    def test_absent_collection_with_nonzero_source_items_fails(self):
        passed, issues = self._run_verify_vault(2)
        self.assertFalse(passed)


class TestOpCheckArchivedExclusion(unittest.TestCase):
    def _make_pux(self, tmpdir: Path, items: list[dict]) -> Path:
        export_data = {
            "accounts": [{
                "vaults": [{
                    "attrs": {"name": "Family"},
                    "items": items,
                }]
            }]
        }
        pux_path = tmpdir / "export.1pux"
        with zipfile.ZipFile(pux_path, "w") as zf:
            zf.writestr("export.data", json.dumps(export_data))
        return pux_path

    def test_archived_items_excluded_from_expected_count(self):
        items = [
            {"state": "active", "overview": {"title": "Active One"}},
            {"state": "archived", "overview": {"title": "Archived One"}},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            pux_path = self._make_pux(Path(tmpdir), items)
            with patch("shutil.which", return_value="/usr/bin/op"), \
                 patch("subprocess.run") as mock_run, \
                 patch("builtins.print") as mock_print:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = json.dumps([{"title": "Active One"}])
                verify._op_check(pux_path, "acct")
                printed = "\n".join(str(c.args[0]) for c in mock_print.call_args_list)
                self.assertIn("1pux=1", printed)
                self.assertIn("OK", printed)

    def test_mismatch_names_differing_items(self):
        items = [
            {"state": "active", "overview": {"title": "Active One"}},
            {"state": "active", "overview": {"title": "Active Two"}},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            pux_path = self._make_pux(Path(tmpdir), items)
            with patch("shutil.which", return_value="/usr/bin/op"), \
                 patch("subprocess.run") as mock_run, \
                 patch("builtins.print") as mock_print:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = json.dumps([{"title": "Active One"}])
                verify._op_check(pux_path, "acct")
                printed = "\n".join(str(c.args[0]) for c in mock_print.call_args_list)
                self.assertIn("MISMATCH", printed)
                self.assertIn("Active Two", printed)


if __name__ == "__main__":
    unittest.main()
