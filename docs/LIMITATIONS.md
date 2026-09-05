# Limitations

## Passkeys

Passkeys are not included in any 1Password `.1pux` export.  No migration tool can move them through the export path.

On **iOS 26+**, FIDO Credential Exchange Protocol (CXP) enables direct passkey transfer between apps.  For all other platforms, passkeys must be re-registered per site after the migration.

## Private vaults

The admin `.1pux` export does not include other users' Private vaults (by 1Password design — admins cannot read member secrets).  Each user must export their own Private vault and import it into Bitwarden themselves.  The runbook in README.md covers the self-service flow.

## ACLs and sharing permissions

1Password exports contain no permission data.  After import, collection permissions must be assigned manually in the Bitwarden admin console.  Document who had access to which vaults before export and re-apply after migration.

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
