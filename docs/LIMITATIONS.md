# Limitations

## Passkeys

Desktop `.1pux` exports contain no passkey credentials.  This is confirmed in 1Password's own documentation and verified empirically: scanning a large export for any key or value matching `passkey`, `webauthn`, or `fido` returns zero hits on passkey data.  The 1Password CLI (`op`) also does not expose passkeys — the `op item get` JSON schema has no passkey field, and `op` item templates do not support passkeys.

Re-verified against op 2.39.0 (2026-09-05): for items confirmed to hold a passkey via the app's `=passkey` search, neither `op item list` nor `op item get` shows any passkey indicator — no field, no flag, not even a null placeholder.  Building the passkey inventory therefore remains a manual step (the `=passkey` search in the app, transcribed); `op` can only help enrich an inventory with usernames/URLs, not discover it.

There is no desktop file export path for passkeys.  Passkeys are transferable via iOS 26 Credential Exchange (CXP) using the Single-Vault Pipeline described in README.md step 9.  That pipeline is also the only route that moves passkeys without per-site re-registration.

**Transfer options:**
- **iOS 26 Single-Vault Pipeline (recommended):** Scope vault visibility to one vault at a time on 1password.com, then run CXP from the iOS 26 1Password app to Bitwarden.  Moves passwords, passkeys, and TOTP seeds for that vault only.  Does not move documents, file attachments, secure notes, credit cards, or identities — use the script route for those.
- **Per-site re-registration:** Visit each site, remove the 1Password passkey, and register a new passkey into Bitwarden.

After migration, run `passkeys.py --from-bitwarden` to confirm which items hold passkeys in Bitwarden (`fido2Credentials` non-empty).

## Private vaults

The admin `.1pux` export does not include other users' Private vaults (by 1Password design — admins cannot read member secrets).  Each user must export their own Private vault and import it into Bitwarden themselves.  The runbook in README.md covers the self-service flow.

## ACLs and sharing permissions

The `.1pux` export itself contains no permission data — this is a static-file limitation and does not change.  After import, collection permissions must still be assigned manually in the Bitwarden admin console, since the bw CLI has no way to set them programmatically.

Separately, `split.py` can query *live* sharing data from the local `op` (1Password CLI), if it's installed and signed in, to auto-resolve an unclassified vault's `vaultDestination` (owner-only vs. shared, and who to share with) instead of falling back to a name guess.  This reads current 1Password ACLs directly from the account, not from the export file.  It does not remove the manual step above — Bitwarden collection permissions still have to be set by hand — it only improves the accuracy of the destination classification going in.  If `op` isn't installed, this is silently skipped and the name-substring guess is used, as before.

## Tags

Bitwarden organization items do not support tags.  Tags from 1Password items are appended to the `notes` field as `Tags: tag1, tag2`.

## Item history

Only `passwordHistory` is carried over.  Other history types (document version history, previous field values) are not in the 1pux format.

## Watchtower / breach reports

1Password's Watchtower data (breach report flags, weak password flags) is not exported and is not migrated.  Bitwarden's equivalent (Vault Health Reports) will re-analyze passwords independently.

## Sends

Bitwarden Sends are a Bitwarden-native concept.  1Password's secure document sharing has no equivalent item type in `.1pux` exports.

## Web import size limits

The Bitwarden web vault importer has a file-size limit.  This tool uses the CLI (`bw import`) which avoids that limit.

## 1pux spec version

This tool targets 1pux format version 3.  Exports from older 1Password versions may use an earlier format and may require adjustments to the parser.

## Large attachment files

The Bitwarden free tier limits attachment sizes.  Organization plans have higher limits.  Very large files (>500 MB per attachment for most plans) may fail to upload.  Check your Bitwarden plan limits before migrating accounts with large document items.

## Collection name collisions

If a Bitwarden collection with the same name already exists in the target org, `bw import` will merge items into it.  The `onExisting` policy in this tool (`refuse` by default) prevents unintended merges, but it operates on name comparison — not on collection UUID.  Use `vaultRename` in config to avoid name collisions.
