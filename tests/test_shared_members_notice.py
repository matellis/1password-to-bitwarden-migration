"""Tests for the post-import shared-member checklist notice in import.py."""
import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

_spec = importlib.util.spec_from_file_location("import_mod", _ROOT / "import.py")
_import_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_import_mod)


class TestSharedMembersNotice(unittest.TestCase):

    def test_notice_printed_for_shared_with_members(self):
        vault_entry = {
            "vaultName": "Kids Accounts",
            "destination": "shared",
            "shareWith": ["kid@example.com", "mom@example.com"],
        }
        buf = io.StringIO()
        with redirect_stdout(buf):
            _import_mod._print_shared_members_notice(vault_entry)
        out = buf.getvalue()
        self.assertIn("Kids Accounts", out)
        self.assertIn("POST-IMPORT ACTION REQUIRED", out)
        self.assertIn("kid@example.com", out)
        self.assertIn("mom@example.com", out)
        self.assertIn("Manage access", out)
        self.assertIn("bw CLI does not support setting collection member permissions", out)

    def test_owner_only_notice_unchanged_format(self):
        vault_entry = {"vaultName": "Finances", "destination": "owner-only"}
        buf = io.StringIO()
        with redirect_stdout(buf):
            _import_mod._print_owner_only_notice(vault_entry)
        out = buf.getvalue()
        self.assertIn("Finances", out)
        self.assertIn("POST-IMPORT ACTION REQUIRED", out)
        self.assertIn("Regular members cannot see a new collection", out)

    def _run_main_loop_notice_check(self, vault_entry: dict, status: str) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            _import_mod._post_import_notices(vault_entry, status)
        return buf.getvalue()

    def test_plain_shared_no_notice(self):
        vault_entry = {"vaultName": "Shared", "destination": "shared"}
        out = self._run_main_loop_notice_check(vault_entry, "ok")
        self.assertEqual(out, "")

    def test_shared_with_members_prints_notice_on_ok(self):
        vault_entry = {
            "vaultName": "Kids Accounts",
            "destination": "shared",
            "shareWith": ["kid@example.com"],
        }
        out = self._run_main_loop_notice_check(vault_entry, "ok")
        self.assertIn("POST-IMPORT ACTION REQUIRED", out)

    def test_shared_with_members_prints_notice_on_partial(self):
        vault_entry = {
            "vaultName": "Kids Accounts",
            "destination": "shared",
            "shareWith": ["kid@example.com"],
        }
        out = self._run_main_loop_notice_check(vault_entry, "partial")
        self.assertIn("POST-IMPORT ACTION REQUIRED", out)

    def test_shared_with_members_no_notice_on_failure(self):
        vault_entry = {
            "vaultName": "Kids Accounts",
            "destination": "shared",
            "shareWith": ["kid@example.com"],
        }
        out = self._run_main_loop_notice_check(vault_entry, "failed")
        self.assertEqual(out, "")

    def test_shared_empty_share_with_no_notice(self):
        vault_entry = {"vaultName": "Shared", "destination": "shared", "shareWith": []}
        out = self._run_main_loop_notice_check(vault_entry, "ok")
        self.assertEqual(out, "")

    def test_owner_only_and_shared_notices_are_independent(self):
        """A non-shared destination never triggers the shared-members notice, even with shareWith set."""
        vault_entry = {
            "vaultName": "Finances",
            "destination": "owner-only",
            "shareWith": ["a@example.com"],
        }
        out = self._run_main_loop_notice_check(vault_entry, "ok")
        self.assertIn("Regular members cannot see a new collection", out)
        self.assertNotIn("grant access to this collection", out)


if __name__ == "__main__":
    unittest.main()
