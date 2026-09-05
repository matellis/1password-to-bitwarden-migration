# Spec: Bridge, an iOS companion app for credential migration

Status: draft, not yet built. This document specifies a small iOS app that closes the
gaps the desktop toolkit cannot: passkey transfer, transfer inventory, and selective
routing. Working title "Bridge".

## Elevator pitch

An iOS 26 app that sits between two credential managers during migration. It receives a
FIDO Credential Exchange (CXP) transfer from 1Password, shows the user exactly what is
inside, lets them choose what to keep, re-exports the selection to Bitwarden (or any
CXP-compatible app), and leaves behind a verification checklist. No account, no network,
no storage of secrets. Credentials pass through; only non-secret metadata (site names,
usernames, URLs) may persist, and only until the user wipes it.

## Problem context

The desktop toolkit (this repo) covers the 1pux file route: full fidelity for documents,
attachments, cards, identities and notes, imported per-vault into Bitwarden organization
collections, with field-level verification. Two gaps remain on the credential side:

1. Passkeys only move via the CXP flow on iOS 26+ / Android. The native flow is
   all-or-nothing for whatever vaults are visible to the user and lands wherever the
   destination app decides (Bitwarden puts everything in the personal vault).
2. Nobody produces an authoritative record of what was transferred. The inventory is
   reconstructed by hand (the `=passkey` search filter) and verified after the fact.

An app that participates in CXP as a receiver sees the full transfer payload and can
act as a selective router between source and destination.

## Goals

Bridge moves exactly one thing: **the login items that contain passkeys, moved whole**
(password, TOTP and passkey together), plus the checklist JSON describing what moved.
Everything else is the desktop toolkit's job.

- Receive a CXP transfer initiated from 1Password (Settings > Advanced > Start Export).
- Display a complete inventory of the transfer: per item, its title, site (rpId), username,
  URLs, and which credential types it carries (password, passkey, TOTP).
- Default selection: only items carrying a passkey. The user can adjust, but the default
  is the point — see "Why whole items" below.
- Re-export the selection via CXP to a user-chosen destination app (Bitwarden first).
- Generate a checklist JSON of transferred items (titles, usernames, URLs, credential
  types; never secrets) and an on-device checklist view. The JSON does triple duty:
  human checklist on the phone, verification input for `passkeys.py`, and a passkey
  inventory for `split.py --mark-passkeys` (see "Integration").
- Zero knowledge, zero network: the app never connects to the internet.

## Why whole items, not passkey-only

A passkey in Bitwarden is not a standalone object; it lives inside a login item
(`login.fido2Credentials`). CXP import only ever creates items; there is no
merge-into-existing-item mechanism. Exporting passkey credentials alone would leave two
half-items per site — the script-imported login (password, TOTP) and a CXP-created orphan
(passkey only) — with autofill offering both forever. Moving the whole item keeps it
coherent. The duplicate it creates against the script-imported copy is resolved
afterwards by `adopt.py` or manual deletion, and `split.py --mark-passkeys` annotates
the script-imported copies so the pairs are easy to find.

## Non-goals (v1)

- No Bitwarden API integration and no Bitwarden client-side cryptography. Destination
  placement inside Bitwarden remains the destination app's job (personal vault) plus the
  documented manual "Move to organization" step. This is a deliberate scope cut: it
  removes the hardest and most security-critical 60% of a full-integration app.
- No 1pux parsing. Files, attachments, cards and identities stay with the desktop toolkit.
- No long-term credential storage. The app is not a password manager.
- No Android v1. Android CXP flows through Google Password Manager; revisit once that
  surface stabilizes.

## Platform and APIs

- iOS 26 or later, SwiftUI, no third-party dependencies.
- AuthenticationServices credential exchange APIs: `ASCredentialImportManager` to receive,
  `ASCredentialExportManager` to re-export. The OS mediates the CXF encryption envelope;
  the app only ever handles decrypted credential structs in memory and hands structs back
  to the OS for re-encryption on export. The app never implements CXF cryptography itself.
- Entitlements and registration requirements for CXP participation must be confirmed
  against Apple documentation at build time (see Open questions). This is the project's
  biggest unknown and gates everything, so it is milestone M0.

## Data model

`TransferSession`: id, created timestamp, source app name, item count.

`CredentialItem`: id, title, site/rpId, username, URLs, flags (`hasPassword`,
`hasPasskey`, `hasTOTP`), source grouping (vault/collection name if the payload carries
it), re-export selection (include/exclude), checklist state (pending/confirmed).

Only non-secret fields are ever written to disk. Passwords, passkey material and TOTP
seeds exist only as in-memory API objects for the duration of a session.

## Flows

1. **Receive.** In 1Password the user starts an export and picks Bridge as the
   destination. Bridge wakes, ingests the transfer, lands on a summary screen: total
   items, counts by credential type, counts by grouping if present.
2. **Review and route.** Searchable, grouped list. Select all / none / per-group /
   per-item. Item detail shows metadata only; a passkey is shown as "Passkey present",
   never its material.
3. **Re-export.** One button: "Send N items to..." The user picks the destination app in
   the system flow. Bridge hands the selected credential structs to
   `ASCredentialExportManager`; the destination app completes the import.
4. **Checklist.** Exported items become checklist entries. The user ticks each entry after
   confirming it works in the destination (for passkeys: one successful sign-in per site).
   Checklist exports as JSON via the share sheet for merging into the desktop
   verification pass (`passkeys.py --from-bridge`).
5. **Wipe.** A single control erases all session data and stored metadata. Sessions also
   auto-purge secrets from memory on completion, on backgrounding past a short timeout,
   and always on re-export handoff.

## Security model

- No network capability used, period. App Store privacy label: Data Not Collected.
- Secrets are in-memory only, never serialized, never logged, never in snapshots (the
  screen is masked when backgrounded).
- Metadata persistence is opt-in per session and erasable with one tap.
- Threat model: a compromised device is out of scope, as it is for every password
  manager on that device. What the app owes the user is to add nothing to the attack
  surface: no network, no persistence of secrets, no third-party SDKs.

## Integration with the desktop toolkit

- Shared checklist JSON schema (versioned): account label, source app, entries of
  {title, username, primary URL, credential types, state}.
- The script route always imports every vault in full, regardless of Bridge. An
  `--exclude` flag for `import.py` was considered and deliberately rejected: filtering
  Bridge's items out of the script import would make the migration depend on the phone
  leg actually happening, and a user who never runs it would silently lose those items.
  Duplicates are recoverable (`adopt.py` or manual deletion); missing items are not.
- Ordering does not matter. The script import can run before or after the Bridge leg;
  `adopt.py` reconciles afterwards.
- Bridge JSON is accepted directly by `split.py --mark-passkeys` and `passkeys.py --bridge`
  (and therefore `--gap-report`), so the checklist doubles as a passkey inventory for both
  the marker mechanism and gap reporting without any format conversion.
- `passkeys.py --from-bridge <file.json>` merges the app's checklist with the desktop
  inventory and `--from-bitwarden` results into one verification report.
- README links both ways.

## Distribution

- Requires an Apple Developer Program membership. v1 can ship via TestFlight to the
  migrating team; public App Store release follows if the toolkit's users want it.
- The repo stays the full-fidelity path; the app is the credential-and-passkey path.

## Open questions (confirm at build time, in this order)

1. Entitlements: which capabilities must an app declare to register as a CXP import and
   export participant, and does Apple gate them (e.g. behind the AutoFill credential
   provider entitlement or a review)?
2. Chaining: may one app both import and export credentials in a single user session?
3. Payload shape: does the CXP payload from 1Password carry vault/collection grouping,
   or is the inventory flat (in which case grouping falls back to site/rpId)?
4. Destination choice: at re-export, does the OS present a destination picker, or can the
   destination be requested programmatically?
5. Graceful degradation: if re-export turns out to require full credential-manager
   privileges, v1 ships as receive + inventory + checklist only. The user still runs the
   standard 1Password-to-Bitwarden CXP flow, and Bridge supplies the authoritative
   checklist and verification. Even this reduced app is worth having.

## Milestones

- **M0, spike (gates the project):** skeleton app on a real device, entitlement
  configured, receive a real CXP transfer from a test 1Password account and dump the
  item inventory to the console. One to two days. If this fails, the project stops here
  at zero sunk cost.
- **M1:** inventory UI (grouping, search, selection).
- **M2:** re-export of the selection to a destination app.
- **M3:** checklist view, JSON export, wipe control, background masking.
- **M4:** TestFlight for the migrating team, dogfood the real migration, then decide on
  App Store submission.

Estimated total: two to four weeks of part-time work beyond M0, dominated by UI polish
and App Store review, not by the credential code (the OS does the cryptography).
