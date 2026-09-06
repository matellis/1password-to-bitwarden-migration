# Decisions

Dated, intentional decisions about the direction of this toolkit.  Newest first.

## 2026-09-05 — Passkeys: re-registration over CXP at small scale

**Decision.**  For migrations with tens of passkeys, re-registering passkeys per site into Bitwarden is the recommended path (README step 9, Option A).  The CXP apparatus — the Single-Vault Pipeline, `adopt.py` reconciliation, and the spec'd Bridge iOS app — is intentionally **not** being pursued for the current migrations.  Bridge is deprioritized, not cancelled.

**Learnings that drove it.**

- Desktop `.1pux` exports carry no passkey data (confirmed by 1Password, verified empirically — docs/LIMITATIONS.md).
- `op` 2.39.0 exposes no passkey presence either: items confirmed to hold a passkey (via the app's `=passkey` search) show no passkey field, flag, or null placeholder in `op item list` or `op item get`.  There is no automated inventory source; the inventory is a manual worklist, optionally enriched with usernames/URLs via `op item list`.
- CXP shipped in iOS/macOS 26 and Bitwarden's iOS app receives transfers natively — an intermediary app (Bridge) was never required for the transfer itself, only for inventory generation and duplicate minimization.
- CXP carries operational costs: per-vault visibility scoping on 1password.com, community-reported transfer failures on large vaults, and duplicate items needing reconciliation.
- Passkeys are designed for multiple enrollments per account: adding a new passkey does not require removing the old one.  Because the script route migrates passwords, there is no lockout risk.  At ~1–3 minutes per site, 43 passkeys ≈ 1–2 hours spread across five people — less total effort than operating the CXP pipeline.
- The inventory built for `--mark-passkeys` doubles as the re-registration worklist; `passkeys.py --from-bitwarden` / `--gap-report` verify completion either way.

**Consequences.**  `adopt.py` stays EXPERIMENTAL and is only relevant to the CXP route.  Bridge (docs/IOS-APP-SPEC.md) remains a spec.  Revisit this decision if a migration with hundreds of passkeys appears, or if passwordless-only accounts (no password fallback) make re-registration impossible — CXP is the only non-recovery route for those.

**Follow-up, same day.**  Even the inventory file turned out to be overhead for the re-registration route: the app's `=passkey` search is itself the live checklist, and deleting each 1Password passkey as it is re-registered shrinks that search to zero — which is also the completion signal.  The markdown inventory, `--mark-passkeys` markers, and `--gap-report` cross-check are now optional extras for those who want item-level proof, not required steps.  (Lesson recorded: when the route changed, the tooling assumptions from the old route should have been re-examined with it.)
