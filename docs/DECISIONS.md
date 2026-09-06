# Decisions

Dated, intentional decisions about the direction of this toolkit.  Newest first.

## 2026-09-05 — Passkeys: drop them, re-enroll opportunistically

**Decision.**  Passkeys are intentionally dropped in the current migrations — no inventory, no checklist, no markers, no gap-report step, no CXP.  When a user next visits a site whose passkey didn't migrate, they log in with the (migrated) password and save a new passkey to Bitwarden then.  Re-enrollment happens opportunistically, at natural pace, with zero coordination — humans cannot work at the precision or time commitment a tracked passkey project demands.  The CXP apparatus (Single-Vault Pipeline, `adopt.py`, the spec'd Bridge iOS app) is intentionally **not** being pursued.  Bridge is deprioritized, not cancelled.

**Learnings that drove it.**

- Desktop `.1pux` exports carry no passkey data (confirmed by 1Password, verified empirically — docs/LIMITATIONS.md).
- `op` 2.39.0 exposes no passkey presence either: items confirmed to hold a passkey (via the app's `=passkey` search) show no passkey field, flag, or null placeholder in `op item list` or `op item get`.  Every automated discovery route is closed; any tracking would be manual transcription, which is exactly the overhead being rejected.
- CXP shipped in iOS/macOS 26 and Bitwarden's iOS app receives transfers natively — an intermediary app (Bridge) was never required for the transfer itself, only for inventory generation and duplicate minimization.
- CXP carries operational costs: per-vault visibility scoping on 1password.com, community-reported transfer failures on large vaults, and duplicate items needing reconciliation.
- Passkeys are designed for multiple enrollments per account: a new passkey does not require removing the old one.  Because the script route migrates passwords, a missing passkey is an inconvenience, never a lockout.

**Consequences.**  README step 9 presents "do nothing, re-enroll opportunistically" as the default.  The passkey tooling (`passkeys.py`, `--mark-passkeys`, `--gap-report`) stays in the repo as optional for migrations that want tracking.  `adopt.py` stays EXPERIMENTAL and is only relevant to the CXP route.  Bridge (docs/IOS-APP-SPEC.md) remains a spec.  Revisit only if a migration with hundreds of passkeys appears, or if passwordless-only accounts (no password fallback) make re-registration impossible — CXP is the only non-recovery route for those.
