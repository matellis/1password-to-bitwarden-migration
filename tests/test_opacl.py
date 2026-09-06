"""Tests for lib/opacl.py, the op CLI wrapper used for live vault sharing lookups."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib import opacl


def _proc(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


class TestOpMissing(unittest.TestCase):
    @patch("lib.opacl.shutil.which", return_value=None)
    @patch("lib.opacl.subprocess.run")
    def test_owner_email_none_when_op_missing(self, mock_run, mock_which):
        self.assertIsNone(opacl.owner_email(None))
        mock_run.assert_not_called()

    @patch("lib.opacl.shutil.which", return_value=None)
    @patch("lib.opacl.subprocess.run")
    def test_vault_members_none_when_op_missing(self, mock_run, mock_which):
        self.assertIsNone(opacl.vault_members("Engineering", None))
        mock_run.assert_not_called()


class TestOwnerEmail(unittest.TestCase):
    @patch("lib.opacl.shutil.which", return_value="/usr/local/bin/op")
    @patch("lib.opacl.subprocess.run")
    def test_success(self, mock_run, mock_which):
        mock_run.return_value = _proc(stdout='{"email": "owner@example.com"}')
        self.assertEqual(opacl.owner_email(None), "owner@example.com")

    @patch("lib.opacl.shutil.which", return_value="/usr/local/bin/op")
    @patch("lib.opacl.subprocess.run")
    def test_bad_json_returns_none(self, mock_run, mock_which):
        mock_run.return_value = _proc(stdout="not json")
        self.assertIsNone(opacl.owner_email(None))

    @patch("lib.opacl.shutil.which", return_value="/usr/local/bin/op")
    @patch("lib.opacl.subprocess.run")
    def test_nonzero_exit_returns_none(self, mock_run, mock_which):
        mock_run.return_value = _proc(returncode=1, stderr="not signed in")
        self.assertIsNone(opacl.owner_email(None))

    @patch("lib.opacl.shutil.which", return_value="/usr/local/bin/op")
    @patch("lib.opacl.subprocess.run")
    def test_missing_email_key_returns_none(self, mock_run, mock_which):
        mock_run.return_value = _proc(stdout='{"not_email": "x"}')
        self.assertIsNone(opacl.owner_email(None))

    @patch("lib.opacl.shutil.which", return_value="/usr/local/bin/op")
    @patch("lib.opacl.subprocess.run")
    def test_account_flag_included_when_set(self, mock_run, mock_which):
        mock_run.return_value = _proc(stdout='{"email": "a@example.com"}')
        opacl.owner_email("team")
        args = mock_run.call_args[0][0]
        self.assertIn("--account", args)
        self.assertIn("team", args)

    @patch("lib.opacl.shutil.which", return_value="/usr/local/bin/op")
    @patch("lib.opacl.subprocess.run")
    def test_account_flag_omitted_when_none(self, mock_run, mock_which):
        mock_run.return_value = _proc(stdout='{"email": "a@example.com"}')
        opacl.owner_email(None)
        args = mock_run.call_args[0][0]
        self.assertNotIn("--account", args)


class TestVaultMembers(unittest.TestCase):
    @patch("lib.opacl.shutil.which", return_value="/usr/local/bin/op")
    @patch("lib.opacl.subprocess.run")
    def test_primary_call_failure_returns_none(self, mock_run, mock_which):
        mock_run.return_value = _proc(returncode=1, stderr="vault not found")
        self.assertIsNone(opacl.vault_members("Engineering", None))

    @patch("lib.opacl.shutil.which", return_value="/usr/local/bin/op")
    @patch("lib.opacl.subprocess.run")
    def test_primary_call_bad_json_returns_none(self, mock_run, mock_which):
        mock_run.return_value = _proc(stdout="not json")
        self.assertIsNone(opacl.vault_members("Engineering", None))

    @patch("lib.opacl.shutil.which", return_value="/usr/local/bin/op")
    @patch("lib.opacl.subprocess.run")
    def test_no_members_no_groups(self, mock_run, mock_which):
        mock_run.side_effect = [
            _proc(stdout="[]"),
            _proc(stdout="[]"),
        ]
        result = opacl.vault_members("Engineering", None)
        self.assertEqual(result, {"emails": [], "groups": [], "failed_groups": []})

    @patch("lib.opacl.shutil.which", return_value="/usr/local/bin/op")
    @patch("lib.opacl.subprocess.run")
    def test_direct_members_collected(self, mock_run, mock_which):
        mock_run.side_effect = [
            _proc(stdout='[{"email": "a@example.com"}, {"not_email": 1}, {"email": "b@example.com"}]'),
            _proc(stdout="[]"),
        ]
        result = opacl.vault_members("Engineering", None)
        self.assertEqual(sorted(result["emails"]), ["a@example.com", "b@example.com"])
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["failed_groups"], [])

    @patch("lib.opacl.shutil.which", return_value="/usr/local/bin/op")
    @patch("lib.opacl.subprocess.run")
    def test_group_expansion_merges_members(self, mock_run, mock_which):
        mock_run.side_effect = [
            _proc(stdout='[{"email": "a@example.com"}]'),
            _proc(stdout='[{"name": "Engineers"}]'),
            _proc(stdout='[{"email": "c@example.com"}, {"email": "d@example.com"}]'),
        ]
        result = opacl.vault_members("Engineering", None)
        self.assertEqual(
            sorted(result["emails"]),
            ["a@example.com", "c@example.com", "d@example.com"],
        )
        self.assertEqual(result["groups"], ["Engineers"])
        self.assertEqual(result["failed_groups"], [])

    @patch("lib.opacl.shutil.which", return_value="/usr/local/bin/op")
    @patch("lib.opacl.subprocess.run")
    def test_one_group_fails_others_still_expand(self, mock_run, mock_which):
        mock_run.side_effect = [
            _proc(stdout="[]"),
            _proc(stdout='[{"name": "Broken"}, {"name": "Good"}]'),
            _proc(returncode=1, stderr="group not found"),
            _proc(stdout='[{"email": "e@example.com"}]'),
        ]
        result = opacl.vault_members("Engineering", None)
        self.assertEqual(result["emails"], ["e@example.com"])
        self.assertEqual(result["groups"], ["Broken", "Good"])
        self.assertEqual(result["failed_groups"], ["Broken"])

    @patch("lib.opacl.shutil.which", return_value="/usr/local/bin/op")
    @patch("lib.opacl.subprocess.run")
    def test_user_without_viewing_excluded(self, mock_run, mock_which):
        mock_run.side_effect = [
            _proc(stdout='[{"email": "organizer@example.com", "permissions": ["allow_managing"]}]'),
            _proc(stdout="[]"),
        ]
        result = opacl.vault_members("Engineering", None)
        self.assertEqual(result["emails"], [])

    @patch("lib.opacl.shutil.which", return_value="/usr/local/bin/op")
    @patch("lib.opacl.subprocess.run")
    def test_user_with_viewing_included(self, mock_run, mock_which):
        mock_run.side_effect = [
            _proc(stdout='[{"email": "a@example.com", "permissions": ["allow_viewing", "allow_editing"]}]'),
            _proc(stdout="[]"),
        ]
        result = opacl.vault_members("Engineering", None)
        self.assertEqual(result["emails"], ["a@example.com"])

    @patch("lib.opacl.shutil.which", return_value="/usr/local/bin/op")
    @patch("lib.opacl.subprocess.run")
    def test_user_missing_permissions_key_included(self, mock_run, mock_which):
        mock_run.side_effect = [
            _proc(stdout='[{"email": "a@example.com"}]'),
            _proc(stdout="[]"),
        ]
        result = opacl.vault_members("Engineering", None)
        self.assertEqual(result["emails"], ["a@example.com"])

    @patch("lib.opacl.shutil.which", return_value="/usr/local/bin/op")
    @patch("lib.opacl.subprocess.run")
    def test_managing_only_group_skipped_not_expanded(self, mock_run, mock_which):
        mock_run.side_effect = [
            _proc(stdout="[]"),
            _proc(stdout='[{"name": "Owners", "permissions": ["allow_managing"]}]'),
        ]
        result = opacl.vault_members("Engineering", None)
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["emails"], [])
        self.assertEqual(result["failed_groups"], [])
        # Only the two vault-level calls happen; the group is never expanded.
        self.assertEqual(mock_run.call_count, 2)

    @patch("lib.opacl.shutil.which", return_value="/usr/local/bin/op")
    @patch("lib.opacl.subprocess.run")
    def test_group_with_viewing_expanded(self, mock_run, mock_which):
        mock_run.side_effect = [
            _proc(stdout="[]"),
            _proc(stdout='[{"name": "Engineers", "permissions": ["allow_viewing"]}]'),
            _proc(stdout='[{"email": "c@example.com"}]'),
        ]
        result = opacl.vault_members("Engineering", None)
        self.assertEqual(result["groups"], ["Engineers"])
        self.assertEqual(result["emails"], ["c@example.com"])

    @patch("lib.opacl.shutil.which", return_value="/usr/local/bin/op")
    @patch("lib.opacl.subprocess.run")
    def test_mixed_vault_managing_group_and_viewing_user(self, mock_run, mock_which):
        mock_run.side_effect = [
            _proc(stdout='[{"email": "a@example.com", "permissions": ["allow_viewing"]}]'),
            _proc(stdout='[{"name": "Owners", "permissions": ["allow_managing"]}]'),
        ]
        result = opacl.vault_members("Engineering", None)
        self.assertEqual(result["emails"], ["a@example.com"])
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["failed_groups"], [])

    @patch("lib.opacl.shutil.which", return_value="/usr/local/bin/op")
    @patch("lib.opacl.subprocess.run")
    def test_account_flag_propagates_to_all_calls(self, mock_run, mock_which):
        mock_run.side_effect = [
            _proc(stdout='[{"email": "a@example.com"}]'),
            _proc(stdout='[{"name": "Engineers"}]'),
            _proc(stdout='[{"email": "c@example.com"}]'),
        ]
        opacl.vault_members("Engineering", "team")
        for call in mock_run.call_args_list:
            args = call[0][0]
            self.assertIn("--account", args)
            self.assertIn("team", args)


if __name__ == "__main__":
    unittest.main()
