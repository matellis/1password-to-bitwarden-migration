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

## Two routes, one decision rule

For any given vault there are two migration routes.  Run exactly one per vault — never both.  Both routes on the same vault will produce duplicates.

**Script route** (`split.py` / `import.py`): full-fidelity — moves all item types including documents, file attachments, secure notes, credit cards, and identities.  Cannot move passkeys.  Works entirely on desktop from a `.1pux` export.

**CXP route** (iOS 26 Single-Vault Pipeline): moves credentials only — passwords, passkeys, TOTP seeds.  Transfers app-to-app with no plaintext file on disk.  Requires an iOS 26 device and vault-visibility scoping on 1password.com to keep CXP vault-precise.

Decision rule: search `=passkey` in the 1Password app first.  Vaults that contain passkeys go via CXP.  All other vaults go via the script route.  For vaults that contain both passkeys and attachments, split the work: move credentials via CXP, then use the script route for a separate vault (or accept that attachments must be moved manually).

## Why passkey inventory is semi-manual

No desktop export or CLI path can enumerate which 1Password items have passkeys.  The `.1pux` format carries no passkey fields; the `op` CLI JSON schema has no passkey support.  The only reliable inventory source is the 1Password app itself, via the `=passkey` search filter.

`passkeys.py --from-pux` does a defensive scan anyway — it searches every key and string value in the export JSON for `/passkey|webauthn|fido/i`.  The expected result is zero credential hits, which confirms the export limitation empirically on real data.  Any unexpected hits are reported with their JSON path so the user can inspect them.

`passkeys.py --manual` converts a user-transcribed list (from the `=passkey` search) into a self-contained mobile checklist, so the transfer can be tracked per-item on the phone where FIDO CXP runs.
