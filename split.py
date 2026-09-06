#!/usr/bin/env python3
"""Split a .1pux export into per-vault Bitwarden import JSON files.

Reads config.json (or --config PATH), optionally filtered by --account.
Writes work/<account>/<vault-slug>.json (bulk items),
       work/<account>/<vault-slug>.attachments.json (items with file attachments),
       work/<account>/manifest.json (per-vault counts + collection metadata).

All work/ subdirs created with mode 700.  Attachment files extracted from the
zip land in work/<account>/files/ and are referenced by path in attachments.json.

--personal mode processes ONLY Private (type P) vaults and emits folder-based
import JSON suitable for import into My vault (no org required).

Every non-skipped vault needs a vaultDestination entry in config.json (shared,
owner-only, or personal). When a vault is missing one, split.py writes a guess
into config.json for the user to confirm: "shared" if the vault name contains
"shared", "personal" if it contains "private" or "personal", otherwise "" (no
guess). Existing entries, including deliberately blank ones, are never
overwritten. Org mode still exits non-zero so the user reviews the guesses
before re-running; personal mode warns and skips the vault instead.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import onepux
from lib import passkey_inventory as pkinv

SSH_KEY_SUPPORTED = True

VALID_DESTINATIONS = {"shared", "owner-only", "personal"}


def _normalize_destination(value) -> tuple[str | None, list[str]]:
    """Normalize a vaultDestination config value into (destination, share_with).

    `value` may be:
      - a plain string: "shared" / "owner-only" / "personal"
      - an object: {"destination": "shared", "shareWith": ["a@example.com", ...]}

    `shareWith` is only valid alongside destination "shared", and when present
    must be a non-empty list of strings. Returns (None, []) when `value` is
    absent, blank, or malformed in any way — the caller treats that as
    unclassified.
    """
    if isinstance(value, str):
        if value in VALID_DESTINATIONS:
            return value, []
        return None, []

    if isinstance(value, dict):
        allowed_keys = {"destination", "shareWith"}
        if not set(value.keys()) <= allowed_keys:
            return None, []
        destination = value.get("destination")
        if destination not in VALID_DESTINATIONS:
            return None, []
        if "shareWith" not in value:
            return destination, []
        if destination != "shared":
            return None, []
        share_with = value["shareWith"]
        if not isinstance(share_with, list) or not share_with:
            return None, []
        if not all(isinstance(e, str) for e in share_with):
            return None, []
        return destination, list(share_with)

    return None, []


def _guess_vault_destination(name: str) -> str:
    """Guess a vaultDestination value from a vault name (case-insensitive substring match).

    Returns 'shared' if the name contains 'shared', 'personal' if it contains
    'private' or 'personal', otherwise '' (no confident guess).
    """
    lower = name.lower()
    if "shared" in lower:
        return "shared"
    if "private" in lower or "personal" in lower:
        return "personal"
    return ""


def _write_config(config_path: Path, config: dict) -> None:
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


def _load_config(config_path: Path) -> dict:
    if not config_path.exists():
        sys.exit(f"Config not found: {config_path}\nCopy config.example.json to config.json and fill in your values.")
    with open(config_path) as f:
        return json.load(f)


def _make_work_dir(account_name: str) -> Path:
    old_umask = os.umask(0o077)
    try:
        work = Path("work") / account_name
        work.mkdir(parents=True, mode=0o700, exist_ok=True)
        files_dir = work / "files"
        files_dir.mkdir(mode=0o700, exist_ok=True)
    finally:
        os.umask(old_umask)
    return work


def _mark_items(items: list[dict], inventory: list[dict]) -> int:
    """Append passkey marker to notes of matching items. Returns count marked."""
    count = 0
    for item in items:
        if pkinv.matches_inventory(item, inventory):
            item["notes"] = pkinv.append_marker(item.get("notes"))
            count += 1
    return count


def _check_vault_destinations(
    vault_list: list[dict],
    vault_rename: dict[str, str],
    skip_vault_types: set[str],
    vault_destination: dict[str, str],
) -> list[str]:
    """Return original names of non-skipped vaults that lack a valid destination classification."""
    unclassified = []
    for vault in vault_list:
        attrs = vault.get("attrs") or {}
        vault_type = attrs.get("type", "")
        vault_name = attrs.get("name", attrs.get("uuid", "vault"))
        if vault_type in skip_vault_types:
            continue
        destination, _ = _normalize_destination(vault_destination.get(vault_name))
        if destination is None:
            unclassified.append(vault_name)
    return unclassified


def _check_vault_destinations_personal(
    vault_list: list[dict],
    vault_rename: dict[str, str],
    vault_destination: dict[str, str],
) -> list[str]:
    """Return original names of non-P vaults in a personal-mode account that lack a valid destination."""
    unclassified = []
    for vault in vault_list:
        attrs = vault.get("attrs") or {}
        vault_type = attrs.get("type", "")
        vault_name = attrs.get("name", attrs.get("uuid", "vault"))
        if vault_type == "P":
            continue
        destination, _ = _normalize_destination(vault_destination.get(vault_name))
        if destination is None:
            unclassified.append(vault_name)
    return unclassified


def _process_account(account: dict, work_dir: Path, include_archived: bool,
                     inventory: list[dict] | None = None,
                     config: dict | None = None, config_path: Path | None = None) -> list[dict]:
    pux_path = Path(account["puxPath"])
    if not pux_path.exists():
        sys.exit(f"Export file not found: {pux_path}")

    org_id = account["bitwardenOrgId"]
    skip_vault_types = set(account.get("skipVaultTypes", ["P"]))
    vault_rename = account.get("vaultRename", {})
    vault_destination = account.get("vaultDestination", {})

    files_dir = work_dir / "files"
    export_data, files_map = onepux.parse_export(pux_path, files_dir)

    vault_list = onepux.vaults(export_data)

    unclassified = _check_vault_destinations(
        vault_list, vault_rename, skip_vault_types, vault_destination
    )
    if unclassified:
        lines = []
        added_any = False
        for name in unclassified:
            if name in vault_destination:
                raw = vault_destination[name]
                if isinstance(raw, dict):
                    lines.append(
                        f'  "{name}": invalid entry (expected "shared"/"owner-only"/"personal"'
                        f' or {{"destination": "shared", "shareWith": [...]}})'
                    )
                else:
                    lines.append(f'  "{name}": needs a value filled in')
            else:
                guess = _guess_vault_destination(name)
                vault_destination[name] = guess
                added_any = True
                lines.append(f'  "{name}": {guess if guess else "(blank)"}')
        account["vaultDestination"] = vault_destination
        if added_any and config is not None and config_path is not None:
            _write_config(config_path, config)
        sys.exit(
            f"Account '{account.get('name', '?')}': unclassified vaults — config.json updated:\n"
            + "\n".join(lines) + "\n"
            f"Review/confirm vaultDestination for this account in config.json, then re-run.\n"
            f"Valid destinations: shared, owner-only, personal"
        )

    manifest_entries: list[dict] = []

    for vault in vault_list:
        attrs = vault.get("attrs") or {}
        vault_type = attrs.get("type", "")
        vault_name_orig = attrs.get("name", attrs.get("uuid", "vault"))
        vault_name = vault_rename.get(vault_name_orig, vault_name_orig)

        if vault_type in skip_vault_types:
            continue

        destination, share_with = _normalize_destination(
            vault_destination.get(vault_name_orig, "shared")
        )
        if destination == "personal":
            continue

        items = vault.get("items") or []
        collection_id, collection = onepux.make_collection(org_id, vault_name)
        slug = onepux.vault_slug(attrs)

        result = onepux.convert_vault_items(
            items,
            org_id,
            collection_id,
            files_map,
            include_archived=include_archived,
            ssh_key_supported=SSH_KEY_SUPPORTED,
        )

        marked = 0
        if inventory:
            att_all = [e["item"] for e in result.attachment_items]
            marked += _mark_items(result.bulk_items, inventory)
            marked += _mark_items(att_all, inventory)

        bulk_path = work_dir / f"{slug}.json"
        import_doc = onepux.make_import_doc(org_id, collection, result.bulk_items)
        _write_secure(bulk_path, import_doc)

        attach_path = work_dir / f"{slug}.attachments.json"
        _write_secure(attach_path, {
            "collectionId": collection_id,
            "organizationId": org_id,
            "items": [
                {
                    "item": entry["item"],
                    "files": [str(p) for p in entry["files"]],
                }
                for entry in result.attachment_items
            ],
        })

        total = len(items)
        manifest_entry = {
            "vaultName": vault_name,
            "vaultType": vault_type,
            "destination": destination,
            "slug": slug,
            "collectionId": collection_id,
            "collectionName": vault_name,
            "counts": {
                "total": total,
                "bulk": len(result.bulk_items),
                "attachment": len(result.attachment_items),
                "archivedSkipped": result.archived_count,
                "dupesCollapsed": result.dupe_count,
                "passkeysMarked": marked,
            },
        }
        if share_with:
            manifest_entry["shareWith"] = share_with
        manifest_entries.append(manifest_entry)

    return manifest_entries


def _process_account_personal(account: dict, work_dir: Path, include_archived: bool,
                              inventory: list[dict] | None = None,
                              config: dict | None = None, config_path: Path | None = None) -> list[dict]:
    pux_path = Path(account["puxPath"])
    if not pux_path.exists():
        sys.exit(f"Export file not found: {pux_path}")

    vault_rename = account.get("vaultRename", {})
    vault_destination = account.get("vaultDestination", {})

    files_dir = work_dir / "files"
    export_data, files_map = onepux.parse_export(pux_path, files_dir)

    vault_list = onepux.vaults(export_data)

    unclassified = _check_vault_destinations_personal(vault_list, vault_rename, vault_destination)
    added_any = False
    for n in unclassified:
        if n in vault_destination:
            raw = vault_destination[n]
            if isinstance(raw, dict):
                print(
                    f"  [{account.get('name', '?')}] Warning: vault {n!r} has an invalid"
                    f' vaultDestination entry in config.json (expected "shared"/"owner-only"'
                    f'/"personal" or {{"destination": "shared", "shareWith": [...]}})'
                    f" — skipped (org mode handles it).",
                    file=sys.stderr,
                )
            else:
                print(
                    f"  [{account.get('name', '?')}] Warning: vault {n!r} needs a value filled in"
                    f" for vaultDestination in config.json — skipped (org mode handles it).",
                    file=sys.stderr,
                )
        else:
            guess = _guess_vault_destination(n)
            vault_destination[n] = guess
            added_any = True
            label = guess if guess else "(blank)"
            print(
                f"  [{account.get('name', '?')}] Warning: vault {n!r} has no vaultDestination"
                f" in personal mode — skipped (org mode handles it); added to config.json as {label}.",
                file=sys.stderr,
            )
    if added_any:
        account["vaultDestination"] = vault_destination
        if config is not None and config_path is not None:
            _write_config(config_path, config)

    manifest_entries: list[dict] = []

    for vault in vault_list:
        attrs = vault.get("attrs") or {}
        vault_type = attrs.get("type", "")
        vault_name_raw = attrs.get("name", attrs.get("uuid", "vault"))
        vault_name = vault_rename.get(vault_name_raw, vault_name_raw)

        if vault_type != "P":
            dest, _ = _normalize_destination(vault_destination.get(vault_name_raw))
            if dest != "personal":
                continue

        slug = onepux.vault_slug(attrs)

        items = vault.get("items") or []
        folder_id = str(uuid.uuid4())
        folder = {"id": folder_id, "name": vault_name}

        # Use dummy org/coll for conversion, then strip org fields and wire folder.
        result = onepux.convert_vault_items(
            items,
            "",
            folder_id,
            files_map,
            include_archived=include_archived,
            ssh_key_supported=SSH_KEY_SUPPORTED,
        )

        for item in result.bulk_items:
            item["organizationId"] = None
            item["collectionIds"] = None
            item["folderId"] = folder_id

        for entry in result.attachment_items:
            it = entry["item"]
            it["organizationId"] = None
            it["collectionIds"] = None
            it["folderId"] = folder_id

        marked = 0
        if inventory:
            att_all = [e["item"] for e in result.attachment_items]
            marked += _mark_items(result.bulk_items, inventory)
            marked += _mark_items(att_all, inventory)

        bulk_path = work_dir / f"{slug}.json"
        _write_secure(bulk_path, {
            "encrypted": False,
            "folders": [folder],
            "items": result.bulk_items,
        })

        attach_path = work_dir / f"{slug}.attachments.json"
        _write_secure(attach_path, {
            "folderId": folder_id,
            "items": [
                {
                    "item": entry["item"],
                    "files": [str(p) for p in entry["files"]],
                }
                for entry in result.attachment_items
            ],
        })

        manifest_entries.append({
            "vaultName": vault_name,
            "vaultType": vault_type,
            "destination": "personal",
            "slug": slug,
            "folderId": folder_id,
            "folderName": vault_name,
            "personal": True,
            "counts": {
                "total": len(items),
                "bulk": len(result.bulk_items),
                "attachment": len(result.attachment_items),
                "archivedSkipped": result.archived_count,
                "dupesCollapsed": result.dupe_count,
                "passkeysMarked": marked,
            },
        })

    return manifest_entries


def _write_secure(path: Path, data: dict | list) -> None:
    old_umask = os.umask(0o177)
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(path, 0o600)
    finally:
        os.umask(old_umask)


def _print_table(account_name: str, entries: list[dict]) -> None:
    show_marked = any(e["counts"].get("passkeysMarked", 0) > 0 for e in entries)
    print(f"\nAccount: {account_name}")
    header = (
        f"  {'Vault':<30} {'Type':<6} {'Destination':<12}"
        f" {'Total':>7} {'Bulk':>7} {'Attach':>7}"
        f" {'Archived':>9} {'Dupes':>6}"
        + (f" {'Marked':>7}" if show_marked else "")
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for e in entries:
        c = e["counts"]
        dest = e.get("destination", "")
        row = (
            f"  {e['vaultName']:<30} {e['vaultType']:<6} {dest:<12}"
            f" {c['total']:>7} {c['bulk']:>7} {c['attachment']:>7}"
            f" {c['archivedSkipped']:>9} {c['dupesCollapsed']:>6}"
        )
        if show_marked:
            row += f" {c.get('passkeysMarked', 0):>7}"
        print(row)


def _is_personal(account: dict, args: argparse.Namespace) -> bool:
    return args.personal or account.get("mode") == "personal"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Split a .1pux export into per-vault Bitwarden import files."
    )
    parser.add_argument("--config", default="config.json", metavar="PATH")
    parser.add_argument("--account", metavar="NAME", help="Process only this account name")
    parser.add_argument("--include-archived", action="store_true", help="Include archived items")
    parser.add_argument(
        "--personal", action="store_true",
        help="Personal mode: process only Private (type P) vaults, emit folder-based import JSON"
    )
    parser.add_argument(
        "--mark-passkeys", metavar="FILE",
        help=(
            "Passkey inventory file (markdown '- Site | username | url' format or "
            "Bridge JSON {entries:[{title,username,url}]}). Login items whose fingerprint "
            "matches an inventory entry get a passkey-migration notice appended to their notes."
        ),
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    config = _load_config(config_path)
    accounts = config.get("accounts", [])

    if not accounts:
        sys.exit("No accounts found in config.")

    if args.account:
        accounts = [a for a in accounts if a.get("name") == args.account]
        if not accounts:
            sys.exit(f"Account '{args.account}' not found in config.")

    inventory: list[dict] | None = None
    if args.mark_passkeys:
        inv_path = Path(args.mark_passkeys)
        if not inv_path.exists():
            sys.exit(f"Inventory file not found: {inv_path}")
        inventory = pkinv.load_inventory(inv_path)
        print(f"Passkey inventory loaded: {len(inventory)} entries from {inv_path}")

    print("split.py — 1pux → Bitwarden per-vault import files")
    if args.personal:
        print("Mode: personal (Private vaults only → My vault folders)")

    manifest_all: dict[str, list] = {}
    for account in accounts:
        name = account.get("name", "unknown")
        personal = _is_personal(account, args)
        work_dir = _make_work_dir(name)

        if personal:
            entries = _process_account_personal(
                account, work_dir, args.include_archived, inventory, config, config_path
            )
            if not entries:
                print(f"\nAccount: {name} — no Private (type P) vaults found in this export.")
                continue
        else:
            entries = _process_account(
                account, work_dir, args.include_archived, inventory, config, config_path
            )

        manifest_all[name] = entries
        _write_secure(work_dir / "manifest.json", entries)
        _print_table(name, entries)

    print(f"\nOutput written to work/")
    if any(_is_personal(a, args) for a in accounts):
        print("Review the plan above, then run: python3 import.py --personal")
    else:
        print("Review the plan above, then run: python3 import.py")


if __name__ == "__main__":
    main()
