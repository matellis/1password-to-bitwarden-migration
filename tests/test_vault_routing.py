"""Tests for vaultDestination routing and bitwardenEmail guards."""
import argparse
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import split as split_mod


def _make_vault(name: str, vault_type: str, items: list = None) -> dict:
    return {"attrs": {"type": vault_type, "name": name}, "items": items or []}


class TestCheckVaultDestinations(unittest.TestCase):
    """Unit tests for the pure destination-check helpers."""

    def test_p_vaults_always_ok_without_destination(self):
        vaults = [_make_vault("Private", "P")]
        result = split_mod._check_vault_destinations(vaults, {}, {"P"}, {})
        self.assertEqual(result, [])

    def test_unclassified_non_p_vault_returned(self):
        vaults = [_make_vault("Work", "U")]
        result = split_mod._check_vault_destinations(vaults, {}, {"P"}, {})
        self.assertEqual(result, ["Work"])

    def test_classified_vault_not_returned(self):
        vaults = [_make_vault("Work", "U")]
        result = split_mod._check_vault_destinations(vaults, {}, {"P"}, {"Work": "shared"})
        self.assertEqual(result, [])

    def test_skipped_vault_types_not_checked(self):
        vaults = [_make_vault("Employee", "E")]
        result = split_mod._check_vault_destinations(vaults, {}, {"P", "E"}, {})
        self.assertEqual(result, [])

    def test_original_vault_name_used_for_destination_check(self):
        """vaultDestination keys are original vault names; rename is for output only."""
        vaults = [_make_vault("Old Name", "U")]
        result = split_mod._check_vault_destinations(
            vaults, {"Old Name": "New Name"}, {"P"}, {"Old Name": "shared"}
        )
        self.assertEqual(result, [])

    def test_renamed_vault_name_not_used_for_destination_check(self):
        """A vault classified under its renamed name is still unclassified."""
        vaults = [_make_vault("Old Name", "U")]
        result = split_mod._check_vault_destinations(
            vaults, {"Old Name": "New Name"}, {"P"}, {"New Name": "shared"}
        )
        self.assertEqual(result, ["Old Name"])

    def test_multiple_unclassified_all_returned(self):
        vaults = [_make_vault("Alpha", "U"), _make_vault("Beta", "U")]
        result = split_mod._check_vault_destinations(vaults, {}, {"P"}, {})
        self.assertIn("Alpha", result)
        self.assertIn("Beta", result)

    def test_owner_only_destination_classified(self):
        vaults = [_make_vault("Private Work", "U")]
        result = split_mod._check_vault_destinations(
            vaults, {}, {"P"}, {"Private Work": "owner-only"}
        )
        self.assertEqual(result, [])

    def test_personal_destination_classified(self):
        vaults = [_make_vault("Solo", "U")]
        result = split_mod._check_vault_destinations(
            vaults, {}, {"P"}, {"Solo": "personal"}
        )
        self.assertEqual(result, [])


class TestCheckVaultDestinationsPersonal(unittest.TestCase):
    """Unit tests for personal-mode destination checks."""

    def test_p_vaults_always_ok(self):
        vaults = [_make_vault("Private", "P")]
        result = split_mod._check_vault_destinations_personal(vaults, {}, {})
        self.assertEqual(result, [])

    def test_non_p_without_destination_returned(self):
        vaults = [_make_vault("Work", "U")]
        result = split_mod._check_vault_destinations_personal(vaults, {}, {})
        self.assertEqual(result, ["Work"])

    def test_non_p_with_any_destination_ok(self):
        vaults = [_make_vault("Work", "U")]
        for dest in ("personal", "shared", "owner-only"):
            result = split_mod._check_vault_destinations_personal(
                vaults, {}, {"Work": dest}
            )
            self.assertEqual(result, [], f"Failed for destination={dest!r}")


class TestProcessAccountRefusesUnclassified(unittest.TestCase):
    """_process_account() must sys.exit when vaults lack destination."""

    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_unclassified_vault_causes_sysexit(self, mock_parse, mock_vaults):
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [_make_vault("Shared Work", "U")]
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "bitwardenOrgId": "org-123",
            "vaultRename": {},
            "vaultDestination": {},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            (work_dir / "files").mkdir()
            with self.assertRaises(SystemExit) as ctx:
                split_mod._process_account(account, work_dir, False)
        self.assertIn("Shared Work", str(ctx.exception))

    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_error_message_lists_all_vault_names(self, mock_parse, mock_vaults):
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [
            _make_vault("Alpha", "U"),
            _make_vault("Beta", "U"),
        ]
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "bitwardenOrgId": "org-123",
            "vaultRename": {},
            "vaultDestination": {},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            (work_dir / "files").mkdir()
            with self.assertRaises(SystemExit) as ctx:
                split_mod._process_account(account, work_dir, False)
        msg = str(ctx.exception)
        self.assertIn("Alpha", msg)
        self.assertIn("Beta", msg)


class TestProcessAccountVaultRouting(unittest.TestCase):
    """Manifest entries reflect destination; personal-destination vaults are skipped in org mode."""

    def _make_result(self, bulk=None, attach=None):
        r = MagicMock()
        r.bulk_items = bulk or []
        r.attachment_items = attach or []
        r.archived_count = 0
        r.dupe_count = 0
        return r

    @patch("split.onepux.make_import_doc", return_value={})
    @patch("split.onepux.vault_slug", return_value="shared-slug")
    @patch("split.onepux.make_collection", return_value=("coll-id", {"id": "coll-id", "name": "Shared"}))
    @patch("split.onepux.convert_vault_items")
    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_shared_destination_in_manifest(
        self, mock_parse, mock_vaults, mock_convert, mock_coll, mock_slug, mock_doc
    ):
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [_make_vault("Shared", "U")]
        mock_convert.return_value = self._make_result()
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "bitwardenOrgId": "org-123",
            "vaultRename": {},
            "vaultDestination": {"Shared": "shared"},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            (work_dir / "files").mkdir()
            entries = split_mod._process_account(account, work_dir, False)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["destination"], "shared")
        self.assertEqual(entries[0]["vaultName"], "Shared")

    @patch("split.onepux.make_import_doc", return_value={})
    @patch("split.onepux.vault_slug", return_value="oo-slug")
    @patch("split.onepux.make_collection", return_value=("coll-id", {"id": "coll-id", "name": "Private Work"}))
    @patch("split.onepux.convert_vault_items")
    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_owner_only_destination_in_manifest(
        self, mock_parse, mock_vaults, mock_convert, mock_coll, mock_slug, mock_doc
    ):
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [_make_vault("Private Work", "U")]
        mock_convert.return_value = self._make_result()
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "bitwardenOrgId": "org-123",
            "vaultRename": {},
            "vaultDestination": {"Private Work": "owner-only"},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            (work_dir / "files").mkdir()
            entries = split_mod._process_account(account, work_dir, False)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["destination"], "owner-only")

    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_personal_destination_skipped_in_org_mode(self, mock_parse, mock_vaults):
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [_make_vault("Solo", "U")]
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "bitwardenOrgId": "org-123",
            "vaultRename": {},
            "vaultDestination": {"Solo": "personal"},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            (work_dir / "files").mkdir()
            entries = split_mod._process_account(account, work_dir, False)
        self.assertEqual(entries, [])


class TestPersonalModeUnclassifiedWarning(unittest.TestCase):
    """Personal mode: unclassified non-P vaults warn but do not refuse."""

    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_personal_mode_warns_not_refuses_on_unclassified_non_p(self, mock_parse, mock_vaults):
        import io
        from contextlib import redirect_stderr
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [
            _make_vault("Private", "P"),
            _make_vault("Shared", "U"),
        ]
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "vaultRename": {},
            "vaultDestination": {},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            (work_dir / "files").mkdir()
            stderr_buf = io.StringIO()
            with redirect_stderr(stderr_buf):
                entries = split_mod._process_account_personal(account, work_dir, False)
        self.assertIn("Shared", stderr_buf.getvalue())

    @patch("split.onepux.vault_slug", return_value="shared-slug")
    @patch("split.onepux.convert_vault_items")
    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_personal_mode_imports_u_vault_classified_personal(
        self, mock_parse, mock_vaults, mock_convert, mock_slug
    ):
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [_make_vault("Shared", "U")]
        r = MagicMock()
        r.bulk_items = []
        r.attachment_items = []
        r.archived_count = 0
        r.dupe_count = 0
        mock_convert.return_value = r
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "vaultRename": {},
            "vaultDestination": {"Shared": "personal"},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            (work_dir / "files").mkdir()
            entries = split_mod._process_account_personal(account, work_dir, False)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["vaultName"], "Shared")
        self.assertEqual(entries[0]["destination"], "personal")

    def test_personal_original_name_used_for_destination_lookup(self):
        """Vault renamed from 'Shared' to 'All-Staff' is classified by original name."""
        vaults = [_make_vault("Shared", "U")]
        result = split_mod._check_vault_destinations_personal(
            vaults, {"Shared": "All-Staff"}, {"Shared": "personal"}
        )
        self.assertEqual(result, [])


class TestNormalizeDestination(unittest.TestCase):
    """Unit tests for the vaultDestination string/object normalizer."""

    def test_valid_string_forms(self):
        for dest in ("shared", "owner-only", "personal"):
            self.assertEqual(split_mod._normalize_destination(dest), (dest, []))

    def test_invalid_string_is_none(self):
        self.assertEqual(split_mod._normalize_destination("sharedd"), (None, []))
        self.assertEqual(split_mod._normalize_destination(""), (None, []))

    def test_absent_value_is_none(self):
        self.assertEqual(split_mod._normalize_destination(None), (None, []))

    def test_valid_object_form(self):
        value = {"destination": "shared", "shareWith": ["kid@example.com", "mom@example.com"]}
        self.assertEqual(
            split_mod._normalize_destination(value),
            ("shared", ["kid@example.com", "mom@example.com"]),
        )

    def test_object_form_without_share_with(self):
        self.assertEqual(
            split_mod._normalize_destination({"destination": "shared"}),
            ("shared", []),
        )

    def test_object_missing_destination_is_invalid(self):
        self.assertEqual(
            split_mod._normalize_destination({"shareWith": ["a@example.com"]}),
            (None, []),
        )

    def test_object_bad_destination_is_invalid(self):
        self.assertEqual(
            split_mod._normalize_destination({"destination": "nope"}),
            (None, []),
        )

    def test_share_with_on_owner_only_is_invalid(self):
        self.assertEqual(
            split_mod._normalize_destination(
                {"destination": "owner-only", "shareWith": ["a@example.com"]}
            ),
            (None, []),
        )

    def test_share_with_on_personal_is_invalid(self):
        self.assertEqual(
            split_mod._normalize_destination(
                {"destination": "personal", "shareWith": ["a@example.com"]}
            ),
            (None, []),
        )

    def test_share_with_empty_list_is_invalid(self):
        self.assertEqual(
            split_mod._normalize_destination({"destination": "shared", "shareWith": []}),
            (None, []),
        )

    def test_share_with_not_a_list_is_invalid(self):
        self.assertEqual(
            split_mod._normalize_destination({"destination": "shared", "shareWith": "a@example.com"}),
            (None, []),
        )

    def test_share_with_non_string_entries_invalid(self):
        self.assertEqual(
            split_mod._normalize_destination({"destination": "shared", "shareWith": [1, 2]}),
            (None, []),
        )

    def test_object_unknown_key_is_invalid(self):
        self.assertEqual(
            split_mod._normalize_destination({"destination": "shared", "bogus": True}),
            (None, []),
        )

    def test_other_types_are_invalid(self):
        self.assertEqual(split_mod._normalize_destination(123), (None, []))
        self.assertEqual(split_mod._normalize_destination(["shared"]), (None, []))


class TestCheckVaultDestinationsObjectForm(unittest.TestCase):
    """Object-form vaultDestination values participate in the same classification checks."""

    def test_valid_object_form_passes_classification(self):
        vaults = [_make_vault("Kids Accounts", "U")]
        result = split_mod._check_vault_destinations(
            vaults, {}, {"P"},
            {"Kids Accounts": {"destination": "shared", "shareWith": ["kid@example.com"]}},
        )
        self.assertEqual(result, [])

    def test_malformed_object_flagged_unclassified(self):
        vaults = [_make_vault("Kids Accounts", "U")]
        result = split_mod._check_vault_destinations(
            vaults, {}, {"P"}, {"Kids Accounts": {"destination": "bogus"}}
        )
        self.assertEqual(result, ["Kids Accounts"])

    def test_share_with_on_non_shared_flagged_unclassified(self):
        vaults = [_make_vault("Finances", "U")]
        result = split_mod._check_vault_destinations(
            vaults, {}, {"P"},
            {"Finances": {"destination": "owner-only", "shareWith": ["a@example.com"]}},
        )
        self.assertEqual(result, ["Finances"])


class TestMalformedObjectNeverOverwritten(unittest.TestCase):
    """_process_account must never clobber a present-but-malformed vaultDestination entry."""

    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_malformed_object_flagged_and_preserved(self, mock_parse, mock_vaults):
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [_make_vault("Kids Accounts", "U")]
        malformed = {"destination": "shared", "shareWith": []}
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "bitwardenOrgId": "org-123",
            "vaultRename": {},
            "vaultDestination": {"Kids Accounts": dict(malformed)},
        }
        config = {"accounts": [account]}
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir) / "work"
            (work_dir / "files").mkdir(parents=True)
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(json.dumps(config, indent=2) + "\n")
            with self.assertRaises(SystemExit) as ctx:
                split_mod._process_account(
                    account, work_dir, False, config=config, config_path=config_path
                )
            written = json.loads(config_path.read_text())
        self.assertEqual(written["accounts"][0]["vaultDestination"]["Kids Accounts"], malformed)
        self.assertIn("invalid entry", str(ctx.exception))


class TestShareWithInManifest(unittest.TestCase):
    """Manifest entries carry shareWith only when the object form supplies a non-empty list."""

    def _make_result(self):
        r = MagicMock()
        r.bulk_items = []
        r.attachment_items = []
        r.archived_count = 0
        r.dupe_count = 0
        return r

    @patch("split.onepux.make_import_doc", return_value={})
    @patch("split.onepux.vault_slug", return_value="kids-slug")
    @patch("split.onepux.make_collection", return_value=("coll-id", {"id": "coll-id", "name": "Kids Accounts"}))
    @patch("split.onepux.convert_vault_items")
    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_manifest_carries_share_with_when_present(
        self, mock_parse, mock_vaults, mock_convert, mock_coll, mock_slug, mock_doc
    ):
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [_make_vault("Kids Accounts", "U")]
        mock_convert.return_value = self._make_result()
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "bitwardenOrgId": "org-123",
            "vaultRename": {},
            "vaultDestination": {
                "Kids Accounts": {
                    "destination": "shared",
                    "shareWith": ["kid@example.com", "mom@example.com"],
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            (work_dir / "files").mkdir()
            entries = split_mod._process_account(account, work_dir, False)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["destination"], "shared")
        self.assertEqual(entries[0]["shareWith"], ["kid@example.com", "mom@example.com"])

    @patch("split.onepux.make_import_doc", return_value={})
    @patch("split.onepux.vault_slug", return_value="shared-slug")
    @patch("split.onepux.make_collection", return_value=("coll-id", {"id": "coll-id", "name": "Shared"}))
    @patch("split.onepux.convert_vault_items")
    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_manifest_omits_share_with_key_when_plain_string(
        self, mock_parse, mock_vaults, mock_convert, mock_coll, mock_slug, mock_doc
    ):
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [_make_vault("Shared", "U")]
        mock_convert.return_value = self._make_result()
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "bitwardenOrgId": "org-123",
            "vaultRename": {},
            "vaultDestination": {"Shared": "shared"},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            (work_dir / "files").mkdir()
            entries = split_mod._process_account(account, work_dir, False)
        self.assertEqual(len(entries), 1)
        self.assertNotIn("shareWith", entries[0])

    @patch("split.onepux.make_import_doc", return_value={})
    @patch("split.onepux.vault_slug", return_value="shared-slug")
    @patch("split.onepux.make_collection", return_value=("coll-id", {"id": "coll-id", "name": "Shared"}))
    @patch("split.onepux.convert_vault_items")
    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_manifest_omits_share_with_key_when_object_without_it(
        self, mock_parse, mock_vaults, mock_convert, mock_coll, mock_slug, mock_doc
    ):
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [_make_vault("Shared", "U")]
        mock_convert.return_value = self._make_result()
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "bitwardenOrgId": "org-123",
            "vaultRename": {},
            "vaultDestination": {"Shared": {"destination": "shared"}},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            (work_dir / "files").mkdir()
            entries = split_mod._process_account(account, work_dir, False)
        self.assertEqual(len(entries), 1)
        self.assertNotIn("shareWith", entries[0])


class TestBitwardenEmailGuard(unittest.TestCase):
    """The bitwardenEmail guard logic used by import.py and verify.py."""

    def _check_email(self, account: dict) -> None:
        name = account.get("name", "?")
        personal = account.get("mode") == "personal"
        email = account.get("bitwardenEmail")
        if personal and not email:
            sys.exit(
                f"Account '{name}': bitwardenEmail is required for mode=personal entries.\n"
                f"Add \"bitwardenEmail\": \"you@example.com\" to this account in config.json."
            )

    def test_personal_without_email_exits(self):
        account = {"name": "me-test", "mode": "personal"}
        with self.assertRaises(SystemExit) as ctx:
            self._check_email(account)
        msg = str(ctx.exception)
        self.assertIn("bitwardenEmail", msg)
        self.assertIn("me-test", msg)

    def test_personal_with_email_passes(self):
        account = {"name": "me-test", "mode": "personal", "bitwardenEmail": "me@example.com"}
        self._check_email(account)

    def test_org_without_email_passes(self):
        account = {"name": "my-family", "bitwardenOrgId": "org-123"}
        self._check_email(account)

    def test_org_with_email_passes(self):
        account = {"name": "my-family", "bitwardenOrgId": "org-123", "bitwardenEmail": "admin@example.com"}
        self._check_email(account)


class TestImportManifestDestinationCheck(unittest.TestCase):
    """Manifest entries without 'destination' must trigger refusal in import.py."""

    def _check_manifest(self, manifest: list, account_name: str) -> None:
        unclassified = [
            v["vaultName"] for v in manifest
            if not v.get("personal") and "destination" not in v
        ]
        if unclassified:
            names = ", ".join(f'"{n}"' for n in unclassified)
            sys.exit(
                f"Account '{account_name}': manifest contains vaults with no destination: {names}\n"
                f"Re-run split.py — it will require vaultDestination entries for these vaults."
            )

    def test_manifest_without_destination_exits(self):
        manifest = [{"vaultName": "Work", "vaultType": "U", "slug": "work"}]
        with self.assertRaises(SystemExit) as ctx:
            self._check_manifest(manifest, "test")
        msg = str(ctx.exception)
        self.assertIn("Work", msg)
        self.assertIn("Re-run split.py", msg)

    def test_manifest_with_destination_passes(self):
        manifest = [{"vaultName": "Work", "vaultType": "U", "slug": "work", "destination": "shared"}]
        self._check_manifest(manifest, "test")

    def test_personal_entries_not_checked(self):
        manifest = [{"vaultName": "Private", "vaultType": "P", "slug": "private", "personal": True}]
        self._check_manifest(manifest, "test")

    def test_multiple_unclassified_all_listed(self):
        manifest = [
            {"vaultName": "Alpha", "vaultType": "U", "slug": "alpha"},
            {"vaultName": "Beta", "vaultType": "U", "slug": "beta"},
        ]
        with self.assertRaises(SystemExit) as ctx:
            self._check_manifest(manifest, "test")
        msg = str(ctx.exception)
        self.assertIn("Alpha", msg)
        self.assertIn("Beta", msg)


if __name__ == "__main__":
    unittest.main()
