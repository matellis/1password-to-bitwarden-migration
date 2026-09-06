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
- [Bitwarden CLI](https://bitwarden.com/help/cli/) (`bw`) installed
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

# Step 2 — import (scripts log in and unlock automatically)
python3 import.py --account family

# Step 3 — verify
python3 verify.py --account family
```

## Four-entity walkthrough

The exemplar scenario has four destination entities for one person: **my-family** (family org), **team-a** (first team org), **team-b** (second team org), and three personal-vault entries — **me-family**, **me-team-a**, **me-team-b** — one for each 1Password account export, each landing in a different Bitwarden account.

Privacy in Bitwarden is determined by **who has access**, not by vault type.  A user-created vault that only the owner can see must be declared as `"owner-only"` or `"personal"` — the tool will not guess.

### 1. Create Bitwarden organizations

For each shared 1Password account (family, team-a, team-b), create a Bitwarden organization.  Note the organization UUID from the admin console; put it in `config.json` as `bitwardenOrgId`.

### 2. Export from 1Password

In 1Password: **File → Export → All Items → 1PUX format**.  Do this per account.

Save exports to the `exports/` directory (gitignored).

### 3. Configure

```bash
cp config.example.json config.json
```

You supply account-level values by hand (where to find each one is listed below).  You do **not** fill in `vaultDestination` yourself — run `split.py --account <name>` and it writes one entry per vault for you to review (real sharing data when `opAccount` is set and `op` works, name-based guesses otherwise).  Your only job on `vaultDestination` is to confirm or correct what split.py wrote, then re-run.

Org entries:
- `puxPath` — path to the `.1pux` from step 2, e.g. `exports/1PasswordExport-….1pux`
- `bitwardenOrgId` — the org's UUID from step 1: Bitwarden web vault → Admin Console → your org → **Settings → My organization**, or from the URL when the org is open
- `bitwardenServer` — which Bitwarden instance this account lives in (see below).  Region is chosen at org creation and cannot change later — decide before creating the org
- `onExisting` — what to do if a collection already has items (`refuse`, `skip`, `allow`); use `refuse` for the first run
- `skipVaultTypes` — vault types to ignore (default `["P"]` skips Private vaults)
- `vaultRename` — optional map of `{"1Password vault name": "Bitwarden collection name"}`
- `opAccount` — the shorthand from `op account list` (e.g. `my` for `my.1password.com`) for the 1Password account this entry's export came from.  **Required** for the `op`-based auto-resolution described below (without it, `op` is never consulted — in multi-account setups the `op` default account is ambiguous and could return sharing data for the wrong account); also used by `verify.py --op`.
- `vaultDestination` — filled in by split.py, reviewed by you (see below)

Personal entries (`"mode": "personal"`) need the same account-level values minus `bitwardenOrgId`, plus:
- `bitwardenEmail` — **required**: the email of the Bitwarden account this vault lands in (you created these accounts in step 1's personal-account setup)
- `vaultRename` — use this to avoid three colliding "Private" folders, e.g. `{"Private": "Private (Family)"}`

**`bitwardenServer` values**

| Value | Server |
| --- | --- |
| `"us"` | `https://vault.bitwarden.com` (US cloud, default when key is absent) |
| `"eu"` | `https://vault.bitwarden.eu` (EU cloud) |
| `"https://..."` | Self-hosted — any full `https://` URL pointing at your instance |

JSON has no comment syntax, so the key serves as its own documentation.  Omitting `bitwardenServer` is identical to setting it to `"us"`.

**Accounts spanning regions:** the bw CLI's server setting is global.  When you run `import.py` or `verify.py` without `--account`, the scripts iterate over all accounts and switch the CLI's server as they go.  You will be asked to log in once per region.  Running per-account (`--account my-family`) is cleaner when accounts span US and EU clouds.

**`vaultDestination` — your privacy decision**

Every non-Private vault must be explicitly classified.  When `split.py` finds an unclassified vault, it writes a guess into `config.json` for you to confirm.  Existing non-blank entries are never overwritten.  A bare `""` entry is a machine-written placeholder rather than a decision, so if `opAccount` is set it's re-resolved via op on each run; without `opAccount` it's left alone.  Org mode still exits non-zero after writing so you can review the guesses in `config.json` before re-running; personal mode warns and skips instead.

When the account sets `opAccount` and `op` is installed and signed in, `split.py` tries to resolve the vault's real sharing info before falling back to the name-substring guess:

- `op vault user list` and `op vault group list` return the vault's direct members and groups; each group is expanded with `op user list --group` to collect its members' emails.
- Only grants with viewing permission count as sharing.  1Password's built-in Owners/Administrators groups (and any user entry with only `allow_managing`) are administrative and are ignored, even though they appear on every vault; a family organizer's or team admin's Bitwarden equivalent is org Owner/Admin, not collection access, so their email correctly stays out of `shareWith`.
- No members and no groups beyond the owner → the guess is written as `"owner-only"`.
- Members found (after excluding the vault owner's own email, from `op whoami`) → the guess is written as the object form `{"destination": "shared", "shareWith": [...]}` with the resolved, deduped, sorted email list.
- If a group fails to expand, whatever was resolved from direct members is still written, and the console line names the group(s) that need a manual check.
- If `op` is not installed, or the per-vault `op` lookup fails for any reason, this step is silently skipped and the old name-substring guess applies: `"shared"` if the vault name contains "shared", `"personal"` if it contains "private" or "personal", otherwise an empty string.

`opAccount` is passed to every `op` call as `--account <opAccount>`.  Its value is the shorthand shown by `op account list` (e.g. `my` for `my.1password.com`).

Signing in: enable **Settings → Developer → Integrate with 1Password CLI** in the 1Password 8 desktop app — then `op` authenticates through the unlocked app and no `op signin` is needed.  If you use `op signin` instead, it only works via `eval $(op signin)` and the session token does not carry over to other terminals (including the ones these scripts spawn from), so app integration is strongly recommended.  `op account list` shows registered accounts even when nothing is signed in — use `op whoami` to confirm sign-in actually works.

| Destination | Meaning |
| --- | --- |
| `"shared"` | Imported as a normal org collection — all org members may be granted access. |
| `"owner-only"` | Imported as an org collection intended for the owner only.  The bw CLI cannot set member permissions; after import you must restrict the collection in the Bitwarden web vault (Admin Console > Organizations > Collections > Manage access).  The tool prints a post-import reminder listing the collections to restrict. |
| `"personal"` | Routed to personal-mode output — same handling as the built-in Private vault.  This vault is skipped in the org-mode run and should be processed by a personal-mode entry. |

`"shared"` also accepts an object form when you want to name specific members to grant access to, since the bw CLI cannot set collection member permissions itself:

```json
"vaultDestination": {
  "Kids Accounts": { "destination": "shared", "shareWith": ["kid@example.com", "mom@example.com"] }
}
```

`import.py` carries that list through to the manifest and prints a post-import checklist naming the collection and the members to grant access to in the Bitwarden web vault.  Plain `"shared"` with no `shareWith` stays silent, same as today.

**`bitwardenEmail` — per-account identity guard**

Personal-mode entries each land in a separate Bitwarden account.  `bitwardenEmail` is required for every `mode: personal` entry and is optional (but enforced when present) for org entries.  When the CLI is logged in as a different account, the scripts log out and re-authenticate to the correct account automatically.

**`bitwardenEmail`** is also recommended for org entries when you want the same protection — set it to the Bitwarden admin account email for that org.

### 4. Split (offline, safe to re-run)

```bash
python3 split.py
```

This reads the `.1pux` files and writes `work/<account>/` with one JSON file per vault plus an attachments manifest.  **Nothing is written to Bitwarden.**  Review the table it prints before proceeding.

### 5. Import (one account at a time)

```bash
python3 import.py --account family
```

The script logs in and unlocks Bitwarden automatically.  If your vault has 2FA, the interactive prompt appears in the terminal.  The tool checks the `state/<account>.json` ledger and skips vaults already imported.  Use `--force` to re-import.

### 6. Verify

```bash
python3 verify.py --account family
```

Compares source 1pux data field-by-field against live Bitwarden data.  Exits 0 on full pass.

### 7. Invite users and assign permissions

1Password exports contain no ACL data, so collection permissions must be set manually in the Bitwarden admin console after import.  Invite users to the Bitwarden org and grant them access to the appropriate collections.

### Non-interactive (CI) authentication

All scripts call `bw login` and `bw unlock` automatically. For headless environments, set these environment variables before running:

| Variable | Purpose |
| --- | --- |
| `BW_CLIENTID` | API key client ID (from Bitwarden account settings) |
| `BW_CLIENTSECRET` | API key client secret |
| `BW_PASSWORD` | Master password for non-interactive vault unlock |

When `BW_CLIENTID` and `BW_CLIENTSECRET` are both set, the scripts use `bw login --apikey` instead of prompting for email and password. When `BW_PASSWORD` is set, unlock is non-interactive. 2FA is handled automatically by the interactive terminal flow when these env vars are absent.

The session key is held only in memory (`BW_SESSION` env var for the process lifetime) and is never written to disk or logged.

### 8. Private vaults — user self-service

Private vaults are not exported by the admin `.1pux` (by 1Password design).  Each user migrates their own Private vault.

**iOS 26 device available:** Use the Single-Vault Pipeline in step 9.  It moves passwords and passkeys together with no plaintext file.

**Desktop only:** Use personal mode.  Passkeys do not move this way — re-register them per site after migration.

```bash
# In config.json, add a mode=personal entry with bitwardenEmail set.
python3 split.py --personal --account me-family
python3 import.py --personal --account me-family
python3 verify.py --personal --account me-family
```

### 9. Passkeys

Desktop `.1pux` exports contain no passkey credentials — this is confirmed by 1Password and by `passkeys.py --from-pux` scanning real exports.  The 1Password CLI (`op`) does not expose passkeys either — re-verified against op 2.39.0: items confirmed to hold a passkey (via the app's `=passkey` search) show no passkey field, flag, or null placeholder in `op item list` or `op item get`.  There is no automated way to discover which items have passkeys; the inventory below is built by hand.

Two options, plus the default (see docs/DECISIONS.md for why):

**Default — do nothing.**  Let the passkeys go.  The script route has already migrated each item's password, so when someone next visits a site whose passkey is missing, they log in with the password and save a fresh passkey to Bitwarden right there.  No inventory, no checklist, no coordination — re-enrollment happens opportunistically as people naturally use their accounts.  This is the recommended path for most migrations.

**Option A — systematic re-registration (only if someone insists on completeness).**  Search `=passkey` in the 1Password app — that live search is the checklist (each person works their own vaults on their own device).  For each item: log into the site with the password, add a passkey, save it to Bitwarden, then delete the passkey from the 1Password item so it drops out of the search.  When `=passkey` returns zero, you are done.

**Option B — CXP transfer (for hundreds of passkeys, or passwordless-only accounts).**  iOS 26 Credential Exchange moves passkey private keys app-to-app with no plaintext file; Bitwarden's iOS app receives transfers natively, so no intermediary app is required.  Costs: per-vault visibility scoping on 1password.com, community-reported failures on large vaults, and duplicates to reconcile afterwards (`adopt.py` or manual deletion).  If an account is truly passwordless-only (no password fallback), CXP is the only non-recovery route — identify those from the `=passkey` search before choosing.

**Every vault goes through the script route, in full, regardless of which option you pick.**  CXP moves credentials only — passwords, passkeys, TOTP seeds — never documents, file attachments, secure notes, credit cards or identities.  A vault that relied on CXP alone would silently lose every non-credential item, so `split.py` / `import.py` always import everything; Option B then layers passkeys on top, per person, at each owner's own pace.

**Single-Vault Pipeline (Option B, iOS 26, one vault at a time):**

1. On 1password.com in a desktop browser: Manage Account → People → select your own user → Manage Vaults → deselect every vault except the one you are migrating now.  The 1Password iOS app now only sees that vault.
2. On an iOS 26 device: 1Password app → Settings → Advanced → Start Export → approve → choose Bitwarden as the destination.  CXP transfers that one vault — including passkey private keys and TOTP seeds — app-to-app with no plaintext file on disk.
3. In the Bitwarden web vault: transferred items land in "No Folder".  Select all and move them into a folder named after the source vault.
4. For items that belong in an organization: select the folder's items and use "Move to organization" to assign them to the target collection.
5. Back on 1password.com, re-enable all vaults before repeating for the next vault.
6. Repeat from step 1 for the next passkey vault.

Admin caveat: vault visibility changes on 1password.com affect what the user sees across all devices.  Always restore full visibility (step 5) before proceeding.

This technique is [documented by the Bitwarden community](https://community.bitwarden.com).

**Optional: machine-readable inventory.**  Not needed for Option A (the `=passkey` search is the checklist).  Build one only if you want `--mark-passkeys` notices in imported items (step 10) or an item-level `--gap-report` cross-check:

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
4. Open `work/passkeys/index.html` on your phone and check off each passkey as you re-register or transfer it.

**Scan the export (optional sanity check):**

```bash
python3 passkeys.py --account family --from-pux exports/family.1pux
```

This searches the export JSON for any key or string matching `passkey`, `webauthn`, or `fido`.  Zero hits is expected and confirms the desktop export carries no passkey data.

**Confirm after migration:**

```bash
python3 passkeys.py --account family --from-bitwarden
```

This reads `bw list items` and reports login items whose `fido2Credentials` field is non-empty.  Run after re-registration or CXP transfers to verify passkeys arrived in Bitwarden.

### 10. Optional: tracking passkeys

With the default passkey stance (step 9: do nothing, re-enroll opportunistically), none of this section applies — it exists for migrations that deliberately want item-level tracking.  Without tracking, passkeys are silently absent in Bitwarden after migration, by design.  Three tools make the gap visible and recoverable for those who want that.

**Markers in the imported items** — run split.py with a passkey inventory and every affected login will have a notice in its notes field:

```bash
python3 split.py --mark-passkeys passkeys-family.md --account family
```

The inventory file can be the same markdown file you build for the checklist (`- Site | username | url`) or a Bridge JSON file (`{entries:[{title,username,url}]}`).  The notice reads:

> [1Password migration] This login had a passkey that was NOT migrated. Re-register it into Bitwarden on the site (log in with the password, add a passkey in security settings), or transfer it via iOS Credential Exchange (see README).

Users see this in the Bitwarden web vault as soon as they open the item.

**Gap report** — after re-registration or CXP transfers are supposed to be done, check who is finished:

```bash
# Load the inventory and confirm what is in Bitwarden
python3 passkeys.py --account family --manual passkeys-family.md --from-bitwarden --gap-report
```

This prints: expected passkeys (from inventory), confirmed in Bitwarden, missing, and unexpected.  Always exits 0 — it is a report, not a gate.

Note: org items are visible to the admin.  Personal-vault passkeys require each user to run this themselves.

**adopt.py** — if a user does a late CXP transfer after the script route has already run, personal-vault CXP items and org items now coexist as duplicates.  `adopt.py` merges the passkey into the org item and moves the personal duplicate to trash:

```bash
# Review the plan first (no writes)
python3 adopt.py --account family --collection Employee

# Apply after verifying the plan
python3 adopt.py --account family --collection Employee --apply
```

`adopt.py` is **EXPERIMENTAL**.  Read the warnings printed by the script before using `--apply`.  Verify with a real passkey sign-in after the first run on a single item before batch use.

Manual fallback: in the Bitwarden web vault, move the CXP-transferred item into the target org collection, then delete the script-imported login that has the passkey notice in its notes.

### 11. Clean up

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

See [docs/DESIGN.md](docs/DESIGN.md) for the design notes and rationale.

## Field mapping

See [docs/MAPPING.md](docs/MAPPING.md) for the full category and field conversion tables.

## Companion apps and upstream

- [docs/IOS-APP-SPEC.md](docs/IOS-APP-SPEC.md) specifies "Bridge", a small iOS 26 app that receives a CXP transfer from 1Password and re-exports only the passkey-bearing login items, keeping the duplicate set minimal; its checklist JSON feeds `split.py --mark-passkeys` and `passkeys.py --gap-report`. Not yet built; deprioritized — see [docs/DECISIONS.md](docs/DECISIONS.md).
- [docs/DECISIONS.md](docs/DECISIONS.md) records dated, intentional direction decisions (e.g. the 2026-09-05 passkey strategy).
- [docs/MACOS-APP-SPEC.md](docs/MACOS-APP-SPEC.md) specifies a Migration Assistant-style macOS app that wraps this toolkit for less technical users: the scripts stay the engine, the app makes the runbook clickable. Not yet built.
- [docs/UPSTREAM-IMPORTER-PR.md](docs/UPSTREAM-IMPORTER-PR.md) scopes a fix for the root cause in Bitwarden's own importer ([bitwarden/clients#20724](https://github.com/bitwarden/clients/issues/20724)), including a file map, test plan, draft PR description, and an untested draft patch in [docs/upstream/](docs/upstream/).
