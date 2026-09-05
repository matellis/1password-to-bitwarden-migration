#!/usr/bin/env python3
"""Generate a spec-faithful synthetic .1pux for testing.

Creates tests/fixture.1pux (gitignored) containing:
  - A Personal vault (type P) with one login — must be skipped by split.py
  - An Employee vault (type E) with:
      * Login with TOTP, multiple URLs, tags, custom section fields
      * Secure note
      * Credit card
      * An archived login (must be skipped by default)
      * A login with a reference-type field (attachment reference)
  - A Shared vault (type U) with:
      * Document item (category 006) with a real file in files/

Usage:
    python3 tests/make_fixture.py
    # => tests/fixture.1pux

Then test:
    python3 split.py --config tests/fixture_config.json --account test
"""

from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from pathlib import Path

FIXTURE_PATH = Path("tests/fixture.1pux")
FIXTURE_CONFIG_PATH = Path("tests/fixture_config.json")

DOC_ID = "aabbccdd11223344aabbccdd11223344"
DOC_FILENAME = "important.txt"
DOC_CONTENT = b"This is a test document attachment.\n"

REF_ITEM_ID = "ffffffffffffffffffffffffffffffff"

EXPORT_ATTRIBUTES = {
    "version": "3",
    "description": "synthetic fixture for testing",
    "createdAt": 1700000000,
}

EXPORT_DATA = {
    "accounts": [
        {
            "attrs": {
                "accountName": "Test Family",
                "name": "Test Family",
                "avatar": "",
                "email": "family@example.com",
                "uuid": "testacc0000000000000000000000001",
                "domain": "my.1password.com",
            },
            "vaults": [
                {
                    "attrs": {
                        "uuid": "pvault000000000000000000000000001",
                        "desc": "Personal vault — should be skipped",
                        "avatar": "default",
                        "name": "Personal",
                        "type": "P",
                    },
                    "items": [
                        {
                            "uuid": "privatitem0000000000000000000001",
                            "favIndex": 0,
                            "createdAt": 1700000001,
                            "updatedAt": 1700000001,
                            "state": "active",
                            "categoryUuid": "001",
                            "details": {
                                "loginFields": [
                                    {"value": "private@example.com", "id": "username", "name": "username", "fieldType": "T", "designation": "username"},
                                    {"value": "PrivatePass1!", "id": "password", "name": "password", "fieldType": "P", "designation": "password"},
                                ],
                                "notesPlain": "",
                                "sections": [
                                    {
                                        "title": "Two-Factor Auth",
                                        "name": "totp_section",
                                        "fields": [
                                            {
                                                "title": "TOTP",
                                                "id": "priv_totp",
                                                "value": {"totp": "otpauth://totp/Private:private@example.com?secret=JBSWY3DPEHPK3PXP&issuer=Private"},
                                                "indexInSection": 0,
                                            },
                                        ],
                                    },
                                ],
                                "passwordHistory": [
                                    {"value": "OldPrivate1!", "time": 1699000001},
                                ],
                            },
                            "overview": {
                                "title": "Private Login",
                                "urls": [{"label": "website", "url": "https://private.example.com"}],
                                "tags": ["personal"],
                            },
                        },
                        {
                            "uuid": "privnote000000000000000000000001",
                            "favIndex": 0,
                            "createdAt": 1700000050,
                            "updatedAt": 1700000050,
                            "state": "active",
                            "categoryUuid": "002",
                            "details": {
                                "loginFields": [],
                                "notesPlain": "Wi-Fi password: hunter2\nRouter IP: 192.168.1.1",
                                "sections": [],
                                "passwordHistory": [],
                            },
                            "overview": {
                                "title": "Home Network",
                                "tags": [],
                            },
                        },
                        {
                            "uuid": "privcard000000000000000000000001",
                            "favIndex": 0,
                            "createdAt": 1700000060,
                            "updatedAt": 1700000060,
                            "state": "active",
                            "categoryUuid": "003",
                            "details": {
                                "loginFields": [],
                                "notesPlain": "",
                                "sections": [
                                    {
                                        "title": "",
                                        "name": "priv_card_section",
                                        "fields": [
                                            {"title": "Cardholder Name", "id": "cardholder", "value": {"string": "Pat Example"}, "indexInSection": 0},
                                            {"title": "Card Number", "id": "ccnum", "value": {"creditCardNumber": "5500005555555559"}, "indexInSection": 1},
                                            {"title": "CVV", "id": "cvv", "value": {"concealed": "737"}, "indexInSection": 2},
                                            {"title": "Expiry Date", "id": "expiry", "value": {"monthYear": 202812}, "indexInSection": 3},
                                            {"title": "Type", "id": "type", "value": {"string": "mc"}, "indexInSection": 4},
                                        ],
                                    }
                                ],
                                "passwordHistory": [],
                            },
                            "overview": {
                                "title": "Personal Mastercard",
                                "tags": [],
                            },
                        },
                        {
                            "uuid": "privarch000000000000000000000001",
                            "favIndex": 0,
                            "createdAt": 1698000000,
                            "updatedAt": 1698500000,
                            "state": "archived",
                            "categoryUuid": "001",
                            "details": {
                                "loginFields": [
                                    {"value": "old-personal@example.com", "id": "username", "name": "username", "fieldType": "T", "designation": "username"},
                                    {"value": "OldPrivPass!", "id": "password", "name": "password", "fieldType": "P", "designation": "password"},
                                ],
                                "notesPlain": "Old account, closed.",
                                "sections": [],
                                "passwordHistory": [],
                            },
                            "overview": {
                                "title": "Archived Personal Login",
                                "urls": [],
                                "tags": [],
                            },
                        },
                    ],
                },
                {
                    "attrs": {
                        "uuid": "evault000000000000000000000000001",
                        "desc": "Employee vault",
                        "avatar": "default",
                        "name": "Employee",
                        "type": "E",
                    },
                    "items": [
                        {
                            "uuid": "loginitem000000000000000000000001",
                            "favIndex": 1,
                            "createdAt": 1700000100,
                            "updatedAt": 1700000200,
                            "state": "active",
                            "categoryUuid": "001",
                            "details": {
                                "loginFields": [
                                    {"value": "alice@example.com", "id": "username", "name": "username", "fieldType": "T", "designation": "username"},
                                    {"value": "Sup3rS3cr3t!", "id": "password", "name": "password", "fieldType": "P", "designation": "password"},
                                ],
                                "notesPlain": "Remember to rotate quarterly.",
                                "sections": [
                                    {
                                        "title": "Two-Factor Auth",
                                        "name": "twofa_section",
                                        "fields": [
                                            {
                                                "title": "TOTP",
                                                "id": "totp_field",
                                                "value": {"totp": "otpauth://totp/Example:alice@example.com?secret=JBSWY3DPEHPK3PXP&issuer=Example"},
                                                "indexInSection": 0,
                                            },
                                        ],
                                    },
                                    {
                                        "title": "Extra Info",
                                        "name": "extra_section",
                                        "fields": [
                                            {
                                                "title": "Recovery Code",
                                                "id": "recovery_field",
                                                "value": {"concealed": "ABCD-EFGH-IJKL"},
                                                "indexInSection": 0,
                                            },
                                            {
                                                "title": "Department",
                                                "id": "dept_field",
                                                "value": {"string": "Engineering"},
                                                "indexInSection": 1,
                                            },
                                        ],
                                    },
                                ],
                                "passwordHistory": [
                                    {"value": "OldPass99!", "time": 1699000000},
                                ],
                            },
                            "overview": {
                                "title": "Work SSO Portal",
                                "subtitle": "alice@example.com",
                                "urls": [
                                    {"label": "website", "url": "https://sso.example.com"},
                                    {"label": "mobile", "url": "https://m.sso.example.com"},
                                ],
                                "tags": ["work", "sso"],
                            },
                        },
                        {
                            "uuid": "noteitem000000000000000000000001",
                            "favIndex": 0,
                            "createdAt": 1700001000,
                            "updatedAt": 1700001000,
                            "state": "active",
                            "categoryUuid": "002",
                            "details": {
                                "loginFields": [],
                                "notesPlain": "This is a confidential note.\nLine two.",
                                "sections": [
                                    {
                                        "title": "Details",
                                        "name": "details_section",
                                        "fields": [
                                            {
                                                "title": "Access Level",
                                                "id": "access_field",
                                                "value": {"string": "Admin"},
                                                "indexInSection": 0,
                                            },
                                        ],
                                    }
                                ],
                                "passwordHistory": [],
                            },
                            "overview": {
                                "title": "Confidential Note",
                                "tags": [],
                            },
                        },
                        {
                            "uuid": "carditem000000000000000000000001",
                            "favIndex": 0,
                            "createdAt": 1700002000,
                            "updatedAt": 1700002000,
                            "state": "active",
                            "categoryUuid": "003",
                            "details": {
                                "loginFields": [],
                                "notesPlain": "",
                                "sections": [
                                    {
                                        "title": "",
                                        "name": "card_section",
                                        "fields": [
                                            {"title": "Cardholder Name", "id": "cardholder", "value": {"string": "Alice Smith"}, "indexInSection": 0},
                                            {"title": "Card Number", "id": "ccnum", "value": {"creditCardNumber": "4111111111111111"}, "indexInSection": 1},
                                            {"title": "CVV", "id": "cvv", "value": {"concealed": "123"}, "indexInSection": 2},
                                            {"title": "Expiry Date", "id": "expiry", "value": {"monthYear": 202712}, "indexInSection": 3},
                                            {"title": "Type", "id": "type", "value": {"string": "visa"}, "indexInSection": 4},
                                        ],
                                    }
                                ],
                                "passwordHistory": [],
                            },
                            "overview": {
                                "title": "Work Visa Card",
                                "tags": [],
                            },
                        },
                        {
                            "uuid": "archiveitem0000000000000000000001",
                            "favIndex": 0,
                            "createdAt": 1699000000,
                            "updatedAt": 1699500000,
                            "state": "archived",
                            "categoryUuid": "001",
                            "details": {
                                "loginFields": [
                                    {"value": "old@example.com", "id": "username", "name": "username", "fieldType": "T", "designation": "username"},
                                    {"value": "OldPass!", "id": "password", "name": "password", "fieldType": "P", "designation": "password"},
                                ],
                                "notesPlain": "Decommissioned service.",
                                "sections": [],
                                "passwordHistory": [],
                            },
                            "overview": {
                                "title": "Old Service Login",
                                "urls": [],
                                "tags": [],
                            },
                        },
                        {
                            "uuid": "refitem000000000000000000000000001",
                            "favIndex": 0,
                            "createdAt": 1700003000,
                            "updatedAt": 1700003000,
                            "state": "active",
                            "categoryUuid": "001",
                            "details": {
                                "loginFields": [
                                    {"value": "bob@example.com", "id": "username", "name": "username", "fieldType": "T", "designation": "username"},
                                    {"value": "BobPass99!", "id": "password", "name": "password", "fieldType": "P", "designation": "password"},
                                ],
                                "notesPlain": "",
                                "sections": [
                                    {
                                        "title": "Attachments",
                                        "name": "attach_section",
                                        "fields": [
                                            {
                                                "title": "Key File",
                                                "id": "keyfile_field",
                                                "value": {"reference": REF_ITEM_ID},
                                                "indexInSection": 0,
                                            },
                                        ],
                                    }
                                ],
                                "passwordHistory": [],
                            },
                            "overview": {
                                "title": "Service With Key File",
                                "urls": [{"label": "website", "url": "https://keyservice.example.com"}],
                                "tags": [],
                            },
                        },
                    ],
                },
                {
                    "attrs": {
                        "uuid": "uvault000000000000000000000000001",
                        "desc": "Shared vault",
                        "avatar": "default",
                        "name": "Shared",
                        "type": "U",
                    },
                    "items": [
                        {
                            "uuid": "docitem0000000000000000000000001",
                            "favIndex": 0,
                            "createdAt": 1700004000,
                            "updatedAt": 1700004000,
                            "state": "active",
                            "categoryUuid": "006",
                            "details": {
                                "loginFields": [],
                                "notesPlain": "Important document for the team.",
                                "sections": [],
                                "passwordHistory": [],
                                "documentAttributes": {
                                    "documentId": DOC_ID,
                                    "fileName": DOC_FILENAME,
                                },
                            },
                            "overview": {
                                "title": "Team Document",
                                "tags": ["shared"],
                            },
                        }
                    ],
                },
            ],
        }
    ]
}


def build_fixture() -> None:
    FIXTURE_PATH.parent.mkdir(exist_ok=True)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("export.attributes", json.dumps(EXPORT_ATTRIBUTES))
        zf.writestr("export.data", json.dumps(EXPORT_DATA))
        zf.writestr(f"files/{DOC_ID}___{DOC_FILENAME}", DOC_CONTENT)
        zf.writestr(f"files/{REF_ITEM_ID}___keyfile.pem", b"-----BEGIN RSA KEY-----\ntest\n-----END RSA KEY-----\n")

    FIXTURE_PATH.write_bytes(buf.getvalue())
    print(f"Wrote {FIXTURE_PATH} ({FIXTURE_PATH.stat().st_size} bytes)")

    fixture_config = {
        "accounts": [
            {
                "name": "test",
                "puxPath": str(FIXTURE_PATH),
                "bitwardenOrgId": "00000000-0000-0000-0000-000000000001",
                "onExisting": "refuse",
                "skipVaultTypes": ["P"],
                "vaultRename": {},
            }
        ]
    }
    FIXTURE_CONFIG_PATH.write_text(json.dumps(fixture_config, indent=2))
    print(f"Wrote {FIXTURE_CONFIG_PATH}")


if __name__ == "__main__":
    build_fixture()
