#!/usr/bin/env python3
"""Import per-vault Bitwarden JSON files produced by split.py.

Reads config.json (or --config PATH), uses the state ledger in state/<account>.json
to track which vaults have already been imported.  Respects the onExisting policy
(refuse / skip / allow) per account.

Calls bw import for bulk items, bw sync after each vault import to resolve the
server-assigned collection ID, and bw create item + bw create attachment for
items with file attachments.  All Bitwarden writes are guarded by the ledger and
onExisting checks.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from lib import bwcli


def _load_json(path: Path) -> Any:
    with open(path) as f:
        return json.load(f)


def _load_config(config_path: Path) -> dict:
    if not config_path.exists():
        sys.exit(f"Config not found: {config_path}")
    return _load_json(config_path)


def _load_ledger(account_name: str) -> dict:
    ledger_path = Path("state") / f"{account_name}.json"
    if ledger_path.exists():
        return _load_json(ledger_path)
    return {"imported": {}, "failures": {}}


def _save_ledger(account_name: str, ledger: dict) -> None:
    state_dir = Path("state")
    old_umask = os.umask(0o077)
    try:
        state_dir.mkdir(mode=0o700, exist_ok=True)
        ledger_path = state_dir / f"{account_name}.json"
        with open(ledger_path, "w") as f:
            json.dump(ledger, f, indent=2)
        os.chmod(ledger_path, 0o600)
    finally:
        os.umask(old_umask)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _resolve_real_collection_id(vault_name: str, org_id: str) -> str | None:
    """Sync and return the server-assigned collection ID for vault_name, or None."""
    bwcli.sync()
    collections = bwcli.list_org_collections(org_id)
    match = next((c for c in collections if c.get("name") == vault_name), None)
    return match["id"] if match else None


def _apply_on_existing_policy(
    policy: str,
    collection_name: str,
    org_id: str,
    existing_collections: list[dict],
    yes: bool,
) -> tuple[bool, str | None]:
    match = next(
        (c for c in existing_collections if c.get("name") == collection_name), None
    )
    if not match:
        return True, None

    coll_id = match["id"]
    existing_items = bwcli.list_items_in_collection(coll_id, org_id)
    count = len(existing_items)

    if count == 0:
        return True, None

    if policy == "refuse":
        return False, (
            f"Collection '{collection_name}' already exists with {count} item(s). "
            f"Set onExisting=skip or allow to proceed."
        )

    if policy == "allow":
        if not yes:
            answer = input(
                f"Collection '{collection_name}' already has {count} item(s). "
                f"Import anyway? (y/N): "
            ).strip().lower()
            if answer != "y":
                return False, "Aborted by user."
        return True, None

    return True, None


def _fingerprints_in_collection(collection_name: str, org_id: str, existing_collections: list[dict]) -> set[tuple]:
    match = next(
        (c for c in existing_collections if c.get("name") == collection_name), None
    )
    if not match:
        return set()
    return bwcli.fingerprints_in_collection(match["id"], org_id)


def _import_vault(
    account: dict,
    vault_entry: dict,
    work_dir: Path,
    ledger: dict,
    yes: bool,
    force: bool,
) -> dict:
    org_id = account["bitwardenOrgId"]
    policy = account.get("onExisting", "refuse")
    vault_name = vault_entry["vaultName"]
    slug = vault_entry["slug"]

    if vault_name in ledger["imported"] and not force:
        print(f"  [{vault_name}] Already imported (ledger). Use --force to re-run.")
        return {"status": "skipped_ledger"}

    print(f"  [{vault_name}] Checking existing collections...")
    existing_collections = bwcli.list_org_collections(org_id)

    allowed, reason = _apply_on_existing_policy(
        policy, vault_name, org_id, existing_collections, yes
    )
    if not allowed:
        print(f"  [{vault_name}] REFUSED: {reason}")
        return {"status": "refused", "reason": reason}

    existing_fps: set[tuple] = set()
    if policy == "skip":
        print(f"  [{vault_name}] Building fingerprint set for skip policy...")
        existing_fps = _fingerprints_in_collection(vault_name, org_id, existing_collections)

    bulk_path = work_dir / f"{slug}.json"
    if not bulk_path.exists():
        print(f"  [{vault_name}] No bulk file found at {bulk_path}, skipping.")
        return {"status": "no_bulk_file"}

    imported_count = 0
    skipped_count = 0

    if policy == "skip" and existing_fps:
        bulk_doc = _load_json(bulk_path)
        keep_items = []
        for item in bulk_doc.get("items", []):
            login = item.get("login") or {}
            uris = login.get("uris") or []
            primary_uri = uris[0].get("uri", "") if uris else ""
            fp = (
                item.get("type", 0),
                (item.get("name") or "").strip().lower(),
                (login.get("username") or "").lower(),
                primary_uri.lower(),
            )
            if fp in existing_fps:
                skipped_count += 1
            else:
                keep_items.append(item)

        if keep_items:
            tmp = None
            old_umask = os.umask(0o177)
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".json", dir=str(work_dir), delete=False
                ) as tf:
                    tmp = Path(tf.name)
                    import_doc = dict(bulk_doc)
                    import_doc["items"] = keep_items
                    json.dump(import_doc, tf, indent=2)
                os.chmod(tmp, 0o600)
                print(f"  [{vault_name}] Bulk import: {len(keep_items)} items ({skipped_count} skipped)...")
                bwcli.bulk_import(tmp, org_id)
                imported_count += len(keep_items)
            finally:
                os.umask(old_umask)
                if tmp is not None:
                    tmp.unlink(missing_ok=True)
        else:
            print(f"  [{vault_name}] All {skipped_count} bulk items already exist, skipping bulk.")
    else:
        print(f"  [{vault_name}] Bulk import...")
        bwcli.bulk_import(bulk_path, org_id)
        imported_count += vault_entry["counts"]["bulk"]

    # Resolve the server-assigned collection ID. bw import discards the
    # placeholder uuid from the JSON and creates a new server-side id.
    real_coll_id = _resolve_real_collection_id(vault_name, org_id)
    if real_coll_id is None:
        msg = f"Collection '{vault_name}' not found in org after sync."
        print(f"  [{vault_name}] FAILED: {msg}")
        ledger["failures"][vault_name] = [{"error": msg}]
        return {"status": "failed"}

    attach_path = work_dir / f"{slug}.attachments.json"
    attach_results: list[dict] = []
    attachment_failures: list[dict] = []

    if attach_path.exists():
        attach_doc = _load_json(attach_path)
        attach_org_id = attach_doc.get("organizationId", org_id)
        attach_items = attach_doc.get("items", [])

        for entry in attach_items:
            item_def = entry["item"]
            file_paths = entry.get("files", [])

            item_def["organizationId"] = attach_org_id
            item_def["collectionIds"] = [real_coll_id]

            if policy == "skip":
                login = item_def.get("login") or {}
                uris = login.get("uris") or []
                primary_uri = uris[0].get("uri", "") if uris else ""
                fp = (
                    item_def.get("type", 0),
                    (item_def.get("name") or "").strip().lower(),
                    (login.get("username") or "").lower(),
                    primary_uri.lower(),
                )
                if fp in existing_fps:
                    skipped_count += 1
                    continue

            try:
                print(f"    Creating item: {item_def.get('name', '?')}...")
                created = bwcli.create_item(item_def, attach_org_id)
                item_id = created["id"]
                imported_count += 1
            except bwcli.BWError as e:
                msg = str(e)
                print(f"    FAILED to create item {item_def.get('name', '?')}: {msg}")
                attachment_failures.append({"item": item_def.get("name"), "error": msg})
                continue

            for fpath_str in file_paths:
                fpath = Path(fpath_str)
                if not fpath.exists():
                    msg = f"File not found: {fpath}"
                    print(f"    FAILED attachment upload: {msg}")
                    attachment_failures.append({"item": item_id, "file": fpath_str, "error": msg})
                    continue
                try:
                    print(f"    Uploading attachment: {fpath.name}...")
                    bwcli.create_attachment(fpath, item_id)
                except bwcli.BWError as e:
                    msg = str(e)
                    print(f"    FAILED attachment upload {fpath.name}: {msg}")
                    attachment_failures.append({"item": item_id, "file": fpath_str, "error": msg})

            attach_results.append({"name": item_def.get("name"), "id": item_id})

    ledger["imported"][vault_name] = {
        "timestamp": _now_iso(),
        "importedCount": imported_count,
        "skippedCount": skipped_count,
        "attachmentItems": len(attach_results),
        "failures": len(attachment_failures),
        "collectionId": real_coll_id,
    }
    if attachment_failures:
        ledger["failures"][vault_name] = attachment_failures

    status = "ok" if not attachment_failures else "partial"
    print(f"  [{vault_name}] Done — {imported_count} imported, {skipped_count} skipped, {len(attachment_failures)} failure(s). Status: {status}")
    return {"status": status}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import per-vault Bitwarden files produced by split.py."
    )
    parser.add_argument("--config", default="config.json", metavar="PATH")
    parser.add_argument("--account", metavar="NAME")
    parser.add_argument("--vault", metavar="NAME", help="Process only this vault name")
    parser.add_argument("--force", action="store_true", help="Re-import even if ledger says done")
    parser.add_argument("--yes", action="store_true", help="Skip allow-policy confirmation prompt")
    args = parser.parse_args()

    config = _load_config(Path(args.config))
    accounts = config.get("accounts", [])

    if args.account:
        accounts = [a for a in accounts if a.get("name") == args.account]
        if not accounts:
            sys.exit(f"Account '{args.account}' not found in config.")

    print("import.py — Bitwarden per-vault import")
    print("Checking bw prerequisites...")
    bwcli.check_prereqs()
    print("Syncing vault...")
    bwcli.sync()

    for account in accounts:
        name = account.get("name", "unknown")
        work_dir = Path("work") / name
        manifest_path = work_dir / "manifest.json"

        if not manifest_path.exists():
            print(f"\n[{name}] No manifest found at {manifest_path}. Run split.py first.")
            continue

        manifest = _load_json(manifest_path)
        ledger = _load_ledger(name)
        print(f"\nAccount: {name}")

        vaults_to_process = manifest
        if args.vault:
            vaults_to_process = [v for v in manifest if v["vaultName"] == args.vault]
            if not vaults_to_process:
                print(f"  Vault '{args.vault}' not in manifest.")
                continue

        for vault_entry in vaults_to_process:
            _import_vault(account, vault_entry, work_dir, ledger, args.yes, args.force)
            _save_ledger(name, ledger)

    print("\nDone. Run verify.py to confirm the migration.")


if __name__ == "__main__":
    main()
