# 1Password → Bitwarden Migration

A small, stdlib-only Python toolkit that migrates 1Password `.1pux` exports to Bitwarden **without losing vault structure**.

Bitwarden's own JSON importer collapses all vaults into a flat collection (open bug: [bitwarden/clients#20724](https://github.com/bitwarden/clients/issues/20724)). This tool splits the export per vault, imports each vault into its own Bitwarden organization collection, and then verifies the copy field-by-field.

**Python 3.9+. No pip dependencies.**

## What it migrates

- All non-Private vaults (Employee, Shared, and other named vaults)
- Logins with username, password, TOTP, URLs, and custom fields
- Secure notes, credit cards, identities, SSH keys
- File attachments and document items
- Tags (appended to notes — Bitwarden org items have no native tags)
- Password history (best effort)

## What it does not migrate

See [docs/LIMITATIONS.md](docs/LIMITATIONS.md) for the full list.  The short version: passkeys, item history beyond password fields, ACLs/sharing permissions, Private vaults (each user migrates their own).

## Prerequisites

- Python 3.9 or later
- [Bitwarden CLI](https://bitwarden.com/help/cli/) (`bw`) installed and logged in
- A Bitwarden organization per account you want to migrate to
- A `.1pux` export from 1Password (File → Export → All Items → 1PUX format)

## Quickstart

```bash
git clone https://github.com/matellis/1password-to-bitwarden-migration
cd 1password-to-bitwarden-migration

cp config.example.json config.json
# Edit config.json with your account details

mkdir -p exports/
# Place your .1pux files in exports/

# Step 1 — inspect the plan (no writes anywhere)
python3 split.py

# Step 2 — import (requires bw session)
export BW_SESSION="$(bw unlock --raw)"
python3 import.py --account family

# Step 3 — verify
python3 verify.py --account family
```

## Three-account walkthrough

The exemplar scenario has three accounts: **family**, **team**, and **individual**.

### 1. Create Bitwarden organizations

For each 1Password account, create a Bitwarden organization.  Note the organization UUID from the Bitwarden admin console and put it in `config.json` as `bitwardenOrgId`.

### 2. Export from 1Password

In 1Password: **File → Export → All Items → 1PUX format**.  Do this per account.

Save exports to the `exports/` directory (gitignored).

### 3. Configure

```bash
cp config.example.json config.json
```

Fill in:
- `puxPath` — path to each `.1pux` file
- `bitwardenOrgId` — Bitwarden org UUID for that account
- `onExisting` — what to do if a collection already has items (`refuse`, `skip`, `allow`)
- `skipVaultTypes` — vault types to ignore (default `["P"]` skips Private vaults)
- `vaultRename` — optional map of `{"1Password vault name": "Bitwarden collection name"}`

### 4. Split (offline, safe to re-run)

```bash
python3 split.py
```

This reads the `.1pux` files and writes `work/<account>/` with one JSON file per vault plus an attachments manifest.  **Nothing is written to Bitwarden.**  Review the table it prints before proceeding.

### 5. Import (one account at a time)

```bash
export BW_SESSION="$(bw unlock --raw)"
python3 import.py --account family
```

The tool checks the `state/<account>.json` ledger and skips vaults already imported.  Use `--force` to re-import.

### 6. Verify

```bash
python3 verify.py --account family
```

Compares source 1pux data field-by-field against live Bitwarden data.  Exits 0 on full pass.

### 7. Invite users and assign permissions

1Password exports contain no ACL data, so collection permissions must be set manually in the Bitwarden admin console after import.  Invite users to the Bitwarden org and grant them access to the appropriate collections.

### 8. Private vaults — user self-service

Private vaults are not exported by the admin `.1pux` (by 1Password design).  Each user migrates their own Private vault.

**iOS 26 device available:** Use the Single-Vault Pipeline in step 9.  It moves passwords and passkeys together with no plaintext file.

**Desktop only:** Use personal mode.  Passkeys do not move this way — re-register them per site after migration.

```bash
# In the user's own copy of config.json, set "mode": "personal" on their account entry.
python3 split.py --personal --account jordan
python3 import.py --personal --account jordan
python3 verify.py --personal --account jordan
```

### 9. Passkeys

Desktop `.1pux` exports contain no passkey credentials — this is confirmed by 1Password and by `passkeys.py --from-pux` scanning real exports.  The 1Password CLI (`op`) does not expose passkeys either.  The only path that moves passkeys is iOS 26 Credential Exchange (CXP) via the Single-Vault Pipeline below.

**Decision rule: run exactly one route per vault, never both.**  Running both routes on the same vault guarantees duplicates.  Identify passkey vaults first (search `=passkey` in the 1Password app to see which vaults hold passkeys), then:

- Vaults with passkeys → Single-Vault Pipeline (CXP)
- All other vaults → `split.py` / `import.py`

**What CXP moves:** passwords, passkeys, TOTP seeds.  **What it does not move:** documents, file attachments, secure notes, credit cards, identities.  For those item types, the script route remains the only full-fidelity path.

**Single-Vault Pipeline (iOS 26, one vault at a time):**

1. On 1password.com in a desktop browser: Manage Account → People → select your own user → Manage Vaults → deselect every vault except the one you are migrating now.  The 1Password iOS app now only sees that vault.
2. On an iOS 26 device: 1Password app → Settings → Advanced → Start Export → approve → choose Bitwarden as the destination.  CXP transfers that one vault — including passkey private keys and TOTP seeds — app-to-app with no plaintext file on disk.
3. In the Bitwarden web vault: transferred items land in "No Folder".  Select all and move them into a folder named after the source vault.
4. For items that belong in an organization: select the folder's items and use "Move to organization" to assign them to the target collection.
5. Back on 1password.com, re-enable all vaults before repeating for the next vault.
6. Repeat from step 1 for the next passkey vault.

Admin caveat: vault visibility changes on 1password.com affect what the user sees across all devices.  Always restore full visibility (step 5) before proceeding.

This technique is [documented by the Bitwarden community](https://community.bitwarden.com).

**Build the passkey inventory (do this first):**

1. In the 1Password app (per account), search `=passkey`.  This shows every item with a passkey and which vault it lives in.
2. Transcribe the results to a markdown file, one line per item:
   ```
   - Google | team@example.com | https://accounts.google.com
   - GitHub | team | https://github.com
   ```
3. Run `passkeys.py` to turn the list into a mobile checklist:
   ```bash
   python3 passkeys.py --account family --manual passkeys-family.md
   # => work/passkeys/index.html
   ```
4. Open `work/passkeys/index.html` on your phone and check off each passkey as you transfer it.

**Scan the export (optional sanity check):**

```bash
python3 passkeys.py --account family --from-pux exports/family.1pux
```

This searches the export JSON for any key or string matching `passkey`, `webauthn`, or `fido`.  Zero hits is expected and confirms the desktop export carries no passkey data.

**Confirm after migration:**

```bash
python3 passkeys.py --account family --from-bitwarden
```

This reads `bw list items` and reports login items whose `fido2Credentials` field is non-empty.  Run after CXP transfers to verify passkeys arrived in Bitwarden.

### 10. Clean up

Keep 1Password active for one full billing cycle to catch anything missed.  When satisfied:

```bash
# Secure-delete sensitive intermediates (macOS)
rm -P exports/*.1pux
rm -Prf work/ state/
bw lock
```

## Safety model

- **1Password is never written.** The tool is read-only on the 1Password side.
- **Additive only on Bitwarden.** No existing items are modified or deleted.
- **Ledger-protected.** The `state/<account>.json` ledger prevents accidental re-imports.
- **onExisting policy.** Each account has a configurable policy for handling pre-existing collections.
- **Plaintext discipline.** Intermediate files live in `work/` (mode 700, gitignored). Never put `exports/`, `work/`, `state/`, or `config.json` in a cloud-synced folder.

## Architecture

See [docs/DESIGN.md](docs/DESIGN.md) for the route comparison and rationale.

## Field mapping

See [docs/MAPPING.md](docs/MAPPING.md) for the full category and field conversion tables.
