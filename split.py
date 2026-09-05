#!/usr/bin/env python3
"""Split a .1pux export into per-vault Bitwarden import JSON files.

Reads config.json (or --config PATH), optionally filtered by --account.
Writes work/<account>/<vault-slug>.json (bulk items),
       work/<account>/<vault-slug>.attachments.json (items with file attachments),
       work/<account>/manifest.json (per-vault counts + collection metadata).

All work/ subdirs created with mode 700.  Attachment files extracted from the
zip land in work/<account>/files/ and are referenced by path in attachments.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import shutil
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import onepux

SSH_KEY_SUPPORTED = True


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


def _process_account(account: dict, work_dir: Path, include_archived: bool) -> list[dict]:
    pux_path = Path(account["puxPath"])
    if not pux_path.exists():
        sys.exit(f"Export file not found: {pux_path}")

    org_id = account["bitwardenOrgId"]
    skip_vault_types = set(account.get("skipVaultTypes", ["P"]))
    vault_rename = account.get("vaultRename", {})

    files_dir = work_dir / "files"
    export_data, files_map = onepux.parse_export(pux_path, files_dir)

    vault_list = onepux.vaults(export_data)
    manifest_entries: list[dict] = []

    for vault in vault_list:
        attrs = vault.get("attrs") or {}
        vault_type = attrs.get("type", "")
        vault_name = attrs.get("name", attrs.get("uuid", "vault"))
        vault_name = vault_rename.get(vault_name, vault_name)

        if vault_type in skip_vault_types:
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
        manifest_entries.append({
            "vaultName": vault_name,
            "vaultType": vault_type,
            "slug": slug,
            "collectionId": collection_id,
            "collectionName": vault_name,
            "counts": {
                "total": total,
                "bulk": len(result.bulk_items),
                "attachment": len(result.attachment_items),
                "archivedSkipped": result.archived_count,
                "dupesCollapsed": result.dupe_count,
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
    print(f"\nAccount: {account_name}")
    header = f"  {'Vault':<30} {'Type':<6} {'Total':>7} {'Bulk':>7} {'Attach':>7} {'Archived':>9} {'Dupes':>6}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for e in entries:
        c = e["counts"]
        print(
            f"  {e['vaultName']:<30} {e['vaultType']:<6}"
            f" {c['total']:>7} {c['bulk']:>7} {c['attachment']:>7}"
            f" {c['archivedSkipped']:>9} {c['dupesCollapsed']:>6}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Split a .1pux export into per-vault Bitwarden import files."
    )
    parser.add_argument("--config", default="config.json", metavar="PATH")
    parser.add_argument("--account", metavar="NAME", help="Process only this account name")
    parser.add_argument("--include-archived", action="store_true", help="Include archived items")
    args = parser.parse_args()

    config = _load_config(Path(args.config))
    accounts = config.get("accounts", [])

    if not accounts:
        sys.exit("No accounts found in config.")

    if args.account:
        accounts = [a for a in accounts if a.get("name") == args.account]
        if not accounts:
            sys.exit(f"Account '{args.account}' not found in config.")

    print("split.py — 1pux → Bitwarden per-vault import files")

    manifest_all: dict[str, list] = {}
    for account in accounts:
        name = account.get("name", "unknown")
        work_dir = _make_work_dir(name)
        entries = _process_account(account, work_dir, args.include_archived)
        manifest_all[name] = entries
        _write_secure(work_dir / "manifest.json", entries)
        _print_table(name, entries)

    print(f"\nOutput written to work/")
    print("Review the plan above, then run: python3 import.py")


if __name__ == "__main__":
    main()
