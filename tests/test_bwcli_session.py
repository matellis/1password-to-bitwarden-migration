"""Tests for bwcli.ensure_session() using mocked subprocess and os.environ."""
import importlib
import json
import os
import subprocess
import sys
import types
import unittest
from unittest.mock import MagicMock, call, patch

# Make sure lib/ is importable from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import lib.bwcli as bwcli


def _status_output(status: str) -> str:
    return json.dumps({"status": status, "userEmail": "alex@example.com"})


def _proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    p = MagicMock()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


class TestEnsureSessionAlreadyUnlocked(unittest.TestCase):
    """Already unlocked — no login or unlock calls."""

    @patch("lib.bwcli.shutil.which", return_value="/usr/bin/bw")
    @patch("lib.bwcli.subprocess.run")
    def test_no_auth_calls(self, mock_run, _which):
        # ensure_server reads current server first (call 0), then _bw_status (call 1)
        mock_run.side_effect = [
            _proc(stdout="https://vault.bitwarden.com"),  # bw config server (read)
            _proc(stdout=_status_output("unlocked")),     # bw status
        ]
        env = {"BW_SESSION": "existing-token"}
        with patch.dict(os.environ, env, clear=False):
            bwcli.ensure_session()
        # Two calls: bw config server + bw status (no auth needed)
        self.assertEqual(mock_run.call_count, 2)
        args = mock_run.call_args[0][0]
        self.assertIn("status", args)


class TestEnsureSessionLocked(unittest.TestCase):
    """Locked vault — unlock called, session stored in env."""

    @patch("lib.bwcli.shutil.which", return_value="/usr/bin/bw")
    @patch("lib.bwcli.subprocess.run")
    def test_unlock_called_session_stored(self, mock_run, _which):
        unlock_token = "s3cr3t-session-key"
        mock_run.side_effect = [
            _proc(stdout="https://vault.bitwarden.com"),  # bw config server (read)
            _proc(stdout=_status_output("locked")),        # _bw_status()
            _proc(stdout=unlock_token),                    # bw unlock --raw
            _proc(stdout=_status_output("unlocked")),      # _bw_status() verify
        ]
        env = {}
        with patch.dict(os.environ, env, clear=False):
            # Remove BW_PASSWORD and BW_SESSION to test interactive path
            os.environ.pop("BW_PASSWORD", None)
            os.environ.pop("BW_SESSION", None)
            bwcli.ensure_session()
            stored = os.environ.get("BW_SESSION")
        self.assertEqual(stored, unlock_token)
        # Third call (index 2) should be bw unlock --raw (no --passwordenv)
        unlock_call_args = mock_run.call_args_list[2][0][0]
        self.assertIn("unlock", unlock_call_args)
        self.assertIn("--raw", unlock_call_args)
        self.assertNotIn("--passwordenv", unlock_call_args)

    @patch("lib.bwcli.shutil.which", return_value="/usr/bin/bw")
    @patch("lib.bwcli.subprocess.run")
    def test_unlock_uses_passwordenv_when_set(self, mock_run, _which):
        unlock_token = "pw-session-key"
        mock_run.side_effect = [
            _proc(stdout="https://vault.bitwarden.com"),  # bw config server (read)
            _proc(stdout=_status_output("locked")),
            _proc(stdout=unlock_token),
            _proc(stdout=_status_output("unlocked")),
        ]
        with patch.dict(os.environ, {"BW_PASSWORD": "hunter2"}, clear=False):
            os.environ.pop("BW_SESSION", None)
            bwcli.ensure_session()
            stored = os.environ.get("BW_SESSION")
        self.assertEqual(stored, unlock_token)
        unlock_call_args = mock_run.call_args_list[2][0][0]
        self.assertIn("--passwordenv", unlock_call_args)
        self.assertIn("BW_PASSWORD", unlock_call_args)


class TestEnsureSessionUnauthenticatedApiKey(unittest.TestCase):
    """Unauthenticated with API key env vars — apikey login then unlock."""

    @patch("lib.bwcli.shutil.which", return_value="/usr/bin/bw")
    @patch("lib.bwcli.subprocess.run")
    def test_apikey_login_then_unlock(self, mock_run, _which):
        unlock_token = "api-session-key"
        mock_run.side_effect = [
            _proc(stdout="https://vault.bitwarden.com"),      # bw config server (read)
            _proc(stdout=_status_output("unauthenticated")),  # initial status
            _proc(stdout=""),                                  # bw login --apikey
            _proc(stdout=unlock_token),                        # bw unlock --raw
            _proc(stdout=_status_output("unlocked")),          # verify
        ]
        env = {"BW_CLIENTID": "client-id-abc", "BW_CLIENTSECRET": "client-secret-xyz"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("BW_SESSION", None)
            os.environ.pop("BW_PASSWORD", None)
            bwcli.ensure_session()
            stored = os.environ.get("BW_SESSION")
        self.assertEqual(stored, unlock_token)
        login_call = mock_run.call_args_list[2][0][0]
        self.assertIn("login", login_call)
        self.assertIn("--apikey", login_call)
        unlock_call = mock_run.call_args_list[3][0][0]
        self.assertIn("unlock", unlock_call)
        self.assertIn("--raw", unlock_call)


class TestEnsureSessionUnauthenticatedInteractive(unittest.TestCase):
    """Unauthenticated without env vars — interactive login --raw path."""

    @patch("lib.bwcli.shutil.which", return_value="/usr/bin/bw")
    @patch("lib.bwcli.subprocess.run")
    def test_login_raw_captures_session(self, mock_run, _which):
        login_token = "interactive-raw-token"
        mock_run.side_effect = [
            _proc(stdout="https://vault.bitwarden.com"),      # bw config server (read)
            _proc(stdout=_status_output("unauthenticated")),  # status
            _proc(stdout=login_token),                         # bw login --raw
            _proc(stdout=_status_output("unlocked")),          # verify
        ]
        keys_to_remove = ["BW_CLIENTID", "BW_CLIENTSECRET", "BW_SESSION", "BW_PASSWORD"]
        with patch.dict(os.environ, {}, clear=False):
            for k in keys_to_remove:
                os.environ.pop(k, None)
            bwcli.ensure_session()
            stored = os.environ.get("BW_SESSION")
        self.assertEqual(stored, login_token)
        login_call = mock_run.call_args_list[2][0][0]
        self.assertIn("login", login_call)
        self.assertIn("--raw", login_call)

    @patch("lib.bwcli.shutil.which", return_value="/usr/bin/bw")
    @patch("lib.bwcli.subprocess.run")
    def test_login_raw_fallback_to_interactive(self, mock_run, _which):
        """If bw login --raw exits non-zero, fall back to plain bw login then unlock."""
        unlock_token = "fallback-session-key"
        mock_run.side_effect = [
            _proc(stdout="https://vault.bitwarden.com"),       # bw config server (read)
            _proc(stdout=_status_output("unauthenticated")),   # status
            _proc(returncode=1, stdout="", stderr="error"),    # bw login --raw fails
            _proc(stdout=""),                                   # bw login (fallback)
            _proc(stdout=unlock_token),                         # bw unlock --raw
            _proc(stdout=_status_output("unlocked")),           # verify
        ]
        keys_to_remove = ["BW_CLIENTID", "BW_CLIENTSECRET", "BW_SESSION", "BW_PASSWORD"]
        with patch.dict(os.environ, {}, clear=False):
            for k in keys_to_remove:
                os.environ.pop(k, None)
            bwcli.ensure_session()
            stored = os.environ.get("BW_SESSION")
        self.assertEqual(stored, unlock_token)
        # Fourth call (index 3): fallback plain login (no --raw)
        fallback_call = mock_run.call_args_list[3][0][0]
        self.assertIn("login", fallback_call)
        self.assertNotIn("--raw", fallback_call)


class TestEnsureSessionUnlockFailure(unittest.TestCase):
    """Unlock returns empty session key — raises BWError."""

    @patch("lib.bwcli.shutil.which", return_value="/usr/bin/bw")
    @patch("lib.bwcli.subprocess.run")
    def test_empty_session_raises(self, mock_run, _which):
        mock_run.side_effect = [
            _proc(stdout="https://vault.bitwarden.com"),  # bw config server (read)
            _proc(stdout=_status_output("locked")),        # status
            _proc(stdout=""),                              # bw unlock --raw → empty
        ]
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BW_SESSION", None)
            os.environ.pop("BW_PASSWORD", None)
            with self.assertRaises(bwcli.BWError):
                bwcli.ensure_session()

    @patch("lib.bwcli.shutil.which", return_value="/usr/bin/bw")
    @patch("lib.bwcli.subprocess.run")
    def test_unlock_nonzero_exit_raises(self, mock_run, _which):
        mock_run.side_effect = [
            _proc(stdout="https://vault.bitwarden.com"),  # bw config server (read)
            _proc(stdout=_status_output("locked")),
            _proc(returncode=1, stdout="", stderr="Invalid master password"),
        ]
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BW_SESSION", None)
            os.environ.pop("BW_PASSWORD", None)
            with self.assertRaises(bwcli.BWError):
                bwcli.ensure_session()

    @patch("lib.bwcli.shutil.which", return_value="/usr/bin/bw")
    @patch("lib.bwcli.subprocess.run")
    def test_status_not_unlocked_after_unlock_raises(self, mock_run, _which):
        mock_run.side_effect = [
            _proc(stdout="https://vault.bitwarden.com"),  # bw config server (read)
            _proc(stdout=_status_output("locked")),
            _proc(stdout="some-token"),
            _proc(stdout=_status_output("locked")),  # still locked after unlock
        ]
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BW_SESSION", None)
            os.environ.pop("BW_PASSWORD", None)
            with self.assertRaises(bwcli.BWError):
                bwcli.ensure_session()


class TestCheckPrereqsCompat(unittest.TestCase):
    """check_prereqs() is a thin wrapper — delegates to ensure_session()."""

    @patch("lib.bwcli.ensure_session")
    def test_delegates(self, mock_ensure):
        bwcli.check_prereqs()
        mock_ensure.assert_called_once()


if __name__ == "__main__":
    unittest.main()
