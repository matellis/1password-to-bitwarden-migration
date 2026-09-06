"""Tests for split.py's op-CLI-based vaultDestination auto-resolution."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import split as split_mod


def _make_vault(name: str, vault_type: str, items: list = None) -> dict:
    return {"attrs": {"type": vault_type, "name": name}, "items": items or []}


class TestOpGuessVaultDestination(unittest.TestCase):

    @patch("split.opacl.vault_members", return_value=None)
    def test_op_unusable_returns_none(self, mock_members):
        self.assertIsNone(split_mod._op_guess_vault_destination("Engineering", "test-acct", "owner@example.com"))

    @patch("split.opacl.vault_members")
    def test_no_members_no_groups_is_owner_only(self, mock_members):
        mock_members.return_value = {"emails": [], "groups": [], "failed_groups": []}
        value, label = split_mod._op_guess_vault_destination("Engineering", "test-acct", "owner@example.com")
        self.assertEqual(value, "owner-only")
        self.assertIn("owner-only", label)

    @patch("split.opacl.vault_members")
    def test_members_excludes_owner_dedupes_sorts(self, mock_members):
        mock_members.return_value = {
            "emails": ["b@example.com", "owner@example.com", "a@example.com", "b@example.com"],
            "groups": [],
            "failed_groups": [],
        }
        value, label = split_mod._op_guess_vault_destination("Engineering", "test-acct", "owner@example.com")
        self.assertEqual(
            value, {"destination": "shared", "shareWith": ["a@example.com", "b@example.com"]}
        )
        self.assertIn("a@example.com, b@example.com", label)
        self.assertIn("via op account owner@example.com", label)

    @patch("split.opacl.vault_members")
    def test_groups_present_but_no_resolved_members_is_blank(self, mock_members):
        mock_members.return_value = {"emails": [], "groups": ["Everyone"], "failed_groups": []}
        value, label = split_mod._op_guess_vault_destination("Engineering", "test-acct", "owner@example.com")
        self.assertEqual(value, "")

    @patch("split.opacl.vault_members")
    def test_failed_group_noted_in_label(self, mock_members):
        mock_members.return_value = {
            "emails": ["a@example.com"],
            "groups": ["Broken"],
            "failed_groups": ["Broken"],
        }
        value, label = split_mod._op_guess_vault_destination("Engineering", "test-acct", "owner@example.com")
        self.assertEqual(value, {"destination": "shared", "shareWith": ["a@example.com"]})
        self.assertIn("Broken", label)

    @patch("split.opacl.vault_members")
    def test_unknown_owner_not_excluded_and_label_says_so(self, mock_members):
        mock_members.return_value = {
            "emails": ["owner@example.com", "a@example.com"],
            "groups": [],
            "failed_groups": [],
        }
        value, label = split_mod._op_guess_vault_destination("Engineering", "test-acct", None)
        self.assertEqual(
            value,
            {"destination": "shared", "shareWith": ["a@example.com", "owner@example.com"]},
        )
        self.assertIn("owner not excluded", label)


class TestProcessAccountOpIntegration(unittest.TestCase):

    @patch("split.opacl.owner_email", return_value=None)
    @patch("split.opacl.vault_members", return_value=None)
    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_op_unavailable_falls_back_to_name_guess(self, mock_parse, mock_vaults, mock_members, mock_owner):
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [_make_vault("Shared Team", "U")]
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "bitwardenOrgId": "org-123",
            "opAccount": "test-acct",
            "vaultRename": {},
            "vaultDestination": {},
        }
        config = {"accounts": [account]}
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir) / "work"
            (work_dir / "files").mkdir(parents=True)
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(json.dumps(config, indent=2) + "\n")
            with self.assertRaises(SystemExit):
                split_mod._process_account(
                    account, work_dir, False, config=config, config_path=config_path
                )
            written = json.loads(config_path.read_text())
        self.assertEqual(written["accounts"][0]["vaultDestination"], {"Shared Team": "shared"})

    @patch("split.opacl.owner_email", return_value="owner@example.com")
    @patch("split.opacl.vault_members")
    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_op_error_per_vault_falls_back_to_name_guess(
        self, mock_parse, mock_vaults, mock_members, mock_owner
    ):
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [_make_vault("Shared Team", "U")]
        mock_members.return_value = None
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "bitwardenOrgId": "org-123",
            "opAccount": "test-acct",
            "vaultRename": {},
            "vaultDestination": {},
        }
        config = {"accounts": [account]}
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir) / "work"
            (work_dir / "files").mkdir(parents=True)
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(json.dumps(config, indent=2) + "\n")
            with self.assertRaises(SystemExit):
                split_mod._process_account(
                    account, work_dir, False, config=config, config_path=config_path
                )
            written = json.loads(config_path.read_text())
        self.assertEqual(written["accounts"][0]["vaultDestination"], {"Shared Team": "shared"})

    @patch("split.opacl.owner_email", return_value="owner@example.com")
    @patch("split.opacl.vault_members")
    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_unshared_vault_writes_owner_only(
        self, mock_parse, mock_vaults, mock_members, mock_owner
    ):
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [_make_vault("Finances", "U")]
        mock_members.return_value = {"emails": [], "groups": [], "failed_groups": []}
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "bitwardenOrgId": "org-123",
            "opAccount": "test-acct",
            "vaultRename": {},
            "vaultDestination": {},
        }
        config = {"accounts": [account]}
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir) / "work"
            (work_dir / "files").mkdir(parents=True)
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(json.dumps(config, indent=2) + "\n")
            with self.assertRaises(SystemExit):
                split_mod._process_account(
                    account, work_dir, False, config=config, config_path=config_path
                )
            written = json.loads(config_path.read_text())
        self.assertEqual(written["accounts"][0]["vaultDestination"], {"Finances": "owner-only"})

    @patch("split.opacl.owner_email", return_value="owner@example.com")
    @patch("split.opacl.vault_members")
    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_shared_vault_writes_object_form(
        self, mock_parse, mock_vaults, mock_members, mock_owner
    ):
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [_make_vault("Kids Accounts", "U")]
        mock_members.return_value = {
            "emails": ["mom@example.com", "kid@example.com", "owner@example.com"],
            "groups": [],
            "failed_groups": [],
        }
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "bitwardenOrgId": "org-123",
            "opAccount": "test-acct",
            "vaultRename": {},
            "vaultDestination": {},
        }
        config = {"accounts": [account]}
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir) / "work"
            (work_dir / "files").mkdir(parents=True)
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(json.dumps(config, indent=2) + "\n")
            with self.assertRaises(SystemExit):
                split_mod._process_account(
                    account, work_dir, False, config=config, config_path=config_path
                )
            written = json.loads(config_path.read_text())
        self.assertEqual(
            written["accounts"][0]["vaultDestination"],
            {"Kids Accounts": {"destination": "shared", "shareWith": ["kid@example.com", "mom@example.com"]}},
        )

    @patch("split.opacl.owner_email", return_value="owner@example.com")
    @patch("split.opacl.vault_members")
    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_existing_entry_never_overwritten_even_with_op_available(
        self, mock_parse, mock_vaults, mock_members, mock_owner
    ):
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [_make_vault("Shared Team", "U")]
        mock_members.return_value = {
            "emails": ["someone@example.com"],
            "groups": [],
            "failed_groups": [],
        }
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "bitwardenOrgId": "org-123",
            "opAccount": "test-acct",
            "vaultRename": {},
            "vaultDestination": {"Shared Team": "owner-only"},
        }
        config = {"accounts": [account]}
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir) / "work"
            (work_dir / "files").mkdir(parents=True)
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(json.dumps(config, indent=2) + "\n")
            entries = split_mod._process_account(
                account, work_dir, False, config=config, config_path=config_path
            )
            written = json.loads(config_path.read_text())
        self.assertEqual(written["accounts"][0]["vaultDestination"], {"Shared Team": "owner-only"})
        mock_members.assert_not_called()
        self.assertTrue(entries)


class TestProcessAccountPersonalOpIntegration(unittest.TestCase):

    @patch("split.opacl.owner_email", return_value="owner@example.com")
    @patch("split.opacl.vault_members")
    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_personal_mode_unclassified_gets_op_enrichment(
        self, mock_parse, mock_vaults, mock_members, mock_owner
    ):
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [_make_vault("Family Docs", "U")]
        mock_members.return_value = {
            "emails": ["mom@example.com"],
            "groups": [],
            "failed_groups": [],
        }
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "opAccount": "test-acct",
            "vaultRename": {},
            "vaultDestination": {},
        }
        config = {"accounts": [account]}
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir) / "work"
            (work_dir / "files").mkdir(parents=True)
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(json.dumps(config, indent=2) + "\n")
            stderr_buf = io.StringIO()
            with redirect_stderr(stderr_buf):
                split_mod._process_account_personal(
                    account, work_dir, False, config=config, config_path=config_path
                )
            written = json.loads(config_path.read_text())
        self.assertEqual(
            written["accounts"][0]["vaultDestination"],
            {"Family Docs": {"destination": "shared", "shareWith": ["mom@example.com"]}},
        )
        self.assertIn("mom@example.com", stderr_buf.getvalue())

    @patch("split.opacl.vault_members", return_value=None)
    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_personal_mode_existing_entry_never_overwritten(
        self, mock_parse, mock_vaults, mock_members
    ):
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [_make_vault("Work", "U")]
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "opAccount": "test-acct",
            "vaultRename": {},
            "vaultDestination": {"Work": "personal"},
        }
        config = {"accounts": [account]}
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir) / "work"
            (work_dir / "files").mkdir(parents=True)
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(json.dumps(config, indent=2) + "\n")
            stderr_buf = io.StringIO()
            with redirect_stderr(stderr_buf):
                split_mod._process_account_personal(
                    account, work_dir, False, config=config, config_path=config_path
                )
            written = json.loads(config_path.read_text())
        self.assertEqual(written["accounts"][0]["vaultDestination"], {"Work": "personal"})
        mock_members.assert_not_called()


if __name__ == "__main__":
    unittest.main()
