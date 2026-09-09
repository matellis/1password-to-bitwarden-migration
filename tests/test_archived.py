"""Tests for archived.py — archived 1Password items → Bitwarden archive."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import archived
from lib import bwcli


def _raw(title, state, notes=None):
    return {
        "uuid": title.lower(),
        "categoryUuid": "001",
        "state": state,
        "overview": {"title": title, "url": "https://x.example"},
        "details": {"loginFields": [{"designation": "username", "value": "u"},
                                    {"designation": "password", "value": "p"}],
                    "notesPlain": notes},
    }


def _export(vault_name, items, vault_type="U"):
    return {"accounts": [{"vaults": [{"attrs": {"name": vault_name, "type": vault_type}, "items": items}]}]}


class TestCollect(unittest.TestCase):
    def test_only_archived_items_from_manifest_vaults(self):
        data = _export("Work", [_raw("Live", "active"), _raw("Old", "archived", "hello")])
        out = archived.collect_archived(data, {}, [{"vaultName": "Work"}], {}, "org")
        self.assertEqual(list(out), ["Work"])
        self.assertEqual([e["item"]["name"] for e in out["Work"]], ["Old"])
        self.assertTrue(out["Work"][0]["item"]["notes"].startswith(archived.ARCHIVED_NOTE))
        self.assertIn("hello", out["Work"][0]["item"]["notes"])

    def test_vault_not_in_manifest_ignored(self):
        data = _export("Other", [_raw("Old", "archived")])
        out = archived.collect_archived(data, {}, [{"vaultName": "Work"}], {}, "org")
        self.assertEqual(out, {})

    def test_rename_applied(self):
        data = _export("Private", [_raw("Old", "archived")])
        out = archived.collect_archived(data, {}, [{"vaultName": "Private (Team)"}],
                                        {"Private": "Private (Team)"}, "")
        self.assertEqual(list(out), ["Private (Team)"])


class TestHelpers(unittest.TestCase):
    def test_prefixed_name_idempotent(self):
        self.assertEqual(archived.prefixed_name("A"), "[archived] A")
        self.assertEqual(archived.prefixed_name("[archived] A"), "[archived] A")

    def test_is_archived_live_item(self):
        self.assertTrue(archived.is_archived_live_item({"archivedDate": "2026-01-01"}))
        self.assertTrue(archived.is_archived_live_item({"name": "[archived] x"}))
        self.assertFalse(archived.is_archived_live_item({"name": "x", "archivedDate": None}))


class TestProcessVault(unittest.TestCase):
    def _entries(self):
        data = _export("Work", [_raw("Old", "archived")])
        return archived.collect_archived(data, {}, [{"vaultName": "Work"}], {}, "org")["Work"]

    @patch.object(bwcli, "sync")
    @patch.object(bwcli, "list_org_collections", return_value=[{"id": "c1", "name": "Work"}])
    @patch.object(bwcli, "list_items_in_collection", return_value=[])
    @patch.object(bwcli, "create_item", return_value={"id": "i1"})
    @patch.object(bwcli, "archive_item", side_effect=bwcli.BWError("no"))
    @patch.object(bwcli, "get_item", return_value={"id": "i1", "name": "Old"})
    @patch.object(bwcli, "edit_item")
    def test_org_falls_back_to_prefix(self, edit, get, arch, create, *_):
        r = archived.process_vault("Work", self._entries(), False, "org", False)
        self.assertEqual(r["counts"]["prefixed"], 1)
        self.assertEqual(create.call_args[0][0]["collectionIds"], ["c1"])
        self.assertEqual(edit.call_args[0][1]["name"], "[archived] Old")

    @patch.object(bwcli, "sync")
    @patch.object(bwcli, "list_folders", return_value=[{"id": "f1", "name": "Work"}])
    @patch.object(bwcli, "list_items_in_folder", return_value=[])
    @patch.object(bwcli, "create_personal_item", return_value={"id": "i1"})
    @patch.object(bwcli, "archive_item")
    def test_personal_archives(self, arch, create, *_):
        r = archived.process_vault("Work", self._entries(), True, None, False)
        self.assertEqual(r["counts"]["archived"], 1)
        self.assertEqual(create.call_args[0][0]["folderId"], "f1")
        self.assertIsNone(create.call_args[0][0]["organizationId"])
        arch.assert_called_once_with("i1")

    @patch.object(bwcli, "sync")
    @patch.object(bwcli, "list_folders", return_value=[{"id": "f1", "name": "Work"}])
    @patch.object(bwcli, "create_personal_item")
    def test_skips_existing_even_when_prefixed(self, create, *_):
        live = [{"type": 1, "name": "[archived] Old", "login": {"username": "u", "uris": [{"uri": "https://x.example"}]}}]
        with patch.object(bwcli, "list_items_in_folder", return_value=live):
            r = archived.process_vault("Work", self._entries(), True, None, False)
        self.assertEqual(r["counts"]["skipped"], 1)
        create.assert_not_called()

    @patch.object(bwcli, "sync", side_effect=AssertionError("bw must not be called"))
    @patch.object(bwcli, "create_personal_item")
    def test_dry_run_writes_nothing(self, create, *_):
        r = archived.process_vault("Work", self._entries(), True, None, True)
        self.assertEqual(r["counts"]["archived"], 1)
        create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
