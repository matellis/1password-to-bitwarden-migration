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

Private vaults are not exported by the admin `.1pux` (by 1Password design).  Each user exports their own Private vault and imports it themselves into Bitwarden.

### 9. Passkeys

Passkeys are not included in any 1Password export.  On iOS 26+, FIDO Credential Exchange Protocol (CXP) can transfer passkeys directly between apps.  For everyone else, re-register passkeys per site after the migration.

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
