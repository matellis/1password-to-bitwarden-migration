# Spec: Conduit, a macOS Migration Assistant for the toolkit

Status: draft, not yet built. This document specifies a macOS 26 app that makes the
Python toolkit's runbook clickable for non-technical users. The underlying scripts
remain unchanged; the app is a shell that drives them and renders their output.

Working title: **Conduit**. No trademark of the source or destination manager appears
in the name, the icon, or any promotional position. Descriptive text may name either
product in the nominative sense — identifying what the tool migrates — following the
same rationale in the companion iOS spec.

## Elevator pitch

A Migration Assistant-style macOS app that guides a less technical user through every
step of the toolkit's runbook: drop the export file, map it to a destination org, review
the plan, import vault by vault, verify the results, and hand off passkeys to the iOS
pipeline. The Python engine does all the work. The app makes it approachable.

## Problem context

The toolkit is correct and complete, but its runbook has seven numbered steps, two
configuration files, a BW_SESSION export, and a cleanup section involving `rm -P`. For
an IT administrator migrating a small team, that is fine. For anyone else, it is a full
afternoon of reading and a real risk of skipping a safety step.

The macOS app maps each runbook step to a screen with a clear action and plain-language
feedback. It enforces the dry-run-first philosophy, keeps the ledger visible throughout,
and handles the cleanup securely. The Python scripts are the engine; the app adds no
migration logic of its own.

## Relationship to the toolkit and the iOS companion

The three components share one pipeline:

- The Python toolkit (`split.py`, `import.py`, `verify.py`, `passkeys.py`, `adopt.py`)
  is the engine. It is versioned separately and pinned in the app's bundle.
- This macOS app is the desktop shell. It drives the engine and renders its output.
- Bridge (iOS, `docs/IOS-APP-SPEC.md`) handles the passkey CXP transfer and produces
  the checklist JSON. Conduit's passkey handoff screen points directly to that pipeline
  and can import the resulting checklist for gap reporting.

Neither app duplicates the other's work. The boundary: desktop export files and
Bitwarden org imports are Conduit's domain; passkey transfer is Bridge's.

## Engine embedding

Three options exist for shipping the Python runtime alongside the app. Only one is
viable.

**Option A — PyInstaller-style frozen binary.** Each script compiled to a self-contained
executable with its Python interpreter and standard library bundled. Startup cost is
high (the frozen importer unpacks hundreds of files into a temp directory on first
launch). Code signing is awkward because the binary list grows with each PyInstaller
version. Notarization has historically rejected frozen binaries that embed a writeable
temp path. Maintenance burden is non-trivial: every Python update requires a rebuild.

**Option B — Private Python.framework (recommended).** A stripped `Python.framework`
(the CPython distributed by python.org, arm64 + x86_64 fat) embedded in
`Conduit.app/Contents/Frameworks/`. The scripts ship verbatim in
`Conduit.app/Contents/Resources/engine/`. Swift calls them via `Process` with the
bundled interpreter path. The framework is a proper Mach-O bundle: `codesign --deep`
signs it cleanly, `notarytool` accepts it, and the hardened runtime flag requires no
exceptions beyond `com.apple.security.cs.allow-unsigned-executable-memory` (which
Python itself requires; python.org's official framework already carries it). App size
is roughly 20–25 MB for the stripped framework plus the scripts. Startup cost is
negligible — `python3 split.py` runs in under a second on any Apple Silicon Mac.
Maintenance: update the pinned framework version alongside toolkit releases.

**Option C — Native Swift port.** Maximum performance and smallest binary, but the
scripts would need to be rewritten in Swift, diverging the engine. Two codebases
implementing the same migration logic means bugs fixed in one may not reach the other.
This violates the one-engine principle and is not viable.

**Recommendation: Option B.** Same scripts, same behavior, straightforward signing
path, reasonable size. The framework version is pinned in the bundle's `Info.plist`
and displayed in the About screen.

### BW CLI strategy

The Bitwarden CLI (`bw`) is bundled inside `Conduit.app/Contents/Resources/bin/bw`
as a fat binary (arm64 + x86_64). At launch the app resolves the CLI path: bundled
binary first, then a PATH search as a fallback for users who manage their own
installation. The resolved path is logged to the run log and shown in Preferences.

`BW_SESSION` is held in memory only — as a `String` on the `@MainActor` session object
— for the duration of the import phase. It is never written to disk, never logged,
never placed in the Keychain, and is zeroed when the import phase ends or the app quits.
The rationale for not using the Keychain: a session token has a short TTL and is only
needed during the import run; storing it persistently would widen the attack surface
without user benefit. The user unlocks once per session via the in-app sheet (see
Screen 4 below).

The working directory is `~/Library/Application Support/Conduit/sessions/<uuid>/`
created at mode 700. It holds `work/`, `state/`, and the run log. The session
directory is never exposed in Finder by default and is listed under the "clean up"
section of the post-migration checklist.

## Sandbox decision

The app ships **notarized but not sandboxed**. The toolkit requires `subprocess` access
(to run `bw`, `python3`, and optionally `rm -P`), full read access to user-chosen
`.1pux` files anywhere on disk, and write access to `~/Library/Application Support/`.
The macOS App Sandbox cannot grant arbitrary `subprocess` usage or `open(2)` access to
arbitrary user-picked paths without entitlements that Apple does not grant to apps in
this category. A non-sandboxed, hardened-runtime, notarized distribution via GitHub
Releases is the correct path. The app must use `NSOpenPanel` for all file picking so
the user's selection is explicit and auditable.

## UX flows

The app is a single-window assistant. A persistent sidebar lists the accounts loaded
from `config.json` and their ledger state. The main area steps through the runbook.

### Screen 1 — Welcome and safety explainer

Full-width card with two sentences: what the app does, what it never does (never writes
to the source, additive-only on the destination). A prominent secondary line: "Your
source data stays unchanged. We read it; we never write to it."

Below: two action rows with glyphs:
- "Open an existing session" (resumes from a prior run's state directory)
- "Start a new migration" (proceeds to Screen 2)

No "skip intro" — the safety language is intentional. But it is short.

### Screen 2 — Drop or pick export files

One section per account to migrate. For each, a drop target that accepts `.1pux` files
plus a "Choose file" button backed by `NSOpenPanel`. The exemplar shows three accounts:
family, team, and individual.

Validation runs immediately on drop: the app checks that the file is a valid `.1pux`
archive (zip with an `export.data` entry) and shows a green checkmark or a plain-
language error ("This file doesn't look like a 1PUX export — try exporting again").

A note below the drop targets: "Private vaults are not included in admin exports. Each
user migrates their own — see the passkey handoff screen at the end."

### Screen 3 — Account-to-org mapping wizard

For each account, the user provides the Bitwarden organization ID. A text field with
placeholder "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx". A "Validate" button calls
`bw list collections --organizationid <id>` (using the bundled CLI) and shows a
green checkmark and the org's display name on success, or a plain-language error on
failure.

Below the org field: "What to do if a collection already has items." Three plain-
language radio options mapped to `onExisting`:

- **Stop and tell me** (`refuse`) — recommended, shown first
- **Skip items that already exist** (`skip`)
- **Import anyway** (`allow`) — shown with a brief caution note

The wizard writes `config.json` into the session directory. The user never edits JSON.

A "Vault rename" disclosure triangle opens an optional table where the user can retype
any vault name as it should appear in Bitwarden. Left column: source vault name (read
from the `.1pux`). Right column: editable destination collection name.

### Screen 4 — Plan view

The app runs `split.py` (offline, no network) and renders the output as a native table:
one row per vault, columns for item count, attachment count, and passkey presence.
Vaults with passkeys show a distinct glyph and a note: "Contains passkeys — these will
not move through this route. See the passkey handoff screen."

A disclosure triangle per vault row expands to the item-type breakdown (logins, secure
notes, cards, identities, SSH keys, documents).

A "Run dry run" label above the table clarifies: "Nothing has been written yet. Review
this before continuing."

The "Start Import" button is disabled until at least one account's files and org are
validated.

### Screen 5 — Bitwarden unlock

A sheet drops down before import starts. "Unlock your Bitwarden vault to continue."
A password field (not stored) and an Unlock button. The app runs
`bw unlock --raw` with the entered password, captures the session token into memory,
and dismisses the sheet. On failure, a plain-language error ("Wrong password — try
again") stays in the sheet.

If the user has the CLI's biometric unlock configured, an "Use Touch ID" row appears
above the password field and calls `bw unlock --raw` via biometric challenge.

### Screen 6 — Import with live progress

For each account (one at a time), the app runs `import.py --account <name>` and
streams stdout line by line into a live log view. The main area shows:

- A progress bar per account (item count from the plan / items confirmed in the ledger)
- The current vault being imported, in large type
- A scrollable log panel (monospaced, 11 pt) below — collapsed by default, expandable

The ledger file (`state/<account>.json`) is parsed after each vault completes and the
sidebar updates the ledger state badge immediately.

If import exits non-zero, a plain-language error sheet appears with the relevant log
lines highlighted and a "Try again" button (which re-runs with `--force` only for the
failed vault, not the whole account).

`BW_SESSION` is zeroed from memory when all accounts finish or the sheet is dismissed.

### Screen 7 — Verify report

The app runs `verify.py --account <name>` per account and renders the results as a
grouped list:

- Each vault: a PASS or FAIL badge (green / red, high-contrast, no reliance on color
  alone — PASS and FAIL text is always shown)
- FAIL rows expand to show the mismatched fields in plain language:
  "Login 'GitHub (team)' — password differs between source and destination"
- A summary line at the top: "47 vaults verified. 47 passed. 0 failed."

An "Export report" button writes the verify output to a user-chosen location as a
plain-text file.

### Screen 8 — Passkey handoff

This screen is shown after verify, whether or not the user has passkeys. It serves as
the transition to the iOS pipeline.

Two sections:

**Build the passkey inventory.** Instructions to search `=passkey` in the source app
and transcribe the results. A text editor area in the screen accepts the markdown list
directly. A "Generate checklist" button runs `passkeys.py --account <name> --manual`
with the pasted content and opens the resulting `work/passkeys/index.html` in the
default browser. A note: "Open that page on your phone and check off each passkey as
you transfer it."

**Use the iOS companion.** A plain-language description of Bridge and the CXP transfer
route, with a link to the iOS app's TestFlight or App Store page. If a Bridge checklist
JSON is available (dragged in or picked with NSOpenPanel), a "Import checklist" button
runs `passkeys.py --gap-report` and shows the result inline.

A "Mark passkey gaps in imported items" toggle (off by default) maps to
`split.py --mark-passkeys`. When on, items that had passkeys will carry a notice in
their Bitwarden notes field.

### Screen 9 — Post-migration checklist and cleanup

A checklist the user ticks manually:
- [ ] Verify sign-in works on at least three sites
- [ ] Confirm passkey sign-in works (if passkeys were transferred)
- [ ] Invite users to Bitwarden orgs and assign collection permissions
- [ ] Keep the source manager active for one full billing cycle

A "Clean up sensitive files" section with:
- A list of what will be deleted (`exports/*.1pux`, `work/`, `state/`)
- A "Shred everything" button that runs `rm -P exports/*.1pux && rm -Prf work/ state/`
  inside the session directory and then calls `bw lock`
- A confirmation alert before any deletion: "This permanently deletes the export files
  and intermediate data. Your Bitwarden data is not affected."

After cleanup the session directory is marked complete in the sidebar and the app shows
a final card: "Migration complete."

## Safety model surfaced as UI

The toolkit's safety properties are made visible throughout:

- **Ledger state** is shown as a badge on each account in the sidebar: Not started /
  In progress / Complete. Clicking opens a detail view of the ledger JSON rendered as a
  readable table.
- **Policy picker** (Screen 3) defaults to `refuse` and uses plain language.
- **Workdir disclosure** — a Preferences pane shows the session directory path, its
  mode (700), and a button to reveal it in Finder.
- **Dry-run first** — the plan view (Screen 4) runs before any import button is enabled.
- **No cloud sync** — the session directory is never in iCloud Drive. The app checks for
  this on startup and warns if the resolved path is inside a synced folder.
- **Shred button** (Screen 9) uses `rm -P`, not `rm`, and logs the result.

## Design system

The app uses the macOS Sequoia-era grouped utility design language across all screens.

### Window structure

A single `NSWindow` with a sidebar (200 pt wide, native sidebar material with a restrained
cool wash) on the left and a detail pane on the right. The sidebar lists accounts from
`config.json` with their ledger-state badges and a "Preferences" entry at the bottom.
The assistant flow occupies the detail pane in a standard 800 pt column with 30 pt
horizontal margins and 20 pt vertical margins.

The sidebar uses the two-trailing-corner treatment (18 pt continuous radius on the outer
trailing corners only). A subtle hairline and restrained shadow (black at 12 percent,
radius 12 pt, x 3 pt, y 2 pt) establish depth. No collapse control.

### Typography

System typeface throughout. Pane titles at 18 pt semibold. Section titles at 13 pt
semibold. Row titles at 13 pt regular. Secondary and detail text at 11 pt regular,
secondary color. Monospaced text for the live log panel, ledger values, org UUIDs,
and command output only.

### Spacing

Standard spacing tokens: 2 pt for tightly related labels, 4 pt for title-to-subtitle,
6 pt for section-to-surface and footer-to-surface, 10 pt for standard row content gaps,
14 pt between pane-level blocks. No arbitrary spacing values at screen level.

### Surfaces and grouping

Three levels of hierarchy: the white/near-white window surface, the raised sidebar
material, and light-gray grouped content surfaces in the detail pane. Grouped surfaces
use an 8 pt continuous corner radius with no border and no shadow — hierarchy comes from
background contrast, type, and spacing alone.

Standard content rows are at least 52 pt high (28 pt content, 12 pt above, 12 pt below).
Variable-height rows (the log panel, the vault drill-down, the checklist) retain at
least 12 pt top and bottom padding and grow as needed.

Separators appear only between peer rows — not above the first row or below the last.
They are 1 pt at the macOS semantic separator color at 38 percent opacity. No decorative
horizontal rules anywhere in the document or in the UI.

### Colors and controls

Semantic dynamic colors throughout — no fixed light-mode grays. Accent color for the
primary action button and selection. Destructive actions (the shred button) use the
semantic red role with bordered styling. PASS/FAIL badges use semantic green and red
with explicit text labels for accessibility.

Switches for binary settings use a narrow capsule with circular thumb (34 × 20 pt track,
16 pt thumb). Primary buttons use `borderedProminent`. Secondary buttons use `bordered`.

### Accessibility

Full VoiceOver support on every screen. PASS/FAIL conveyed by both color and text.
Progress bars have accessible descriptions. The live log is a live region. Keyboard
navigation through the wizard without mouse. Dynamic type is honored where AppKit
allows it.

### Screen inventory

| # | Name | Primary engine call |
| --- | --- | --- |
| 1 | Welcome | None |
| 2 | Drop export files | None (local file validation) |
| 3 | Account-to-org mapping | `bw list collections` |
| 4 | Plan view | `split.py` |
| 5 | Bitwarden unlock | `bw unlock --raw` |
| 6 | Import with live progress | `import.py` |
| 7 | Verify report | `verify.py` |
| 8 | Passkey handoff | `passkeys.py` |
| 9 | Post-migration checklist | `rm -P`, `bw lock` |

## Distribution

The app lives in a separate public GitHub repository, initially provisionally named
`conduit-migration-app` or similar. The toolkit repo links to it from the README.

### Versioning

`<marketing>.<build>` where marketing is bumped for each user-facing release and build
is the git commit count (monotonic, no state files). The pinned toolkit engine version
is also displayed in About.

### Release pipeline

`build.sh` mirrors the established pattern:
- `./build.sh` — dev build, host arch, ad-hoc or Apple Development signing, no notarization
- `./build.sh --run` — build and launch
- `./build.sh --install` — build and copy to `/Applications`
- `./build.sh --dist` — universal binary (arm64 + x86_64), Developer ID signing with a
  SHA-1 identity fingerprint (not a name, to avoid ambiguity when multiple Developer ID
  certs coexist), hardened runtime (`--options runtime`), trusted timestamp, notarization
  via `xcrun notarytool submit --wait`, then `xcrun stapler staple` before packaging

The distribution artifact is a signed, notarized, stapled DMG (created by a separate
`Scripts/make-dmg.sh`). The DMG background is plain; the layout places the app icon
and an Applications folder alias. It is produced alongside a zip for users who prefer
that format.

Both are uploaded as assets to the GitHub Release. The release is tagged `v<marketing>`
in git.

### Update check

On launch (and once per day in the background) the app queries the GitHub Releases API
for the latest release tag:

```
GET https://api.github.com/repos/<owner>/conduit-migration-app/releases/latest
```

If the returned tag is newer than the running build's marketing version, a discreet
banner appears in the sidebar with a "View release" button (opens the release page in
the default browser). No automatic download, no third-party update framework. The user
downloads and installs manually. This keeps the update surface minimal and the signing
story simple.

The update check respects `NSAppTransportSecurity` and requires TLS. It carries no
user identifier. If the request fails, the check silently retries the next day.

## Milestones

**M0 — spike (gates the project).** Swift shell app that locates the bundled Python
interpreter, runs `split.py` on a real `.1pux` file, and renders the plan table
natively. Target: one to two days. If `split.py` cannot be driven cleanly from Swift's
`Process` with the bundled interpreter, the embedding strategy is revisited before
any UI work.

**M1 — full plan view.** Screens 1–4. Config wizard writes `config.json`. Plan renders
with vault drill-down. No import yet.

**M2 — import and verify.** Screens 5–7. BW_SESSION handled in memory. Live log.
Ledger reflected in sidebar. Verify report rendered.

**M3 — passkey handoff and cleanup.** Screens 8–9. Bridge checklist import. Shred
button. Post-migration checklist.

**M4 — distribution.** Notarized DMG on GitHub Releases. Update check wired.
Documentation updated in the toolkit README.

Estimated total beyond M0: three to five weeks of part-time work, dominated by the
import/verify plumbing and notarization, not by the wizard UI.

## Open questions

**1. Notarization of bundled Python.framework.** Apple's notary service has historically
required that every Mach-O inside a bundle be signed with hardened runtime. The
python.org CPython framework is distributed pre-signed, but its signing identity may
need to be replaced with the app developer's own Developer ID to satisfy notarization.
Confirm at M0 whether the framework can be signed `--deep` with the app's identity and
whether any Python extension modules (`.so` files) inside the framework require the
`com.apple.security.cs.allow-unsigned-executable-memory` entitlement. Verify against
a real notarization run before committing to this path.

**2. Notarization of the bundled bw CLI.** The Bitwarden CLI binary is a third-party
Mach-O. It must be re-signed with the app's Developer ID identity before being bundled.
Bitwarden's own distribution terms should be checked for any restriction on
redistribution of the binary. The fallback — requiring the user to install the CLI
themselves — is always available if redistribution is not permitted; the app's PATH
search covers this case transparently.

**3. Sandbox vs notarized-non-sandboxed.** This spec recommends the non-sandboxed path
(see "Sandbox decision" above). If a future distribution path requires sandboxing (e.g.
Mac App Store), the subprocess usage would need to move into an XPC service with a
narrow interface. That is a significant re-architecture and is out of scope for v1.

**4. Size budget.** The stripped Python.framework (CPython 3.12, fat) is approximately
20 MB. The bw CLI is approximately 40 MB. The scripts and assets are negligible. Total
app bundle: roughly 65 MB before compression, 30–35 MB as a compressed DMG. This is
acceptable for a migration tool that is not installed permanently. Confirm sizes against
the actual framework build at M0.

**5. Python version pinning.** The bundled framework version is pinned in
`Info.plist` as `BundledPythonVersion`. Engine script compatibility is tested against
that version. A toolkit update that requires a newer Python version bumps both the
scripts and the pinned framework together in a coordinated release.

## Non-goals

- No editing of vault items — the app is read-write only in the direction of import.
- No direct reading of the source manager beyond `.1pux` and CXP (via Bridge). No
  browser extension integration, no agent, no clipboard reading.
- No Windows or Linux.
- No telemetry, no analytics, no network calls except the GitHub Releases update check.
- No multi-tenant SaaS mode — the app is a local tool for a single migration run.
