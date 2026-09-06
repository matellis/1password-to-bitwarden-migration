# Format Notes

## 1pux zip layout

Spec: https://support.1password.com/1pux-format/

A `.1pux` file is a ZIP archive containing:

```
export.attributes   — JSON: version, description, createdAt
export.data         — JSON tree (see below)
files/              — directory of extracted file attachments
  <documentId>___<original filename>
```

`export.data` structure:
```
{
  "accounts": [
    {
      "attrs": { "accountName", "name", "email", "uuid", "domain", ... },
      "vaults": [
        {
          "attrs": { "uuid", "desc", "name", "type": "P"|"E"|"U"|... },
          "items": [ <item>, ... ]
        }
      ]
    }
  ]
}
```

Vault types observed: `P` (Personal/Private), `E` (Employee), `U` (User-shared/Team).

### Item structure

```json
{
  "uuid": "...",
  "favIndex": 0,
  "createdAt": 1234567890,
  "updatedAt": 1234567890,
  "state": "active" | "archived",
  "categoryUuid": "001",
  "details": {
    "loginFields": [ { "value", "id", "name", "fieldType", "designation" } ],
    "notesPlain": "...",
    "sections": [
      {
        "title": "Section Name",
        "name": "section_uuid",
        "fields": [
          { "title", "id", "value": <typed value>, "indexInSection" }
        ]
      }
    ],
    "passwordHistory": [ { "value", "time" } ],
    "documentAttributes": { "documentId": "...", "fileName": "..." }
  },
  "overview": {
    "title": "Item Name",
    "subtitle": "...",
    "urls": [ { "label", "url" } ],
    "tags": [ "tag1", "tag2" ],
    "favIndex": 0
  }
}
```

### Typed field values

Section field values are a single-key dict indicating the type:

| Key | Value type | Notes |
|---|---|---|
| `string` | str | generic text |
| `concealed` | str | secret; → BW hidden field |
| `totp` | str | otpauth:// URI or bare seed |
| `url` | str | |
| `email` | str | |
| `phone` | str | |
| `date` | int | Unix timestamp |
| `monthYear` | int | YYYYMM |
| `creditCardNumber` | str | |
| `menu` | str | selected option |
| `address` | dict | `{street, city, state, zip, country}` |
| `gender` | str | |
| `reference` | str | UUID of another item (file attachment reference) |
| `file` | dict | `{documentId, fileName, decryptedSize}` — file attachment reference, resolved via `files/<documentId>___<name>` |
| `sshKey` | dict | `{privateKey, metadata: {privateKey, publicKey, fingerprint, keyType}}` — real value in `metadata`, top-level `privateKey` is a fallback |

Unknown keys are fallen back to `str()` and stored as text custom fields.

## Bitwarden org-import JSON

Reference: https://bitwarden.com/help/condition-bitwarden-import/

Import format `bitwardenjson`:

```json
{
  "encrypted": false,
  "collections": [
    { "id": "<uuid4>", "organizationId": "...", "name": "...", "externalId": null }
  ],
  "items": [
    {
      "id": null,
      "organizationId": "...",
      "collectionIds": ["<collection uuid>"],
      "type": 1,
      "name": "...",
      "notes": null,
      "favorite": false,
      "login": { "username": null, "password": null, "totp": null, "uris": [] },
      "fields": [ { "name": "...", "value": "...", "type": 0 } ],
      "passwordHistory": [ { "lastUsedDate": "ISO8601Z", "password": "..." } ],
      "reprompt": 0
    }
  ]
}
```

Item types: 1=Login, 2=SecureNote, 3=Card, 4=Identity, 5=SshKey.
Field types: 0=Text, 1=Hidden, 2=Boolean.

### Quirks found during development

- `bw import` creates the collection if the `id` + `name` don't already exist in the org. If the name collides with an existing collection the behavior is merge (all items land in the existing collection). The `onExisting` policy in this tool guards against unintended merges.
- `bw list items --collectionid` takes a single collection UUID (confirmed via `bw list --help`, bw 2026.8.0). Not `--collectionIds` (plural).
- `bw import --formats` requires login; format name `bitwardenjson` is documented.
- SSH key type 5 is supported in bw 2026.8.0.
- `bw create item` takes base64-encoded JSON as a positional argument, not stdin (though `echo ... | bw create item` also works). `--raw` returns the created item's JSON.
- TOTP values in Bitwarden accept the full `otpauth://` URI or a bare base32 seed. We pass through the full URI when present.
