#!/usr/bin/env python3
"""Regression tests for bug fixes in import.py and verify.py.

Bug 1: attachment items must use the server-assigned collection ID, not the
       placeholder uuid written by split.py.
Bug 2: expected attachment count for source items from attachments.json must
       equal len(entry["files"]), not zero.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Load import.py as a module (can't use `import import`).
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

_spec = importlib.util.spec_from_file_location("import_mod", _ROOT / "import.py")
_import_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_import_mod)

from verify import _load_source_items, _compare_item


class TestCollectionIdResolution(unittest.TestCase):
    """Regression for bug 1: resolved collection ID must reach create_item."""

    def _make_vault_files(self, work_dir: Path, placeholder_id: str, org_id: str, vault_name: str) -> None:
        bulk_item = {
            "type": 1, "name": "Work SSO Portal",
            "login": {"username": "alice@example.com", "password": "s3cr3t", "totp": None, "uris": []},
        }
        bulk_doc = {
            "encrypted": False,
            "collections": [{"id": placeholder_id, "name": vault_name}],
            "items": [bulk_item],
        }
        (work_dir / "Employee.json").write_text(json.dumps(bulk_doc))

        attach_item = {
            "type": 1, "name": "Service With Key File",
            "login": {"username": "bob@example.com", "password": "p4ss", "totp": None, "uris": []},
        }
        dummy_file = work_dir / "keyfile.pem"
        dummy_file.write_text("-----BEGIN RSA KEY-----\ntest\n-----END RSA KEY-----\n")
        attach_doc = {
            "organizationId": org_id,
            "items": [{"item": attach_item, "files": [str(dummy_file)]}],
        }
        (work_dir / "Employee.attachments.json").write_text(json.dumps(attach_doc))

    def test_attachment_items_use_resolved_not_placeholder_id(self):
        """create_item must receive the server-resolved collection ID, not the split.py placeholder."""
        placeholder_id = "placeholder-0000-0000-0000-000000000000"
        real_id = "real-server-0000-0000-0000-000000000001"
        org_id = "org-00000000-0000-0000-0000-000000000001"
        vault_name = "Employee"

        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            self._make_vault_files(work_dir, placeholder_id, org_id, vault_name)

            account = {"bitwardenOrgId": org_id, "onExisting": "refuse", "name": "test"}
            vault_entry = {
                "vaultName": vault_name,
                "slug": "Employee",
                "collectionId": placeholder_id,
                "counts": {"bulk": 1, "attachment": 1},
            }
            ledger: dict = {"imported": {}, "failures": {}}

            with patch("lib.bwcli.list_org_collections") as mock_list_cols, \
                 patch("lib.bwcli.bulk_import"), \
                 patch("lib.bwcli.sync"), \
                 patch("lib.bwcli.create_item") as mock_create_item, \
                 patch("lib.bwcli.create_attachment"):

                # First call: initial onExisting check — no existing collection.
                # Second call: inside _resolve_real_collection_id — returns real ID.
                mock_list_cols.side_effect = [
                    [],
                    [{"id": real_id, "name": vault_name}],
                ]
                mock_create_item.return_value = {"id": "new-item-id-abc"}

                result = _import_mod._import_vault(
                    account, vault_entry, work_dir, ledger, yes=True, force=False
                )

                self.assertNotEqual(result.get("status"), "failed", f"import failed: {ledger.get('failures')}")

                mock_create_item.assert_called_once()
                item_arg = mock_create_item.call_args[0][0]
                self.assertEqual(
                    item_arg["collectionIds"], [real_id],
                    f"create_item must use real server id, got {item_arg['collectionIds']!r}",
                )
                self.assertNotIn(
                    placeholder_id, item_arg["collectionIds"],
                    "placeholder id must not reach create_item",
                )

    def test_ledger_stores_resolved_collection_id(self):
        """Ledger entry collectionId must be the server-assigned id, not the placeholder."""
        placeholder_id = "placeholder-0000-0000-0000-000000000000"
        real_id = "real-server-0000-0000-0000-000000000002"
        org_id = "org-00000000-0000-0000-0000-000000000002"
        vault_name = "Employee"

        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            # No attachments for this test — just bulk.
            bulk_item = {"type": 2, "name": "Secure Note", "login": {}}
            bulk_doc = {
                "encrypted": False,
                "collections": [{"id": placeholder_id, "name": vault_name}],
                "items": [bulk_item],
            }
            (work_dir / "Employee.json").write_text(json.dumps(bulk_doc))

            account = {"bitwardenOrgId": org_id, "onExisting": "refuse", "name": "test"}
            vault_entry = {
                "vaultName": vault_name,
                "slug": "Employee",
                "collectionId": placeholder_id,
                "counts": {"bulk": 1, "attachment": 0},
            }
            ledger: dict = {"imported": {}, "failures": {}}

            with patch("lib.bwcli.list_org_collections") as mock_list_cols, \
                 patch("lib.bwcli.bulk_import"), \
                 patch("lib.bwcli.sync"):

                mock_list_cols.side_effect = [
                    [],
                    [{"id": real_id, "name": vault_name}],
                ]

                _import_mod._import_vault(
                    account, vault_entry, work_dir, ledger, yes=True, force=False
                )

                stored_id = ledger["imported"][vault_name]["collectionId"]
                self.assertEqual(stored_id, real_id)
                self.assertNotEqual(stored_id, placeholder_id)

    def test_fails_loudly_when_collection_not_found_after_sync(self):
        """If the collection cannot be resolved after import, status must be 'failed'."""
        placeholder_id = "placeholder-0000-0000-0000-000000000000"
        org_id = "org-00000000-0000-0000-0000-000000000003"
        vault_name = "Employee"

        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            bulk_doc = {
                "encrypted": False,
                "collections": [{"id": placeholder_id, "name": vault_name}],
                "items": [{
                    "type": 1, "name": "Work SSO Portal",
                    "login": {"username": "alice@example.com", "password": "s3cr3t", "totp": None, "uris": []},
                }],
            }
            (work_dir / "Employee.json").write_text(json.dumps(bulk_doc))

            account = {"bitwardenOrgId": org_id, "onExisting": "refuse", "name": "test"}
            vault_entry = {
                "vaultName": vault_name,
                "slug": "Employee",
                "collectionId": placeholder_id,
                "counts": {"bulk": 0, "attachment": 0},
            }
            ledger: dict = {"imported": {}, "failures": {}}

            with patch("lib.bwcli.list_org_collections") as mock_list_cols, \
                 patch("lib.bwcli.bulk_import"), \
                 patch("lib.bwcli.sync"):

                mock_list_cols.side_effect = [
                    [],   # initial check
                    [],   # after sync — still empty
                ]

                result = _import_mod._import_vault(
                    account, vault_entry, work_dir, ledger, yes=True, force=False
                )

                self.assertEqual(result["status"], "failed")
                self.assertIn(vault_name, ledger["failures"])


class TestAttachmentCountVerification(unittest.TestCase):
    """Regression for bug 2: expected attachment count from attachments.json entries."""

    def test_load_source_items_bulk_expect_zero_attachments(self):
        """Bulk items carry no attachments; expected count must be 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bulk_path = Path(tmpdir) / "vault.json"
            attach_path = Path(tmpdir) / "vault.attachments.json"

            bulk_item = {"type": 1, "name": "Work SSO Portal", "login": {}}
            bulk_path.write_text(json.dumps({"items": [bulk_item]}))

            items = _load_source_items(bulk_path, attach_path)
            self.assertEqual(len(items), 1)
            _, count = items[0]
            self.assertEqual(count, 0)

    def test_load_source_items_attach_expect_file_count(self):
        """Attachment items must have expected count = len(entry['files'])."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bulk_path = Path(tmpdir) / "vault.json"
            attach_path = Path(tmpdir) / "vault.attachments.json"

            attach_item = {"type": 1, "name": "Service With Key File", "login": {}}
            attach_doc = {
                "items": [{"item": attach_item, "files": ["key.pem", "cert.pem"]}]
            }
            attach_path.write_text(json.dumps(attach_doc))

            items = _load_source_items(bulk_path, attach_path)
            self.assertEqual(len(items), 1)
            _, count = items[0]
            self.assertEqual(count, 2, "expected count must equal number of files")

    def test_compare_item_no_false_failure_when_counts_match(self):
        """An attachment item with 1 file and 1 live attachment must not produce diffs."""
        src_item = {
            "type": 1, "name": "Service With Key File",
            "login": {"username": "bob@example.com", "password": "p4ss", "totp": None, "uris": []},
            "notes": None, "fields": [],
        }
        live_item = {
            "type": 1, "name": "Service With Key File",
            "login": {"username": "bob@example.com", "password": "p4ss", "totp": None, "uris": []},
            "notes": None, "fields": [],
            "attachments": [{"id": "att-1", "fileName": "key.pem", "size": "123"}],
        }

        diffs = _compare_item(src_item, live_item, expected_attach_count=1)
        self.assertEqual(diffs, [], f"False failure; got diffs: {diffs}")

    def test_compare_item_false_failure_without_fix(self):
        """Demonstrate the old bug: passing expected_attach_count=0 falsely reports mismatch."""
        src_item = {
            "type": 1, "name": "Service With Key File",
            "login": {"username": None, "password": None, "totp": None, "uris": []},
            "notes": None, "fields": [],
        }
        live_item = {
            "type": 1, "name": "Service With Key File",
            "login": {"username": None, "password": None, "totp": None, "uris": []},
            "notes": None, "fields": [],
            "attachments": [{"id": "att-1", "fileName": "key.pem"}],
        }

        # Old behavior: source.get("attachments") == [] → expected 0 → mismatch with live 1.
        diffs_old = _compare_item(src_item, live_item, expected_attach_count=0)
        self.assertTrue(any("attachment count" in d for d in diffs_old), "expected a false-failure diff")

        # Fixed behavior: pass the real expected count → no diff.
        diffs_fixed = _compare_item(src_item, live_item, expected_attach_count=1)
        self.assertFalse(any("attachment count" in d for d in diffs_fixed))


class TestEmptyVaultImport(unittest.TestCase):
    """A vault whose bulk file has zero items must not call bw import (which
    fails with 'Nothing was imported'); it is marked done in the ledger."""

    def test_empty_bulk_and_no_attachments_marked_done(self):
        vault_name = "Managed Keys"
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            (work_dir / "managed-keys.json").write_text(json.dumps({
                "encrypted": False, "collections": [], "items": [],
            }))

            account = {"bitwardenOrgId": "org-1", "onExisting": "refuse", "name": "test"}
            vault_entry = {
                "vaultName": vault_name,
                "slug": "managed-keys",
                "collectionId": "placeholder",
                "counts": {"bulk": 0, "attachment": 0},
            }
            ledger: dict = {"imported": {}, "failures": {}}

            with patch("lib.bwcli.list_org_collections", return_value=[]), \
                 patch("lib.bwcli.bulk_import") as mock_bulk:
                result = _import_mod._import_vault(
                    account, vault_entry, work_dir, ledger, yes=True, force=False
                )

            self.assertEqual(result.get("status"), "empty")
            mock_bulk.assert_not_called()
            entry = ledger["imported"][vault_name]
            self.assertEqual(entry["importedCount"], 0)
            self.assertIsNone(entry["collectionId"])

    def test_empty_bulk_with_attachments_still_imports(self):
        vault_name = "Docs"
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            (work_dir / "docs.json").write_text(json.dumps({
                "encrypted": False, "collections": [], "items": [],
            }))
            (work_dir / "docs.attachments.json").write_text(json.dumps({
                "organizationId": "org-1",
                "items": [],
            }))

            account = {"bitwardenOrgId": "org-1", "onExisting": "refuse", "name": "test"}
            vault_entry = {
                "vaultName": vault_name,
                "slug": "docs",
                "collectionId": "placeholder",
                "counts": {"bulk": 0, "attachment": 0},
            }
            ledger: dict = {"imported": {}, "failures": {}}

            with patch("lib.bwcli.list_org_collections", return_value=[]), \
                 patch("lib.bwcli.bulk_import") as mock_bulk:
                result = _import_mod._import_vault(
                    account, vault_entry, work_dir, ledger, yes=True, force=False
                )

            self.assertEqual(result.get("status"), "empty")
            mock_bulk.assert_not_called()


if __name__ == "__main__":
    unittest.main()
