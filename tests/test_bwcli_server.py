"""Tests for bwcli server-selection helpers (resolve_server, ensure_server)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.bwcli import BWError, ensure_server, resolve_server


class TestResolveServer(unittest.TestCase):
    def test_us_maps_to_us_cloud(self):
        self.assertEqual(resolve_server("us"), "https://vault.bitwarden.com")

    def test_eu_maps_to_eu_cloud(self):
        self.assertEqual(resolve_server("eu"), "https://vault.bitwarden.eu")

    def test_self_hosted_url_passthrough(self):
        url = "https://vault.example.com"
        self.assertEqual(resolve_server(url), url)

    def test_self_hosted_url_with_path(self):
        url = "https://bw.internal.corp/bitwarden"
        self.assertEqual(resolve_server(url), url)

    def test_invalid_raises_bwerror(self):
        with self.assertRaises(BWError) as ctx:
            resolve_server("au")
        self.assertIn("au", str(ctx.exception))
        self.assertIn("https://", str(ctx.exception))

    def test_http_url_raises_bwerror(self):
        with self.assertRaises(BWError):
            resolve_server("http://vault.example.com")

    def test_bare_hostname_raises_bwerror(self):
        with self.assertRaises(BWError):
            resolve_server("vault.bitwarden.com")


class TestEnsureServer(unittest.TestCase):
    def _run_proc(self, returncode=0, stdout="", stderr=""):
        m = MagicMock()
        m.returncode = returncode
        m.stdout = stdout
        m.stderr = stderr
        return m

    @patch("lib.bwcli._bw_path", return_value="/usr/bin/bw")
    @patch("subprocess.run")
    def test_noop_when_already_matching(self, mock_run, mock_path):
        mock_run.return_value = self._run_proc(stdout="https://vault.bitwarden.com")
        ensure_server("us")
        # only the read call, never the set call
        self.assertEqual(mock_run.call_count, 1)
        self.assertIn("config", mock_run.call_args[0][0])
        self.assertNotIn("https://vault.bitwarden.com", mock_run.call_args[0][0])

    @patch("lib.bwcli._bw_path", return_value="/usr/bin/bw")
    @patch("subprocess.run")
    def test_switches_when_different(self, mock_run, mock_path):
        # First call: read current server. Second call: set new server.
        mock_run.side_effect = [
            self._run_proc(stdout="https://vault.bitwarden.com"),  # current
            self._run_proc(stdout=""),                              # set
        ]
        ensure_server("eu")
        self.assertEqual(mock_run.call_count, 2)
        set_args = mock_run.call_args_list[1][0][0]
        self.assertIn("https://vault.bitwarden.eu", set_args)

    @patch("lib.bwcli._bw_path", return_value="/usr/bin/bw")
    @patch("subprocess.run")
    def test_stderr_note_on_switch(self, mock_run, mock_path):
        mock_run.side_effect = [
            self._run_proc(stdout="https://vault.bitwarden.com"),
            self._run_proc(stdout=""),
        ]
        import io
        captured = io.StringIO()
        with patch("sys.stderr", captured):
            ensure_server("eu")
        self.assertIn("https://vault.bitwarden.eu", captured.getvalue())

    @patch("lib.bwcli._bw_path", return_value="/usr/bin/bw")
    @patch("subprocess.run")
    def test_clears_bw_session_on_switch(self, mock_run, mock_path):
        mock_run.side_effect = [
            self._run_proc(stdout="https://vault.bitwarden.com"),
            self._run_proc(stdout=""),
        ]
        old_env = os.environ.copy()
        os.environ["BW_SESSION"] = "old-session-key"
        try:
            ensure_server("eu")
            self.assertNotIn("BW_SESSION", os.environ)
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    @patch("lib.bwcli._bw_path", return_value="/usr/bin/bw")
    @patch("subprocess.run")
    def test_bw_config_failure_raises(self, mock_run, mock_path):
        mock_run.side_effect = [
            self._run_proc(stdout="https://vault.bitwarden.com"),
            self._run_proc(returncode=1, stderr="some bw error"),
        ]
        with self.assertRaises(BWError):
            ensure_server("eu")

    @patch("lib.bwcli._bw_path", return_value="/usr/bin/bw")
    @patch("subprocess.run")
    def test_per_account_region_switching(self, mock_run, mock_path):
        """Two accounts in different regions trigger two server switches."""
        config_accounts = [
            {"name": "family", "bitwardenServer": "us"},
            {"name": "team",   "bitwardenServer": "eu"},
        ]

        call_responses = [
            # Account 1 (us): read → already us, no switch
            self._run_proc(stdout="https://vault.bitwarden.com"),
            # Account 2 (eu): read → still us, switch to eu
            self._run_proc(stdout="https://vault.bitwarden.com"),
            self._run_proc(stdout=""),  # set eu
        ]
        mock_run.side_effect = call_responses

        for account in config_accounts:
            spec = account.get("bitwardenServer", "us")
            ensure_server(spec)

        # Third call sets eu
        set_call_args = mock_run.call_args_list[2][0][0]
        self.assertIn("https://vault.bitwarden.eu", set_call_args)


if __name__ == "__main__":
    unittest.main()
