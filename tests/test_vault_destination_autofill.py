"""Tests for auto-filling vaultDestination guesses into config.json."""
import json
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


class TestGuessVaultDestination(unittest.TestCase):

    def test_shared_substring_guesses_shared(self):
        self.assertEqual(split_mod._guess_vault_destination("Shared Team"), "shared")
        self.assertEqual(split_mod._guess_vault_destination("SHARED-eng"), "shared")

    def test_private_substring_guesses_personal(self):
        self.assertEqual(split_mod._guess_vault_destination("Private Stuff"), "personal")

    def test_personal_substring_guesses_personal(self):
        self.assertEqual(split_mod._guess_vault_destination("My Personal Vault"), "personal")

    def test_no_signal_guesses_blank(self):
        self.assertEqual(split_mod._guess_vault_destination("Engineering"), "")

    def test_shared_takes_precedence_over_private(self):
        self.assertEqual(split_mod._guess_vault_destination("Shared Private Docs"), "shared")


class TestProcessAccountWritesConfig(unittest.TestCase):

    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_unclassified_vault_written_with_guess(self, mock_parse, mock_vaults):
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [_make_vault("Shared Team", "U")]
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "bitwardenOrgId": "org-123",
            "vaultRename": {},
            "vaultDestination": {},
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
        self.assertEqual(
            written["accounts"][0]["vaultDestination"], {"Shared Team": "shared"}
        )
        self.assertIn("shared", str(ctx.exception).lower())

    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_unclassified_vault_no_signal_written_blank(self, mock_parse, mock_vaults):
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [_make_vault("Engineering", "U")]
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "bitwardenOrgId": "org-123",
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
        self.assertEqual(written["accounts"][0]["vaultDestination"], {"Engineering": ""})

    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_blank_existing_entry_blocks_and_is_not_overwritten(self, mock_parse, mock_vaults):
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [_make_vault("Shared Team", "U")]
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "bitwardenOrgId": "org-123",
            "vaultRename": {},
            "vaultDestination": {"Shared Team": ""},
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
        self.assertEqual(written["accounts"][0]["vaultDestination"], {"Shared Team": ""})
        self.assertIn("needs a value filled in", str(ctx.exception))

    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_existing_valid_entry_never_overwritten(self, mock_parse, mock_vaults):
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [
            _make_vault("Shared Team", "U"),
            _make_vault("Engineering", "U"),
        ]
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "bitwardenOrgId": "org-123",
            "vaultRename": {},
            "vaultDestination": {"Shared Team": "owner-only"},
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
        dest = written["accounts"][0]["vaultDestination"]
        self.assertEqual(dest["Shared Team"], "owner-only")
        self.assertEqual(dest["Engineering"], "")

    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_config_written_with_indent_and_trailing_newline(self, mock_parse, mock_vaults):
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [_make_vault("Shared Team", "U")]
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "bitwardenOrgId": "org-123",
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
            raw = config_path.read_text()
        self.assertTrue(raw.endswith("\n"))
        self.assertIn('  "accounts"', raw)

    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_other_accounts_untouched(self, mock_parse, mock_vaults):
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [_make_vault("Shared Team", "U")]
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "bitwardenOrgId": "org-123",
            "vaultRename": {},
            "vaultDestination": {},
        }
        other_account = {
            "name": "other",
            "puxPath": "tests/fixture.1pux",
            "bitwardenOrgId": "org-456",
            "vaultDestination": {"Untouched": "shared"},
        }
        config = {"accounts": [account, other_account]}
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
            written["accounts"][1], {
                "name": "other",
                "puxPath": "tests/fixture.1pux",
                "bitwardenOrgId": "org-456",
                "vaultDestination": {"Untouched": "shared"},
            }
        )


class TestProcessAccountPersonalWritesConfig(unittest.TestCase):

    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_unclassified_non_p_vault_written_with_guess(self, mock_parse, mock_vaults):
        import io
        from contextlib import redirect_stderr
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [_make_vault("Private Notes", "U")]
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
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
            written["accounts"][0]["vaultDestination"], {"Private Notes": "personal"}
        )
        self.assertIn("config.json", stderr_buf.getvalue())

    @patch("split.onepux.vaults")
    @patch("split.onepux.parse_export")
    def test_blank_existing_entry_not_overwritten_in_personal_mode(self, mock_parse, mock_vaults):
        import io
        from contextlib import redirect_stderr
        mock_parse.return_value = ({}, {})
        mock_vaults.return_value = [_make_vault("Work", "U")]
        account = {
            "name": "test",
            "puxPath": "tests/fixture.1pux",
            "vaultRename": {},
            "vaultDestination": {"Work": ""},
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
        self.assertEqual(written["accounts"][0]["vaultDestination"], {"Work": ""})
        self.assertIn("needs a value filled in", stderr_buf.getvalue())


if __name__ == "__main__":
    unittest.main()
