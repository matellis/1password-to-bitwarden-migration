"""Tests for the 1Password categoryUuid -> Bitwarden type mapping (lib/onepux.py).

Ground truth verified against a real 1Password 8 export, 2026-09-05. See
docs/MAPPING.md for the full table.
"""
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from lib import onepux


def _base_item(cat: str, title: str = "Item") -> dict:
    return {
        "state": "active",
        "categoryUuid": cat,
        "overview": {"title": title, "urls": []},
        "details": {
            "notesPlain": "",
            "loginFields": [],
            "sections": [],
            "passwordHistory": [],
        },
    }


class TestApiCredentialCategory(unittest.TestCase):
    """categoryUuid 112 is API Credential -> Login, not SSH key."""

    def test_api_credential_maps_to_login_with_username_and_password(self):
        raw = _base_item("112", "Example API Credential")
        raw["details"]["sections"] = [
            {
                "title": "",
                "fields": [
                    {"title": "username", "id": "username", "value": {"string": "svc-account"}},
                    {"title": "credential", "id": "credential", "value": {"concealed": "sk-example-123"}},
                    {"title": "Hostname", "id": "hostname", "value": {"string": "api.example.com"}},
                ],
            }
        ]
        result = onepux.convert_vault_items([raw], "org-1", "coll-1", {})
        self.assertEqual(len(result.bulk_items), 1)
        item = result.bulk_items[0]
        self.assertEqual(item["type"], 1)
        self.assertEqual(item["login"]["username"], "svc-account")
        self.assertEqual(item["login"]["password"], "sk-example-123")
        hostname_field = next(f for f in item["fields"] if "Hostname" in f["name"])
        self.assertEqual(hostname_field["value"], "api.example.com")

    def test_api_credential_loginfields_take_precedence(self):
        """Spec says 112 items have empty loginFields, but if present, prefer them."""
        raw = _base_item("112", "Weird API Credential")
        raw["details"]["loginFields"] = [
            {"designation": "username", "value": "from-loginfields"},
        ]
        raw["details"]["sections"] = [
            {
                "title": "",
                "fields": [
                    {"title": "username", "id": "username", "value": {"string": "from-section"}},
                ],
            }
        ]
        result = onepux.convert_vault_items([raw], "org-1", "coll-1", {})
        item = result.bulk_items[0]
        self.assertEqual(item["login"]["username"], "from-loginfields")


class TestSshKeyCategory(unittest.TestCase):
    """categoryUuid 114 is SSH Key -> Bitwarden type 5."""

    def _ssh_item(self) -> dict:
        raw = _base_item("114", "Example SSH Key")
        raw["details"]["sections"] = [
            {
                "title": "",
                "fields": [
                    {
                        "title": "private key",
                        "id": "private_key",
                        "value": {
                            "sshKey": {
                                "privateKey": "TOP-LEVEL-FALLBACK-KEY",
                                "metadata": {
                                    "privateKey": "-----BEGIN OPENSSH PRIVATE KEY-----\ntest\n-----END OPENSSH PRIVATE KEY-----\n",
                                    "publicKey": "ssh-rsa AAAAB3NzaC1yc2E test@example.com",
                                    "fingerprint": "SHA256:abc123examplefingerprint",
                                    "keyType": "rsa",
                                },
                            }
                        },
                    },
                ],
            }
        ]
        return raw

    def test_ssh_key_maps_to_type_5_with_real_key_data(self):
        raw = self._ssh_item()
        result = onepux.convert_vault_items([raw], "org-1", "coll-1", {}, ssh_key_supported=True)
        self.assertEqual(len(result.bulk_items), 1)
        item = result.bulk_items[0]
        self.assertEqual(item["type"], 5)
        self.assertEqual(
            item["sshKey"]["privateKey"],
            "-----BEGIN OPENSSH PRIVATE KEY-----\ntest\n-----END OPENSSH PRIVATE KEY-----\n",
        )
        self.assertEqual(item["sshKey"]["publicKey"], "ssh-rsa AAAAB3NzaC1yc2E test@example.com")
        self.assertEqual(item["sshKey"]["keyFingerprint"], "SHA256:abc123examplefingerprint")

    def test_ssh_key_falls_back_to_top_level_private_key_if_metadata_incomplete(self):
        raw = self._ssh_item()
        raw["details"]["sections"][0]["fields"][0]["value"]["sshKey"]["metadata"] = {}
        result = onepux.convert_vault_items([raw], "org-1", "coll-1", {}, ssh_key_supported=True)
        item = result.bulk_items[0]
        self.assertEqual(item["sshKey"]["privateKey"], "TOP-LEVEL-FALLBACK-KEY")

    def test_ssh_key_falls_back_to_secure_note_with_hidden_fields_when_unsupported(self):
        raw = self._ssh_item()
        result = onepux.convert_vault_items([raw], "org-1", "coll-1", {}, ssh_key_supported=False)
        self.assertEqual(len(result.bulk_items), 1)
        item = result.bulk_items[0]
        self.assertEqual(item["type"], 2)
        self.assertNotIn("sshKey", item)
        by_name = {f["name"]: f for f in item["fields"]}
        self.assertEqual(
            by_name["Private Key"]["value"],
            "-----BEGIN OPENSSH PRIVATE KEY-----\ntest\n-----END OPENSSH PRIVATE KEY-----\n",
        )
        self.assertEqual(by_name["Private Key"]["type"], 1)
        self.assertEqual(by_name["Public Key"]["value"], "ssh-rsa AAAAB3NzaC1yc2E test@example.com")
        self.assertEqual(by_name["Public Key"]["type"], 1)
        self.assertEqual(by_name["Key Fingerprint"]["value"], "SHA256:abc123examplefingerprint")
        self.assertEqual(by_name["Key Fingerprint"]["type"], 1)


class TestFileTypedFieldAttachment(unittest.TestCase):
    def test_file_typed_field_becomes_attachment(self):
        doc_id = "eeddccbbaa99887766eeddccbbaa9988"
        raw = _base_item("114", "SSH Key With Public Key File")
        raw["details"]["sections"] = [
            {
                "title": "",
                "fields": [
                    {
                        "title": "private key",
                        "id": "private_key",
                        "value": {"sshKey": {"privateKey": "pk", "metadata": {}}},
                    },
                    {
                        "title": "public key file",
                        "id": "pubkey_file",
                        "value": {"file": {"documentId": doc_id, "fileName": "id_rsa.pub", "decryptedSize": 5}},
                    },
                ],
            }
        ]
        fake_path = Path("/tmp/id_rsa.pub")
        files_map = {doc_id: fake_path}
        result = onepux.convert_vault_items([raw], "org-1", "coll-1", files_map)
        self.assertEqual(result.bulk_items, [])
        self.assertEqual(len(result.attachment_items), 1)
        self.assertEqual(result.attachment_items[0]["files"], [fake_path])
        item = result.attachment_items[0]["item"]
        self.assertFalse(
            any("documentId" in str(f.get("value")) for f in item["fields"]),
            "file-typed field must not leak into a custom field",
        )


class TestUnknownAndMissingCategory(unittest.TestCase):
    def test_unknown_category_maps_to_secure_note_with_custom_fields(self):
        raw = _base_item("100", "Software License")
        raw["details"]["sections"] = [
            {
                "title": "",
                "fields": [
                    {"title": "License Key", "id": "license_key", "value": {"string": "ABCD-1234"}},
                ],
            }
        ]
        result = onepux.convert_vault_items([raw], "org-1", "coll-1", {})
        item = result.bulk_items[0]
        self.assertEqual(item["type"], 2)
        field = next(f for f in item["fields"] if "License Key" in f["name"])
        self.assertEqual(field["value"], "ABCD-1234")

    def test_another_unknown_category_maps_to_secure_note(self):
        raw = _base_item("111", "Membership")
        result = onepux.convert_vault_items([raw], "org-1", "coll-1", {})
        self.assertEqual(result.bulk_items[0]["type"], 2)

    def test_missing_category_uuid_defaults_to_secure_note_not_card(self):
        raw = _base_item("001", "Whatever")
        del raw["categoryUuid"]
        result = onepux.convert_vault_items([raw], "org-1", "coll-1", {})
        item = result.bulk_items[0]
        self.assertEqual(item["type"], 2)
        self.assertNotIn("card", item)


if __name__ == "__main__":
    unittest.main()
