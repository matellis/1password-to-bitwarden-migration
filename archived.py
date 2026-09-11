#!/usr/bin/env python3
"""Bring 1Password *archived* items into Bitwarden as archived items.

split.py / import.py skip items whose 1pux state is "archived".  This step
runs after a successful import and handles only those items:

  1. Reads the 1pux export for the account and collects archived items from
     every vault the manifest imported.
  2. Creates each item in the same collection (org mode) or folder (personal
     mode) via `bw create item`, then runs `bw archive item <id>`.
  3. If Bitwarden refuses to archive (Archive needs a premium-enabled
     account; some org/plan combinations may refuse), the item is renamed with an
     "[archived] " prefix instead so it is still recognisable.

Items already present (matched by fingerprint against live *and* archived
items, with or without the prefix) are skipped, so the step is safe to re-run.
--dry-run prints the plan only.  --dedupe trashes newer duplicate archived
items in each folder/collection, keeping the oldest copy.

Usage:
  python3 archived.py --account NAME [--personal] [--vault NAME] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from lib import bwcli, onepux

ARCHIVED_PREFIX = "[archived] "
ARCHIVED_NOTE = "Archived in 1Password."

# `bw create item` enforces the server's *encrypted* size limits (5000 for a
# field value, 10000 for notes).  Encryption plus base64 inflates plaintext by
# roughly 1.4x, so plaintext is clamped well below the limits.  The bulk
# `bw import` path used for live items is more lenient, which is why these
# tighter limits live here and not in onepux.
_CREATE_FIELD_LIMIT = 3500
_CREATE_NOTES_LIMIT = 7000
_TRIM_MARK = "…[trimmed to fit Bitwarden's encrypted size limit]"


def clamp_for_create(item: dict) -> list[str]:
    """Trim custom field values and notes so `bw create item` accepts them.

    Returns the names of fields that were trimmed (for reporting).
    """
    trimmed: list[str] = []
    for f in item.get("fields") or []:
        v = f.get("value")
        if isinstance(v, str) and len(v) > _CREATE_FIELD_LIMIT:
            f["value"] = v[:_CREATE_FIELD_LIMIT] + _TRIM_MARK
            trimmed.append(f.get("name") or "?")
    notes = item.get("notes")
    if isinstance(notes, str) and len(notes) > _CREATE_NOTES_LIMIT:
        item["notes"] = notes[:_CREATE_NOTES_LIMIT] + _TRIM_MARK
        trimmed.append("notes")
    return trimmed


def _load_json(path: Path) -> Any:
    with open(path) as f:
        return json.load(f)


def _ledger_path(account_name: str, personal: bool) -> Path:
    suffix = "-personal" if personal else ""
    return Path("state") / f"{account_name}{suffix}.json"


def _load_ledger(account_name: str, personal: bool) -> dict:
    p = _ledger_path(account_name, personal)
    if p.exists():
        return _load_json(p)
    return {"imported": {}, "failures": {}}


def _save_ledger(account_name: str, ledger: dict, personal: bool) -> None:
    old_umask = os.umask(0o077)
    try:
        Path("state").mkdir(mode=0o700, exist_ok=True)
        p = _ledger_path(account_name, personal)
        with open(p, "w") as f:
            json.dump(ledger, f, indent=2)
        os.chmod(p, 0o600)
    finally:
        os.umask(old_umask)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def prefixed_name(name: str) -> str:
    return name if name.startswith(ARCHIVED_PREFIX) else ARCHIVED_PREFIX + name


def is_archived_live_item(item: dict) -> bool:
    """True when a live Bitwarden item is one this step created."""
    return bool(item.get("archivedDate")) or (item.get("name") or "").startswith(ARCHIVED_PREFIX)


def collect_archived(export_data: dict, files_map: dict, manifest: list[dict],
                     vault_rename: dict, org_id: str) -> dict[str, list[dict]]:
    """Map manifest vaultName -> list of {"item", "files"} for archived items.

    Only vaults present in the manifest are considered.  Items are converted
    with the same converter as the live import; the collection/folder id is
    fixed up by the caller once the real id is known.
    """
    wanted = {entry["vaultName"]: entry for entry in manifest}
    out: dict[str, list[dict]] = {}
    for vault in onepux.vaults(export_data):
        attrs = vault.get("attrs") or {}
        orig = attrs.get("name", attrs.get("uuid", "vault"))
        name = vault_rename.get(orig, orig)
        if name not in wanted:
            continue
        raw_items = [it for it in (vault.get("items") or []) if it.get("state") == "archived"]
        if not raw_items:
            continue
        result = onepux.convert_vault_items(
            raw_items, org_id, "", files_map, include_archived=True,
        )
        entries = [{"item": it, "files": []} for it in result.bulk_items]
        entries += [{"item": e["item"], "files": list(e["files"])} for e in result.attachment_items]
        for e in entries:
            note = e["item"].get("notes")
            e["item"]["notes"] = ARCHIVED_NOTE + ("\n\n" + note if note else "")
        out[name] = entries
    return out


def _archived_in_target(target_id: str, personal: bool) -> list[dict]:
    """Archived items in a folder (personal) or collection (org).

    Plain `bw list items` omits archived items, which is exactly what makes a
    re-run see them as missing and create duplicates.
    """
    out = []
    for it in bwcli.list_archived_items():
        if personal:
            if it.get("folderId") == target_id and not it.get("organizationId"):
                out.append(it)
        elif target_id in (it.get("collectionIds") or []):
            out.append(it)
    return out


def find_duplicates(items: list[dict]) -> list[dict]:
    """Return the newer copies among items sharing a fingerprint (oldest is kept)."""
    by_fp: dict[tuple, list[dict]] = {}
    for it in items:
        by_fp.setdefault(bwcli.item_fingerprint(it), []).append(it)
    extras: list[dict] = []
    for group in by_fp.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda it: (it.get("creationDate") or "", it.get("id") or ""))
        extras.extend(group[1:])
    return extras


def dedupe_vault(vault_name: str, personal: bool, org_id: str | None, dry_run: bool) -> int:
    """Trash newer duplicate *archived* items in one folder/collection.  Returns count."""
    bwcli.sync()
    if personal:
        match = next((f for f in bwcli.list_folders() if f.get("name") == vault_name), None)
    else:
        match = next((c for c in bwcli.list_org_collections(org_id or "") if c.get("name") == vault_name), None)
    if not match:
        print(f"  [{vault_name}] target not found in Bitwarden.")
        return 0
    archived_items = _archived_in_target(match["id"], personal)
    extras = find_duplicates(archived_items)
    for it in extras:
        label = it.get("name") or "?"
        if dry_run:
            print(f"  [{vault_name}] would trash duplicate: {label}")
        else:
            bwcli.delete_item(it["id"])
            print(f"  [{vault_name}] trashed duplicate: {label}")
    print(f"  [{vault_name}] {len(archived_items)} archived, {len(extras)} duplicate(s)"
          f"{' would be' if dry_run else ''} trashed.")
    return len(extras)


def _existing_fps(items: list[dict]) -> set[tuple]:
    fps: set[tuple] = set()
    for it in items:
        fps.add(bwcli.item_fingerprint(it))
        name = it.get("name") or ""
        if name.startswith(ARCHIVED_PREFIX):
            plain = dict(it)
            plain["name"] = name[len(ARCHIVED_PREFIX):]
            fps.add(bwcli.item_fingerprint(plain))
    return fps


def _create_and_archive(item: dict, files: list[str], personal: bool, org_id: str | None) -> str:
    """Create the item, attach files, archive it.  Returns 'archived' or 'prefixed'."""
    if personal:
        created = bwcli.create_personal_item(item)
    else:
        created = bwcli.create_item(item, org_id or "")
    item_id = created["id"]
    for path in files:
        bwcli.create_attachment(Path(path), item_id)
    try:
        bwcli.archive_item(item_id)
        return "archived"
    except bwcli.BWError:
        live = bwcli.get_item(item_id)
        live["name"] = prefixed_name(live.get("name") or "")
        bwcli.edit_item(item_id, live)
        return "prefixed"


def process_vault(vault_name: str, entries: list[dict], personal: bool,
                  org_id: str | None, dry_run: bool) -> dict:
    counts = {"archived": 0, "prefixed": 0, "skipped": 0, "failed": 0}
    failures: list[str] = []

    if dry_run:
        for entry in entries:
            label = entry["item"].get("name") or "?"
            print(f"  [{vault_name}] would create + archive: {label}"
                  + (f" ({len(entry['files'])} attachment(s))" if entry["files"] else ""))
        counts["archived"] = len(entries)
        return {"status": "dry_run", "counts": counts, "failures": []}

    if personal:
        bwcli.sync()
        folders = bwcli.list_folders()
        match = next((f for f in folders if f.get("name") == vault_name), None)
        if not match:
            print(f"  [{vault_name}] Folder not found in Bitwarden — run import.py first.")
            return {"status": "no_target", "counts": counts}
        target_id = match["id"]
        live = bwcli.list_items_in_folder(target_id) + _archived_in_target(target_id, personal=True)
    else:
        bwcli.sync()
        colls = bwcli.list_org_collections(org_id or "")
        match = next((c for c in colls if c.get("name") == vault_name), None)
        if not match:
            print(f"  [{vault_name}] Collection not found in Bitwarden — run import.py first.")
            return {"status": "no_target", "counts": counts}
        target_id = match["id"]
        live = bwcli.list_items_in_collection(target_id, org_id or "") + _archived_in_target(target_id, personal=False)

    existing = _existing_fps(live)

    for entry in entries:
        item = dict(entry["item"])
        if personal:
            item["organizationId"] = None
            item["collectionIds"] = None
            item["folderId"] = target_id
        else:
            item["organizationId"] = org_id
            item["collectionIds"] = [target_id]
        if bwcli.item_fingerprint(item) in existing:
            counts["skipped"] += 1
            continue
        label = item.get("name") or "?"
        trimmed = clamp_for_create(item)
        if trimmed:
            print(f"  [{vault_name}] {label}: trimmed oversized {', '.join(trimmed)}")
        try:
            outcome = _create_and_archive(item, entry["files"], personal, org_id)
            counts[outcome] += 1
        except bwcli.BWError as exc:
            counts["failed"] += 1
            failures.append(f"{label}: {exc}")

    status = "ok" if not failures else "partial"
    return {"status": status, "counts": counts, "failures": failures}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import archived 1Password items as archived Bitwarden items.")
    parser.add_argument("--config", default="config.json", metavar="PATH")
    parser.add_argument("--account", metavar="NAME", required=True)
    parser.add_argument("--vault", metavar="NAME", help="Process only this vault name")
    parser.add_argument("--personal", action="store_true", help="Personal mode (folders, not collections)")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan; write nothing")
    parser.add_argument("--dedupe", action="store_true",
                        help="Trash newer duplicate archived items instead of importing (keeps the oldest copy)")
    args = parser.parse_args()

    config = _load_json(Path(args.config))
    account = next((a for a in config.get("accounts", []) if a.get("name") == args.account), None)
    if account is None:
        sys.exit(f"Account '{args.account}' not found in config.")

    personal = args.personal or account.get("mode") == "personal"
    org_id = None if personal else account.get("bitwardenOrgId")
    name = account["name"]
    work_dir = Path("work") / name
    manifest_path = work_dir / "manifest.json"
    if not manifest_path.exists():
        sys.exit(f"No manifest at {manifest_path}. Run split.py first.")
    manifest = _load_json(manifest_path)
    if args.vault:
        manifest = [v for v in manifest if v["vaultName"] == args.vault]

    print("archived.py — archived items → Bitwarden archive")

    if args.dedupe:
        bwcli.ensure_session(account.get("bitwardenServer", "us"), account.get("bitwardenEmail"))
        print(f"\nAccount: {name}{' (personal mode)' if personal else ''} — dedupe{' [dry run]' if args.dry_run else ''}")
        for entry in manifest:
            dedupe_vault(entry["vaultName"], personal, org_id, args.dry_run)
        return

    export_data, files_map = onepux.parse_export(Path(account["puxPath"]), work_dir / "files")
    per_vault = collect_archived(export_data, files_map, manifest, account.get("vaultRename", {}), org_id or "")

    if not per_vault:
        print(f"\nAccount: {name} — no archived items in the imported vaults. Nothing to do.")
        return

    if not args.dry_run:
        bwcli.ensure_session(account.get("bitwardenServer", "us"), account.get("bitwardenEmail"))

    ledger = _load_ledger(name, personal)
    ledger.setdefault("archived", {})
    print(f"\nAccount: {name}{' (personal mode)' if personal else ''}{' [dry run]' if args.dry_run else ''}")
    any_failed = False
    for vault_name, entries in per_vault.items():
        print(f"  [{vault_name}] {len(entries)} archived item(s)")
        result = process_vault(vault_name, entries, personal, org_id, args.dry_run)
        c = result["counts"]
        print(f"  [{vault_name}] Done — {c['archived']} archived, {c['prefixed']} prefixed"
              f" (could not archive), {c['skipped']} skipped, {c['failed']} failed. Status: {result['status']}")
        for f in result.get("failures", []):
            print(f"    - {f}")
            any_failed = True
        if not args.dry_run and result["status"] != "no_target":
            ledger["archived"][vault_name] = {"timestamp": _now_iso(), **c}
            _save_ledger(name, ledger, personal)

    if any_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
