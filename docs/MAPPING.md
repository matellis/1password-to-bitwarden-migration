# Field Mapping: 1Password → Bitwarden

## Category mapping

Verified against a real 1Password 8 export, 2026-09-05.

| 1Password categoryUuid | 1Password type | Bitwarden type | Notes |
|---|---|---|---|
| 001 | Login | 1 Login | |
| 005 | Password | 1 Login | treated as login with password field only |
| 112 | API Credential | 1 Login | section fields `username`/`credential` map to `login.username`/`login.password`; other fields as custom |
| 002 | Credit Card | 3 Card | standard card fields mapped; others → custom |
| 003 | Secure Note | 2 SecureNote | no fields / free-form |
| 004 | Identity | 4 Identity | standard identity fields mapped; others → custom |
| 006 | Document | 2 SecureNote + attachment | item created individually; file uploaded |
| 114 | SSH Key | 5 SshKey (bw ≥ 2024) | private/public key + fingerprint pulled from a single `sshKey`-typed field |
| 100–111, 113, and unknown/missing | Software License, Bank Account, Database, Driver License, Outdoor License, Membership, Passport, Rewards, SSN, Wireless Router, Server, Email Account, Medical Record, etc. | 2 SecureNote | all fields as custom fields |

## Login fields

| 1Password | Bitwarden |
|---|---|
| `loginFields[designation=username].value` | `login.username` |
| `loginFields[designation=password].value` | `login.password` |
| Other `loginFields` | custom text fields |
| `overview.urls[].url` | `login.uris[].uri` (match: null) |
| `details.notesPlain` | `notes` |
| `overview.tags[]` | appended to `notes` as `Tags: a, b` |
| `favIndex > 0` | `favorite: true` |
| `details.passwordHistory[]` | `passwordHistory[].{lastUsedDate, password}` |

## Section field types → Bitwarden field types

| 1Password value type | Bitwarden field type | Notes |
|---|---|---|
| `string` | 0 Text | |
| `concealed` | 1 Hidden | |
| `totp` | `login.totp` (logins) or 1 Hidden | full otpauth:// URI passed through |
| `url` | 0 Text | |
| `email` | 0 Text | |
| `phone` | 0 Text | |
| `date` | 0 Text | Unix timestamp → YYYY-MM-DD |
| `monthYear` | 0 Text | YYYYMM → MM/YYYY |
| `creditCardNumber` | 0 Text (or card.number for 002 items) | |
| `menu` | 0 Text | |
| `address` | 0 Text | street, city, state, zip, country joined |
| `gender` | 0 Text | |
| `reference` | attachment job | item excluded from bulk; file uploaded individually |
| `file` | attachment job | item excluded from bulk; file uploaded individually (value is `{documentId, fileName, decryptedSize}`) |
| `sshKey` | consumed by `_fill_ssh_key` | never becomes a custom field; see "SSH key mapping" below |
| unknown | 0 Text | str() of the value |

Section field name format: `<Section Title>: <Field Title>` (or just the field title if no section title).

## Credit card field mapping

| 1Password field ID | Bitwarden card field |
|---|---|
| `cardholder` | `cardholderName` |
| `ccnum` | `number` |
| `cvv` | `code` |
| `expiry` (monthYear) | `expMonth` + `expYear` |
| `type` | `brand` |
| All other fields | custom fields |

## Identity field mapping

| 1Password field ID | Bitwarden identity field |
|---|---|
| `firstname` | `firstName` |
| `lastname` | `lastName` |
| `middlename` / `initial` | `middleName` |
| `title` | `title` |
| `username` | `username` |
| `company` | `company` |
| `email` | `email` |
| `defphone` / `phone` / `cellphone` | `phone` |
| `ssn` | `ssn` |
| `passportno` | `passportNumber` |
| `driverlicno` | `licenseNumber` |
| `address` (address type) | `address1`, `city`, `state`, `postalCode`, `country` |
| `address1` | `address1` |
| `address2` | `address2` |
| `city` | `city` |
| `state` | `state` |
| `zip` | `postalCode` |
| `country` | `country` |
| All other fields | custom fields |

## SSH key mapping (category 114)

The private key, public key, and fingerprint all live in a single section field
whose value is `{"sshKey": {"privateKey": "...", "metadata": {"privateKey", "publicKey", "fingerprint", "keyType"}}}`.
Values are read from `metadata` first, falling back to the top-level `privateKey`
key if `metadata` is missing or incomplete:

| Source | Bitwarden sshKey field |
|---|---|
| `sshKey.metadata.privateKey` (fallback: `sshKey.privateKey`) | `privateKey` |
| `sshKey.metadata.publicKey` | `publicKey` |
| `sshKey.metadata.fingerprint` | `keyFingerprint` |
| All other (non-`sshKey`-typed) section fields | custom fields |

Falls back to SecureNote + hidden custom fields (`Private Key`, `Public Key`,
`Key Fingerprint`) when `bw` does not support type 5.

## Oversized field / notes enforcement

Bitwarden rejects a whole vault import if any custom field value exceeds 5,000 characters (encrypted) or notes exceed 10,000 characters. Every custom field, including "other `loginFields`" above, is enforced against a 4,900-char safe margin before being written:

| Condition | Result |
|---|---|
| Field value ≤ 4,900 chars | unchanged |
| Field value > 4,900 chars, and relocating it keeps notes ≤ 9,900 chars | full value appended to `notes` as `<field name>:\n<value>`; field value replaced with a pointer string |
| Field value > 4,900 chars, but notes have no room left | field value truncated to 4,900 chars + `…[truncated from N characters: exceeded Bitwarden limits]` |
| Final assembled `notes` > 9,900 chars | truncated to 9,900 chars + `…[truncated: exceeded Bitwarden's 10000-character notes limit]` |

`split.py` reports one warning line per relocated/truncated field to stderr after conversion.
