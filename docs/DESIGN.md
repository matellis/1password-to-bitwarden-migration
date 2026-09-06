# Design Notes

## Why 1pux split + bw CLI

Two routes exist for migrating from 1Password to Bitwarden:

**Route A (this tool): split the .1pux, drive bw CLI.**
The `.1pux` is a zip file containing a full JSON tree of all vaults and items. We parse it offline, produce per-vault Bitwarden org-import JSON, then drive `bw import` for bulk items and `bw create item` + `bw create attachment` for attachment-bearing items.

**Route B: op CLI per item.**
Use the 1Password CLI (`op`) to read items one-by-one and write them to Bitwarden. This requires both CLIs to be authenticated simultaneously and is roughly 10x slower for large vaults due to per-item API calls.

Route A is faster, fully offline for the split step, and produces auditable intermediate files you can inspect before any Bitwarden write happens. The offline split is also safely re-runnable; only `import.py` touches Bitwarden.

## Why bulk + individual hybrid

`bw import` is fast but returns no item IDs. File attachments require a Bitwarden item ID to upload against (`bw create attachment --itemid`). So items carrying attachments are excluded from the bulk import and created individually via `bw create item` (which returns the created item's JSON, including its ID), then their files are uploaded.

## Vault structure problem

Bitwarden's own importer flattens all vaults into a single uncollectioned import (see [bitwarden/clients#20724](https://github.com/bitwarden/clients/issues/20724)). This tool works around that by generating one org-import JSON per vault, each with its own collection definition. The `bw import` command creates the collection on import when the collection `id` and `name` don't already exist in the org.

## onExisting policy

The three policies exist because teams frequently have partially-populated Bitwarden orgs before migration:

- **refuse** (default): safe for first-time imports; ensures nothing is silently doubled.
- **skip**: deduplicates against existing items using a `(type, title, username, primaryURI)` fingerprint. Useful for resuming a partial import.
- **allow**: escape hatch for intentional re-imports.

## Idempotency ledger

`state/<account>.json` records every vault import with a timestamp, item counts, and the collection ID. A vault present in the ledger is skipped on re-run unless `--force`. This prevents double-imports when re-running the whole script after a partial failure.

Personal mode uses a separate ledger file (`state/<account>-personal.json`) so org-mode and personal-mode runs for the same account don't interfere.

## Script import plus CXP passkey layer

Every vault uses the script route. Vaults containing passkeys additionally get a CXP transfer afterwards, per person, to add the passkeys the script route cannot carry.

**Script route** (`split.py` / `import.py`): full-fidelity — moves all item types including documents, file attachments, secure notes, credit cards, and identities.  Cannot move passkeys.  Works entirely on desktop from a `.1pux` export.

**CXP route** (iOS 26 Single-Vault Pipeline): moves credentials only — passwords, passkeys, TOTP seeds.  Transfers app-to-app with no plaintext file on disk.  Requires an iOS 26 device and vault-visibility scoping on 1password.com to keep CXP vault-precise.

The script route always runs for every vault, in full.  CXP cannot substitute for it: CXP moves credentials only, so a CXP-only vault would silently lose documents, attachments, secure notes, credit cards and identities.  CXP's role is to add passkeys after the script import, producing recoverable duplicates for passkey-bearing logins: `adopt.py` merges the passkey from the CXP-created item into the script-imported one and moves the duplicate to trash.  `split.py --mark-passkeys` annotates affected items at import time so the gap is visible in Bitwarden even if a user never runs CXP.

## Passkey safety-net features

Three tools make the passkey gap visible and recoverable after the script route runs:

**`split.py --mark-passkeys <file>`** — accepts a markdown inventory (`- Site | username | url`) or a Bridge JSON (`{entries:[...]}`) and appends a human-readable notice to the notes of every generated login whose fingerprint matches an inventory entry.  The notice survives import into Bitwarden and is visible in the web vault.  The `passkeysMarked` count appears in the printed table and manifest.

**`passkeys.py --gap-report`** — loads inventory entries (via `--manual` or `--bridge`) and Bitwarden passkey items (via `--from-bitwarden`) then prints per-account: expected, confirmed, missing, and unexpected.  Always exits 0.

**`adopt.py`** (EXPERIMENTAL) — after a late CXP transfer, lists personal-vault login items that carry `fido2Credentials`, fingerprint-matches them against a target org collection, and for each match: edits the org item to add the passkey, verifies the passkey landed, then soft-deletes the personal duplicate.  Deletes happen only after verification.  Default is dry-run; `--apply` is required to write.  The Bitwarden server may or may not accept `fido2Credentials` on an edit — treat the first `--apply` run as an experiment on a single item.

## Privacy routing and vault destination classification

1Password uses vault type as a rough privacy signal (type P = Private, visible only to the owner) but user-created vaults (type U, E, etc.) may also be owner-only in practice — the `.1pux` format carries no ACL data.  Routing every non-P vault to an org collection without asking would silently expose owner-only content to org members.

The tool therefore requires an explicit destination declaration for every non-Private vault via the `vaultDestination` config key.  When `split.py` finds a non-P vault lacking a declaration, it writes a guess into `config.json`, then org mode exits with a clear error asking the user to review and confirm the written value; `import.py` refuses the same way if the manifest still carries no destination.  Existing non-blank entries are never overwritten. A bare `""` entry is a machine-written placeholder, not a decision, so it's re-resolved via op on each run when `opAccount` is set; it's left alone otherwise. Type P always routes personal (safe default); everything else is the user's decision.

**op CLI as a live ACL data source.**  `.1pux` carries no ACL data, but the local `op` CLI (1Password's own CLI, distinct from the export) can be queried live if it's installed and signed in.  `lib/opacl.py` wraps `op vault user list`, `op vault group list`, and `op user list --group` for a given vault, plus `op whoami` to identify the vault owner.  `split.py` uses this, per unclassified vault, before falling back to the name-substring guess:

- Only grants carrying viewing permission count as sharing; entries with only `allow_managing` (1Password's built-in Owners/Administrators groups, or any user granted just administrative access) are ignored, since a vault organizer manages a vault without necessarily reading its contents — the Bitwarden equivalent of that role is org Owner/Admin, not collection access, so it correctly does not appear in `shareWith`.
- No direct members and no groups with viewing access → the vault is inferred `"owner-only"`.
- Direct members exist, or a group expands to members, after excluding the owner's own email (from `op whoami`) → the vault is written as `{"destination": "shared", "shareWith": [...]}`, deduped and sorted.
- Groups are expanded member-by-member via `op user list --group <name>`; if a group's expansion fails, whatever else was resolved is still written, and the console output names the group(s) needing a manual check.

**Fallback chain**, each link silent and non-fatal: `opAccount` not set (op is never consulted — the `op` default account is ambiguous in multi-account setups and could return ACLs for the wrong account) → `op` not installed → per-vault `op` call fails (vault not found, `op` not signed in, transient error) → name-substring guess (`"shared"`/`"private"`/`"personal"` in the name, otherwise `""`).

Three destinations are supported:

- **shared** — normal org collection; all org members may be granted access.  `vaultDestination` may instead give an object `{"destination": "shared", "shareWith": [...]}` naming specific member emails, when the intent is narrower than "all org members."  The bw CLI can't set collection member permissions either way, so granting access remains a manual web-vault step — the tool carries the member list through to the manifest and prints a post-import checklist naming the collection and the exact members to grant access to (Admin Console > Organizations > Collections > Manage access).
- **owner-only** — org collection intended for the owner only.  A new collection grants access to nobody: regular members cannot see it unless granted.  The exception is org Owners/Admins, who can see all collections under the default org setting.  The bw CLI (2026.8.0) can neither read nor set collection member permissions (`bw edit org-collection` can rename a collection but not set per-member access), so after import the tool prints a notice to verify access in the Bitwarden web vault (Admin Console > Organizations > Collections > Manage access).  Verification is a manual step.
- **personal** — routed to personal-mode output alongside the built-in Private vault.  Skipped in org-mode runs; should be handled by a personal-mode config entry.

## Login identity guard

`ensure_session` in `lib/bwcli.py` now accepts an `expected_email` parameter.  When the bw CLI is already unlocked, the script reads `userEmail` from `bw status` JSON and compares it case-insensitively to the expected email.  A mismatch triggers `bw logout`, clears `BW_SESSION`, and re-authenticates to the correct account.  This prevents a second personal-mode import from silently landing in the wrong Bitwarden vault when accounts share the same machine.

`bitwardenEmail` is required in config for every `mode: personal` entry.  `import.py` and `verify.py` exit with a clear error if a personal entry lacks it.  For org entries the field is optional; when present, the same guard applies.

`adopt.py` threads `bitwardenEmail` from the account entry into `ensure_session`.  `passkeys.py --from-bitwarden` takes a `--server` flag but no account entry and is therefore account-agnostic; it does not enforce the email guard.

## Why passkey inventory is semi-manual

No desktop export or CLI path can enumerate which 1Password items have passkeys.  The `.1pux` format carries no passkey fields; the `op` CLI JSON schema has no passkey support.  The only reliable inventory source is the 1Password app itself, via the `=passkey` search filter.

`passkeys.py --from-pux` does a defensive scan anyway — it searches every key and string value in the export JSON for `/passkey|webauthn|fido/i`.  The expected result is zero credential hits, which confirms the export limitation empirically on real data.  Any unexpected hits are reported with their JSON path so the user can inspect them.

`passkeys.py --manual` converts a user-transcribed list (from the `=passkey` search) into a self-contained mobile checklist, so the transfer can be tracked per-item on the phone where FIDO CXP runs.
