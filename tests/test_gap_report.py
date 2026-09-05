#!/usr/bin/env python3
"""Tests for passkey_inventory.gap_report set math."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from lib import passkey_inventory as pkinv


def _inv_entry(site: str, username: str = "", url: str = "") -> dict:
    return {"site": site, "username": username, "url": url}


def _bw_with_passkey(name: str, username: str = "", url: str = "") -> dict:
    return {
        "type": 1,
        "name": name,
        "login": {
            "username": username,
            "uris": [{"uri": url}] if url else [],
            "fido2Credentials": [{"credentialId": "fakeid"}],
        },
    }


def _bw_without_passkey(name: str, username: str = "", url: str = "") -> dict:
    item = _bw_with_passkey(name, username, url)
    item["login"]["fido2Credentials"] = []
    return item


class TestGapReport(unittest.TestCase):

    def test_all_matched(self):
        inventory = [_inv_entry("Google", "alice@example.com", "https://accounts.google.com")]
        bw_items = [_bw_with_passkey("Google", "alice@example.com", "https://accounts.google.com")]
        report = pkinv.gap_report(inventory, bw_items)
        self.assertEqual(len(report["matched"]), 1)
        self.assertEqual(report["missing_from_bw"], [])
        self.assertEqual(report["unexpected_in_bw"], [])

    def test_missing_from_bw(self):
        inventory = [
            _inv_entry("Google", "alice@example.com", "https://accounts.google.com"),
            _inv_entry("GitHub", "alice", "https://github.com"),
        ]
        bw_items = [_bw_with_passkey("Google", "alice@example.com", "https://accounts.google.com")]
        report = pkinv.gap_report(inventory, bw_items)
        self.assertEqual(len(report["matched"]), 1)
        self.assertEqual(len(report["missing_from_bw"]), 1)
        self.assertEqual(report["missing_from_bw"][0]["site"], "GitHub")

    def test_unexpected_in_bw(self):
        inventory = [_inv_entry("Google", "alice@example.com", "https://accounts.google.com")]
        bw_items = [
            _bw_with_passkey("Google", "alice@example.com", "https://accounts.google.com"),
            _bw_with_passkey("Slack", "alice@corp.example", "https://slack.com"),
        ]
        report = pkinv.gap_report(inventory, bw_items)
        self.assertEqual(len(report["matched"]), 1)
        self.assertEqual(len(report["unexpected_in_bw"]), 1)
        self.assertEqual(report["unexpected_in_bw"][0]["name"], "Slack")

    def test_empty_inventory(self):
        bw_items = [_bw_with_passkey("Google", "alice@example.com", "https://accounts.google.com")]
        report = pkinv.gap_report([], bw_items)
        self.assertEqual(report["matched"], [])
        self.assertEqual(report["missing_from_bw"], [])
        self.assertEqual(len(report["unexpected_in_bw"]), 1)

    def test_empty_bw(self):
        inventory = [_inv_entry("Google", "alice@example.com", "https://accounts.google.com")]
        report = pkinv.gap_report(inventory, [])
        self.assertEqual(report["matched"], [])
        self.assertEqual(len(report["missing_from_bw"]), 1)
        self.assertEqual(report["unexpected_in_bw"], [])

    def test_bw_items_without_passkeys_ignored(self):
        inventory = [_inv_entry("Google", "alice@example.com", "https://accounts.google.com")]
        bw_items = [
            _bw_without_passkey("Google", "alice@example.com", "https://accounts.google.com"),
        ]
        report = pkinv.gap_report(inventory, bw_items)
        self.assertEqual(report["matched"], [])
        self.assertEqual(len(report["missing_from_bw"]), 1)
        self.assertEqual(report["unexpected_in_bw"], [])

    def test_host_level_url_match(self):
        inventory = [_inv_entry("Google", "alice@example.com", "accounts.google.com")]
        bw_items = [_bw_with_passkey("Google", "alice@example.com", "https://accounts.google.com/signin")]
        report = pkinv.gap_report(inventory, bw_items)
        self.assertEqual(len(report["matched"]), 1)

    def test_no_url_in_inventory_matches(self):
        inventory = [_inv_entry("GitHub", "alice", "")]
        bw_items = [_bw_with_passkey("GitHub", "alice", "https://github.com/login")]
        report = pkinv.gap_report(inventory, bw_items)
        self.assertEqual(len(report["matched"]), 1)

    def test_no_double_match(self):
        inventory = [
            _inv_entry("Google", "alice@example.com", "https://accounts.google.com"),
            _inv_entry("Google", "alice@example.com", "https://accounts.google.com"),
        ]
        bw_items = [_bw_with_passkey("Google", "alice@example.com", "https://accounts.google.com")]
        report = pkinv.gap_report(inventory, bw_items)
        self.assertEqual(len(report["matched"]), 1)
        self.assertEqual(len(report["missing_from_bw"]), 1)

    def test_both_empty(self):
        report = pkinv.gap_report([], [])
        self.assertEqual(report["matched"], [])
        self.assertEqual(report["missing_from_bw"], [])
        self.assertEqual(report["unexpected_in_bw"], [])


if __name__ == "__main__":
    unittest.main()
