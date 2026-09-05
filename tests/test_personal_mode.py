#!/usr/bin/env python3
"""Unit tests for split.py personal mode.

Verifies:
  - Personal-mode JSON shape: folders array, folderId wired on every item,
    organizationId null, collectionIds null.
  - Org-mode items (non-P vaults) are not emitted in personal output.
  - Only P-type vaults are processed in personal mode.
  - Archived items are skipped by default in personal mode.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

# Regenerate fixture before tests so P vault has realistic content.
_make_fixture = importlib.util.spec_from_file_location(
    "make_fixture", _ROOT / "tests" / "make_fixture.py"
)
_mf_mod = importlib.util.module_from_spec(_make_fixture)
_make_fixture.loader.exec_module(_mf_mod)

import argparse
import split as split_mod


def _make_args(**kwargs) -> argparse.Namespace:
    defaults = {
        "config": "tests/fixture_config.json",
        "account": "test",
        "include_archived": False,
        "personal": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestPersonalModeJSON(unittest.TestCase):
    """Personal-mode output must use folders, not collections."""

    @classmethod
    def setUpClass(cls):
        _mf_mod.build_fixture()

    def setUp(self):
        config = split_mod._load_config(Path("tests/fixture_config.json"))
        self.account = config["accounts"][0]
        self.work_dir = None

    def _run_personal_split(self) -> tuple[Path, list[dict]]:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            (work_dir / "files").mkdir()
            entries = split_mod._process_account_personal(
                self.account, work_dir, include_archived=False
            )
            # Snapshot the files before tmpdir is cleaned up.
            snapshots = {}
            for slug in [e["slug"] for e in entries]:
                bulk = work_dir / f"{slug}.json"
                attach = work_dir / f"{slug}.attachments.json"
                if bulk.exists():
                    snapshots[f"{slug}.json"] = json.loads(bulk.read_text())
                if attach.exists():
                    snapshots[f"{slug}.attachments.json"] = json.loads(attach.read_text())
            return entries, snapshots

    def test_only_p_vaults_in_manifest(self):
        entries, _ = self._run_personal_split()
        for e in entries:
            self.assertEqual(e["vaultType"], "P", f"Non-P vault in personal output: {e}")

    def test_manifest_has_folder_fields(self):
        entries, _ = self._run_personal_split()
        self.assertTrue(entries, "No P-vault entries found")
        for e in entries:
            self.assertIn("folderId", e, "Manifest entry missing folderId")
            self.assertIn("folderName", e, "Manifest entry missing folderName")
            self.assertTrue(e.get("personal"), "Manifest entry missing personal=True")
            self.assertNotIn("collectionId", e, "Personal entry must not have collectionId")

    def test_bulk_doc_has_folders_not_collections(self):
        entries, snapshots = self._run_personal_split()
        self.assertTrue(entries)
        slug = entries[0]["slug"]
        doc = snapshots.get(f"{slug}.json")
        self.assertIsNotNone(doc, f"{slug}.json not found in snapshots")
        self.assertIn("folders", doc, "Personal import doc must have 'folders'")
        self.assertNotIn("collections", doc, "Personal import doc must not have 'collections'")
        self.assertEqual(doc["encrypted"], False)
        self.assertTrue(doc["folders"], "folders must be non-empty")
        folder = doc["folders"][0]
        self.assertIn("id", folder)
        self.assertIn("name", folder)

    def test_items_have_folder_id_and_no_org(self):
        entries, snapshots = self._run_personal_split()
        self.assertTrue(entries)
        slug = entries[0]["slug"]
        doc = snapshots.get(f"{slug}.json")
        self.assertIsNotNone(doc)
        folder_id = doc["folders"][0]["id"]

        for item in doc.get("items", []):
            self.assertEqual(item.get("folderId"), folder_id,
                             f"Item {item.get('name')!r} has wrong folderId")
            self.assertIsNone(item.get("organizationId"),
                              f"Item {item.get('name')!r} has non-null organizationId")
            self.assertIsNone(item.get("collectionIds"),
                              f"Item {item.get('name')!r} has non-null collectionIds")

    def test_archived_items_excluded_by_default(self):
        entries, snapshots = self._run_personal_split()
        self.assertTrue(entries)
        slug = entries[0]["slug"]
        doc = snapshots.get(f"{slug}.json")
        self.assertIsNotNone(doc)
        titles = [i.get("name") for i in doc.get("items", [])]
        self.assertNotIn("Archived Personal Login", titles,
                         "Archived item must be excluded from personal output by default")

    def test_p_vault_items_appear_in_personal_not_org_mode(self):
        """Org mode skips P; personal mode includes P — no overlap."""
        config = split_mod._load_config(Path("tests/fixture_config.json"))
        account = config["accounts"][0]

        with tempfile.TemporaryDirectory() as tmpdir_org:
            work_dir_org = Path(tmpdir_org)
            (work_dir_org / "files").mkdir()
            org_entries = split_mod._process_account(account, work_dir_org, include_archived=False)
            org_vault_types = {e["vaultType"] for e in org_entries}

        self.assertNotIn("P", org_vault_types, "Org mode must skip P vaults")

        with tempfile.TemporaryDirectory() as tmpdir_per:
            work_dir_per = Path(tmpdir_per)
            (work_dir_per / "files").mkdir()
            per_entries = split_mod._process_account_personal(account, work_dir_per, include_archived=False)
            per_vault_types = {e["vaultType"] for e in per_entries}

        self.assertEqual(per_vault_types, {"P"}, "Personal mode must include only P vaults")

    def test_personal_item_counts_exclude_archived(self):
        entries, _ = self._run_personal_split()
        self.assertTrue(entries)
        counts = entries[0]["counts"]
        # Fixture P vault: 3 active items (login, note, card) + 1 archived (skipped)
        self.assertEqual(counts["archivedSkipped"], 1)
        self.assertEqual(counts["total"], 4)  # raw item list includes archived
        self.assertEqual(counts["bulk"] + counts["attachment"], 3)


class TestPersonalAttachmentsDoc(unittest.TestCase):
    """Personal-mode attachments.json must not have org fields."""

    @classmethod
    def setUpClass(cls):
        _mf_mod.build_fixture()

    def test_attachments_doc_has_folder_id_not_org(self):
        config = split_mod._load_config(Path("tests/fixture_config.json"))
        account = config["accounts"][0]

        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            (work_dir / "files").mkdir()
            entries = split_mod._process_account_personal(account, work_dir, include_archived=False)
            for e in entries:
                attach_path = work_dir / f"{e['slug']}.attachments.json"
                if attach_path.exists():
                    doc = json.loads(attach_path.read_text())
                    self.assertIn("folderId", doc, "Attachments doc missing folderId")
                    self.assertNotIn("collectionId", doc, "Attachments doc must not have collectionId")
                    self.assertNotIn("organizationId", doc, "Attachments doc must not have organizationId")
                    for entry in doc.get("items", []):
                        item = entry["item"]
                        self.assertIsNone(item.get("organizationId"))
                        self.assertIsNone(item.get("collectionIds"))
                        self.assertEqual(item.get("folderId"), doc["folderId"])


if __name__ == "__main__":
    unittest.main()
