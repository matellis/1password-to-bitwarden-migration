#!/usr/bin/env python3
"""Tests for adopt.py: fingerprint matching, dry-run safety, delete-after-verify ordering."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import adopt


def _personal_login(item_id: str, name: str, username: str, url: str,
                    fido2: list | None = None) -> dict:
    return {
        "id": item_id,
        "type": 1,
        "organizationId": None,
        "name": name,
        "login": {
            "username": username,
            "uris": [{"uri": url}] if url else [],
            "fido2Credentials": fido2 if fido2 is not None else [{"credentialId": "fakecred"}],
        },
    }


def _org_login(item_id: str, name: str, username: str, url: str) -> dict:
    return {
        "id": item_id,
        "type": 1,
        "organizationId": "org-uuid",
        "collectionIds": ["col-uuid"],
        "name": name,
        "login": {
            "username": username,
            "uris": [{"uri": url}] if url else [],
            "fido2Credentials": [],
        },
    }


class TestBuildPlan(unittest.TestCase):

    def test_exact_match(self):
        personal = [_personal_login("p1", "Google", "alice@example.com", "https://accounts.google.com")]
        org = [_org_login("o1", "Google", "alice@example.com", "https://accounts.google.com")]
        plan = adopt._build_plan(personal, org)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["personal_item"]["id"], "p1")
        self.assertEqual(plan[0]["org_item"]["id"], "o1")

    def test_host_url_match(self):
        personal = [_personal_login("p1", "Google", "alice@example.com", "https://accounts.google.com/signin")]
        org = [_org_login("o1", "Google", "alice@example.com", "https://accounts.google.com")]
        plan = adopt._build_plan(personal, org)
        self.assertEqual(len(plan), 1)

    def test_no_match_different_username(self):
        personal = [_personal_login("p1", "Google", "bob@example.com", "https://accounts.google.com")]
        org = [_org_login("o1", "Google", "alice@example.com", "https://accounts.google.com")]
        plan = adopt._build_plan(personal, org)
        self.assertEqual(len(plan), 0)

    def test_no_match_different_title(self):
        personal = [_personal_login("p1", "Gmail", "alice@example.com", "https://accounts.google.com")]
        org = [_org_login("o1", "Google", "alice@example.com", "https://accounts.google.com")]
        plan = adopt._build_plan(personal, org)
        self.assertEqual(len(plan), 0)

    def test_no_match_different_host(self):
        personal = [_personal_login("p1", "Google", "alice@example.com", "https://mail.google.com")]
        org = [_org_login("o1", "Google", "alice@example.com", "https://accounts.google.com")]
        plan = adopt._build_plan(personal, org)
        self.assertEqual(len(plan), 0)

    def test_org_item_not_matched_twice(self):
        personal = [
            _personal_login("p1", "Google", "alice@example.com", "https://accounts.google.com"),
            _personal_login("p2", "Google", "alice@example.com", "https://accounts.google.com"),
        ]
        org = [_org_login("o1", "Google", "alice@example.com", "https://accounts.google.com")]
        plan = adopt._build_plan(personal, org)
        self.assertEqual(len(plan), 1)

    def test_personal_without_fido2_excluded_from_plan(self):
        personal_no_fido = _personal_login("p1", "Google", "alice@example.com",
                                           "https://accounts.google.com", fido2=[])
        org = [_org_login("o1", "Google", "alice@example.com", "https://accounts.google.com")]
        plan = adopt._build_plan([personal_no_fido], org)
        self.assertEqual(len(plan), 0)


class TestApplyMatchDryRunSafety(unittest.TestCase):
    """Without --apply, _apply_match must never be called."""

    def test_dry_run_calls_no_edit_or_delete(self):
        personal = [_personal_login("p1", "Google", "alice@example.com", "https://accounts.google.com")]
        org = [_org_login("o1", "Google", "alice@example.com", "https://accounts.google.com")]

        with patch("adopt.bwcli") as mock_bw:
            mock_bw.check_prereqs.return_value = None
            mock_bw.list_org_collections.return_value = [{"id": "col-uuid", "name": "Employee"}]
            mock_bw.sync.return_value = None
            mock_bw.list_personal_items.return_value = personal
            mock_bw.list_items_in_collection.return_value = org

            plan = adopt._build_plan(personal, org)
            self.assertEqual(len(plan), 1)

            mock_bw.edit_item.assert_not_called()
            mock_bw.delete_item.assert_not_called()


class TestApplyMatchOrdering(unittest.TestCase):
    """delete_item must only be called after edit_item AND after successful get_item verify."""

    def _run_apply(self, mock_bw, personal_item, org_item) -> bool:
        match = {"personal_item": personal_item, "org_item": org_item}
        return adopt._apply_match(match)

    def test_success_ordering(self):
        personal = _personal_login("p1", "Google", "alice@example.com", "https://accounts.google.com")
        org = _org_login("o1", "Google", "alice@example.com", "https://accounts.google.com")

        call_order = []

        with patch("adopt.bwcli") as mock_bw:
            mock_bw.edit_item.side_effect = lambda *a, **kw: call_order.append("edit") or org
            mock_bw.get_item.side_effect = lambda *a, **kw: (
                call_order.append("get") or {
                    "login": {"fido2Credentials": [{"credentialId": "cred"}]}
                }
            )
            mock_bw.delete_item.side_effect = lambda *a, **kw: call_order.append("delete")

            result = self._run_apply(mock_bw, personal, org)

        self.assertTrue(result)
        self.assertEqual(call_order, ["edit", "get", "delete"])

    def test_no_delete_if_edit_fails(self):
        from lib.bwcli import BWError
        personal = _personal_login("p1", "Google", "alice@example.com", "https://accounts.google.com")
        org = _org_login("o1", "Google", "alice@example.com", "https://accounts.google.com")

        with patch("adopt.bwcli") as mock_bw:
            mock_bw.BWError = BWError
            mock_bw.edit_item.side_effect = BWError("server refused")

            result = self._run_apply(mock_bw, personal, org)

        self.assertFalse(result)
        mock_bw.delete_item.assert_not_called()

    def test_no_delete_if_verify_fails(self):
        from lib.bwcli import BWError
        personal = _personal_login("p1", "Google", "alice@example.com", "https://accounts.google.com")
        org = _org_login("o1", "Google", "alice@example.com", "https://accounts.google.com")

        with patch("adopt.bwcli") as mock_bw:
            mock_bw.BWError = BWError
            mock_bw.edit_item.return_value = org
            mock_bw.get_item.return_value = {"login": {"fido2Credentials": []}}

            result = self._run_apply(mock_bw, personal, org)

        self.assertFalse(result)
        mock_bw.delete_item.assert_not_called()

    def test_no_delete_if_get_item_raises(self):
        from lib.bwcli import BWError
        personal = _personal_login("p1", "Google", "alice@example.com", "https://accounts.google.com")
        org = _org_login("o1", "Google", "alice@example.com", "https://accounts.google.com")

        with patch("adopt.bwcli") as mock_bw:
            mock_bw.BWError = BWError
            mock_bw.edit_item.return_value = org
            mock_bw.get_item.side_effect = BWError("network error")

            result = self._run_apply(mock_bw, personal, org)

        self.assertFalse(result)
        mock_bw.delete_item.assert_not_called()

    def test_edit_called_with_fido2_credentials(self):
        personal = _personal_login("p1", "Google", "alice@example.com", "https://accounts.google.com",
                                   fido2=[{"credentialId": "test-cred"}])
        org = _org_login("o1", "Google", "alice@example.com", "https://accounts.google.com")

        with patch("adopt.bwcli") as mock_bw:
            from lib.bwcli import BWError
            mock_bw.BWError = BWError
            edited_items = []
            mock_bw.edit_item.side_effect = lambda item_id, item: edited_items.append(item) or item
            mock_bw.get_item.return_value = {"login": {"fido2Credentials": [{"credentialId": "test-cred"}]}}
            mock_bw.delete_item.return_value = None

            self._run_apply(mock_bw, personal, org)

        self.assertEqual(len(edited_items), 1)
        fido2 = (edited_items[0].get("login") or {}).get("fido2Credentials")
        self.assertEqual(fido2, [{"credentialId": "test-cred"}])

    def test_soft_delete_not_permanent(self):
        """delete_item must be called without --permanent."""
        personal = _personal_login("p1", "Google", "alice@example.com", "https://accounts.google.com")
        org = _org_login("o1", "Google", "alice@example.com", "https://accounts.google.com")

        with patch("adopt.bwcli") as mock_bw:
            from lib.bwcli import BWError
            mock_bw.BWError = BWError
            mock_bw.edit_item.return_value = org
            mock_bw.get_item.return_value = {"login": {"fido2Credentials": [{}]}}
            mock_bw.delete_item.return_value = None

            self._run_apply(mock_bw, personal, org)

        mock_bw.delete_item.assert_called_once_with("p1")


if __name__ == "__main__":
    unittest.main()
