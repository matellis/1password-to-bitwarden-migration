"""Tests for the per-account login identity guard in bwcli.ensure_session()."""
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import lib.bwcli as bwcli


def _status(status: str, email: str = "correct@example.com") -> str:
    return json.dumps({"status": status, "userEmail": email})


def _proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    p = MagicMock()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


class TestEmailMatchReusesSession(unittest.TestCase):
    """Unlocked with the correct email — no logout or login."""

    @patch("lib.bwcli.shutil.which", return_value="/usr/bin/bw")
    @patch("lib.bwcli.subprocess.run")
    def test_matching_email_returns_immediately(self, mock_run, _which):
        mock_run.side_effect = [
            _proc(stdout="https://vault.bitwarden.com"),
            _proc(stdout=_status("unlocked", "correct@example.com")),
        ]
        with patch.dict(os.environ, {"BW_SESSION": "existing"}, clear=False):
            bwcli.ensure_session("us", expected_email="correct@example.com")
        self.assertEqual(mock_run.call_count, 2)

    @patch("lib.bwcli.shutil.which", return_value="/usr/bin/bw")
    @patch("lib.bwcli.subprocess.run")
    def test_matching_email_case_insensitive(self, mock_run, _which):
        mock_run.side_effect = [
            _proc(stdout="https://vault.bitwarden.com"),
            _proc(stdout=_status("unlocked", "Correct@Example.COM")),
        ]
        with patch.dict(os.environ, {"BW_SESSION": "existing"}, clear=False):
            bwcli.ensure_session("us", expected_email="correct@example.com")
        self.assertEqual(mock_run.call_count, 2)

    @patch("lib.bwcli.shutil.which", return_value="/usr/bin/bw")
    @patch("lib.bwcli.subprocess.run")
    def test_no_expected_email_skips_check(self, mock_run, _which):
        mock_run.side_effect = [
            _proc(stdout="https://vault.bitwarden.com"),
            _proc(stdout=_status("unlocked", "anyone@example.com")),
        ]
        with patch.dict(os.environ, {"BW_SESSION": "existing"}, clear=False):
            bwcli.ensure_session("us")
        self.assertEqual(mock_run.call_count, 2)


class TestEmailMismatchTriggersReauth(unittest.TestCase):
    """Unlocked but wrong email — logout then login with the correct email."""

    @patch("lib.bwcli.shutil.which", return_value="/usr/bin/bw")
    @patch("lib.bwcli.subprocess.run")
    def test_mismatch_triggers_logout_and_login(self, mock_run, _which):
        login_token = "new-session-token"
        mock_run.side_effect = [
            _proc(stdout="https://vault.bitwarden.com"),       # bw config server
            _proc(stdout=_status("unlocked", "wrong@example.com")),  # bw status
            _proc(stdout=""),                                   # bw logout
            _proc(stdout=login_token),                          # bw login <email> --raw
            _proc(stdout=_status("unlocked", "correct@example.com")),  # verify
        ]
        with patch.dict(os.environ, {"BW_SESSION": "old-token"}, clear=False):
            bwcli.ensure_session("us", expected_email="correct@example.com")
            stored = os.environ.get("BW_SESSION")
        self.assertEqual(stored, login_token)
        logout_call = mock_run.call_args_list[2][0][0]
        self.assertIn("logout", logout_call)
        login_call = mock_run.call_args_list[3][0][0]
        self.assertIn("login", login_call)
        self.assertIn("correct@example.com", login_call)
        self.assertIn("--raw", login_call)

    @patch("lib.bwcli.shutil.which", return_value="/usr/bin/bw")
    @patch("lib.bwcli.subprocess.run")
    def test_mismatch_clears_bw_session_before_login(self, mock_run, _which):
        login_token = "fresh-token"
        mock_run.side_effect = [
            _proc(stdout="https://vault.bitwarden.com"),
            _proc(stdout=_status("unlocked", "other@example.com")),
            _proc(stdout=""),                   # logout
            _proc(stdout=login_token),           # login --raw
            _proc(stdout=_status("unlocked", "me@example.com")),
        ]
        with patch.dict(os.environ, {"BW_SESSION": "stale"}, clear=False):
            bwcli.ensure_session("us", expected_email="me@example.com")
            stored = os.environ.get("BW_SESSION")
        self.assertEqual(stored, login_token)


class TestEmailPassedToLoginCommand(unittest.TestCase):
    """When unauthenticated with expected_email, login command includes the email."""

    @patch("lib.bwcli.shutil.which", return_value="/usr/bin/bw")
    @patch("lib.bwcli.subprocess.run")
    def test_email_in_login_command_when_unauthenticated(self, mock_run, _which):
        login_token = "session-abc"
        mock_run.side_effect = [
            _proc(stdout="https://vault.bitwarden.com"),
            _proc(stdout=_status("unauthenticated", "")),
            _proc(stdout=login_token),
            _proc(stdout=_status("unlocked", "me@example.com")),
        ]
        keys_to_remove = ["BW_CLIENTID", "BW_CLIENTSECRET", "BW_SESSION"]
        with patch.dict(os.environ, {}, clear=False):
            for k in keys_to_remove:
                os.environ.pop(k, None)
            bwcli.ensure_session("us", expected_email="me@example.com")
            stored = os.environ.get("BW_SESSION")
        self.assertEqual(stored, login_token)
        login_call = mock_run.call_args_list[2][0][0]
        self.assertIn("login", login_call)
        self.assertIn("me@example.com", login_call)
        self.assertIn("--raw", login_call)

    @patch("lib.bwcli.shutil.which", return_value="/usr/bin/bw")
    @patch("lib.bwcli.subprocess.run")
    def test_no_email_in_login_command_when_none(self, mock_run, _which):
        login_token = "session-xyz"
        mock_run.side_effect = [
            _proc(stdout="https://vault.bitwarden.com"),
            _proc(stdout=_status("unauthenticated", "")),
            _proc(stdout=login_token),
            _proc(stdout=_status("unlocked", "")),
        ]
        keys_to_remove = ["BW_CLIENTID", "BW_CLIENTSECRET", "BW_SESSION"]
        with patch.dict(os.environ, {}, clear=False):
            for k in keys_to_remove:
                os.environ.pop(k, None)
            bwcli.ensure_session("us")
            stored = os.environ.get("BW_SESSION")
        self.assertEqual(stored, login_token)
        login_call = mock_run.call_args_list[2][0][0]
        self.assertIn("login", login_call)
        self.assertNotIn("@", " ".join(str(a) for a in login_call))


class TestLockedEmailMismatch(unittest.TestCase):
    """Locked with wrong email — logout then login, not just unlock."""

    @patch("lib.bwcli.shutil.which", return_value="/usr/bin/bw")
    @patch("lib.bwcli.subprocess.run")
    def test_locked_mismatch_triggers_logout_not_unlock(self, mock_run, _which):
        login_token = "new-session-after-reauth"
        mock_run.side_effect = [
            _proc(stdout="https://vault.bitwarden.com"),
            _proc(stdout=_status("locked", "wrong@example.com")),
            _proc(stdout=""),
            _proc(stdout=login_token),
            _proc(stdout=_status("unlocked", "correct@example.com")),
        ]
        with patch.dict(os.environ, {"BW_SESSION": "old-token"}, clear=False):
            bwcli.ensure_session("us", expected_email="correct@example.com")
            stored = os.environ.get("BW_SESSION")
        self.assertEqual(stored, login_token)
        logout_call = mock_run.call_args_list[2][0][0]
        self.assertIn("logout", logout_call)
        login_call = mock_run.call_args_list[3][0][0]
        self.assertIn("login", login_call)
        unlock_calls = [
            c for c in mock_run.call_args_list
            if "unlock" in (c[0][0] if c[0] else [])
        ]
        self.assertEqual(unlock_calls, [], "unlock must not be called when email mismatches on locked vault")


class TestPostLoginEmailCheck(unittest.TestCase):
    """After successful login, if bw status shows wrong email, raise BWError."""

    @patch("lib.bwcli.shutil.which", return_value="/usr/bin/bw")
    @patch("lib.bwcli.subprocess.run")
    def test_wrong_email_after_login_raises_bwerror(self, mock_run, _which):
        login_token = "some-session"
        mock_run.side_effect = [
            _proc(stdout="https://vault.bitwarden.com"),
            _proc(stdout=_status("unauthenticated", "")),
            _proc(stdout=login_token),
            _proc(stdout=_status("unlocked", "wrong@example.com")),
        ]
        keys_to_remove = ["BW_CLIENTID", "BW_CLIENTSECRET", "BW_SESSION"]
        with patch.dict(os.environ, {}, clear=False):
            for k in keys_to_remove:
                os.environ.pop(k, None)
            with self.assertRaises(bwcli.BWError) as ctx:
                bwcli.ensure_session("us", expected_email="correct@example.com")
        self.assertIn("wrong@example.com", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
