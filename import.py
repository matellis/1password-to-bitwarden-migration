#!/usr/bin/env python3
"""Import per-vault Bitwarden JSON files produced by split.py.

Reads config.json (or --config PATH), uses the state ledger in state/<account>.json
to track which vaults have already been imported.  Respects the onExisting policy
(refuse / skip / allow) per account.

Org mode (default):
  Calls bw import for bulk items, bw sync after each vault import to resolve the
  server-assigned collection ID, and bw create item + bw create attachment for
  items with file attachments.  All Bitwarden writes are guarded by the ledger and
  onExisting checks.

Personal mode (--personal or "mode": "personal" in config):
  Calls bw import without --organizationid (lands in My vault).  Resolves the
  server-assigned folder ID by name after sync.  Creates attachment-bearing items
  individually with folderId set.  Ledger file is suffixed "-personal".
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


def _ledger_name(account_name: str, personal: bool) -> str:
    return f"{account_name}-personal" if personal else account_name


def _load_ledger(account_name: str, personal: bool) -> dict:
    ledger_path = Path("state") / f"{_ledger_name(account_name, personal)}.json"
    if ledger_path.exists():
        return _load_json(ledger_path)
    return {"imported": {}, "failures": {}}


def _save_ledger(account_name: str, ledger: dict, personal: bool) -> None:
    state_dir = Path("state")
    old_umask = os.umask(0o077)
    try:
        state_dir.mkdir(mode=0o700, exist_ok=True)
        ledger_path = state_dir / f"{_ledger_name(account_name, personal)}.json"
        with open(ledger_path, "w") as f:
            json.dump(ledger, f, indent=2)
        os.chmod(ledger_path, 0o600)
    finally:
        os.umask(old_umask)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# --- Org mode helpers ---

def _resolve_real_collection_id(vault_name: str, org_id: str) -> str | None:
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

    attach_path = work_dir / f"{slug}.attachments.json"
    attach_doc = _load_json(attach_path) if attach_path.exists() else {}
    if not _load_json(bulk_path).get("items") and not attach_doc.get("items"):
        print(f"  [{vault_name}] No active items to import (all archived or empty) — marked done.")
        ledger["imported"][vault_name] = {
            "timestamp": _now_iso(),
            "importedCount": 0,
            "skippedCount": 0,
            "attachmentItems": 0,
            "failures": 0,
            "collectionId": None,
        }
        return {"status": "ok"}

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

    attach_results: list[dict] = []
    attachment_failures: list[dict] = []

    if attach_path.exists():
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


# --- Personal mode helpers ---

def _resolve_real_folder_id(folder_name: str) -> str | None:
    bwcli.sync()
    folders = bwcli.list_folders()
    match = next((f for f in folders if f.get("name") == folder_name), None)
    return match["id"] if match else None


def _apply_on_existing_policy_personal(
    policy: str,
    folder_name: str,
    existing_folders: list[dict],
    yes: bool,
) -> tuple[bool, str | None]:
    match = next((f for f in existing_folders if f.get("name") == folder_name), None)
    if not match:
        return True, None

    items = bwcli.list_items_in_folder(match["id"])
    count = len(items)

    if count == 0:
        return True, None

    if policy == "refuse":
        return False, (
            f"Folder '{folder_name}' already exists with {count} item(s). "
            f"Set onExisting=skip or allow to proceed."
        )

    if policy == "allow":
        if not yes:
            answer = input(
                f"Folder '{folder_name}' already has {count} item(s). "
                f"Import anyway? (y/N): "
            ).strip().lower()
            if answer != "y":
                return False, "Aborted by user."
        return True, None

    return True, None


def _item_fp(item_def: dict) -> tuple:
    login = item_def.get("login") or {}
    uris = login.get("uris") or []
    primary_uri = uris[0].get("uri", "") if uris else ""
    return (
        item_def.get("type", 0),
        (item_def.get("name") or "").strip().lower(),
        (login.get("username") or "").lower(),
        primary_uri.lower(),
    )


def _import_vault_personal(
    account: dict,
    vault_entry: dict,
    work_dir: Path,
    ledger: dict,
    yes: bool,
    force: bool,
) -> dict:
    policy = account.get("onExisting", "refuse")
    vault_name = vault_entry["vaultName"]
    slug = vault_entry["slug"]

    if vault_name in ledger["imported"] and not force:
        print(f"  [{vault_name}] Already imported (ledger). Use --force to re-run.")
        return {"status": "skipped_ledger"}

    print(f"  [{vault_name}] Checking existing folders...")
    existing_folders = bwcli.list_folders()

    allowed, reason = _apply_on_existing_policy_personal(policy, vault_name, existing_folders, yes)
    if not allowed:
        print(f"  [{vault_name}] REFUSED: {reason}")
        return {"status": "refused", "reason": reason}

    existing_fps: set[tuple] = set()
    if policy == "skip":
        print(f"  [{vault_name}] Building fingerprint set for skip policy...")
        match = next((f for f in existing_folders if f.get("name") == vault_name), None)
        if match:
            existing_fps = bwcli.fingerprints_in_folder(match["id"])

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
            if _item_fp(item) in existing_fps:
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
                    tmp_doc = dict(bulk_doc)
                    tmp_doc["items"] = keep_items
                    json.dump(tmp_doc, tf, indent=2)
                os.chmod(tmp, 0o600)
                print(f"  [{vault_name}] Bulk import: {len(keep_items)} items ({skipped_count} skipped)...")
                bwcli.bulk_import_personal(tmp)
                imported_count += len(keep_items)
            finally:
                os.umask(old_umask)
                if tmp is not None:
                    tmp.unlink(missing_ok=True)
        else:
            print(f"  [{vault_name}] All {skipped_count} bulk items already exist, skipping bulk.")
    else:
        print(f"  [{vault_name}] Bulk import (personal)...")
        bwcli.bulk_import_personal(bulk_path)
        imported_count += vault_entry["counts"]["bulk"]

    # bw import discards the folder id from the JSON; resolve the server-assigned one.
    real_folder_id = _resolve_real_folder_id(vault_name)
    if real_folder_id is None:
        msg = f"Folder '{vault_name}' not found after sync."
        print(f"  [{vault_name}] FAILED: {msg}")
        ledger["failures"][vault_name] = [{"error": msg}]
        return {"status": "failed"}

    attach_path = work_dir / f"{slug}.attachments.json"
    attach_results: list[dict] = []
    attachment_failures: list[dict] = []

    if attach_path.exists():
        attach_doc = _load_json(attach_path)
        attach_items = attach_doc.get("items", [])

        for entry in attach_items:
            item_def = entry["item"]
            file_paths = entry.get("files", [])

            item_def["folderId"] = real_folder_id
            item_def["organizationId"] = None
            item_def["collectionIds"] = None

            if policy == "skip" and _item_fp(item_def) in existing_fps:
                skipped_count += 1
                continue

            try:
                print(f"    Creating item: {item_def.get('name', '?')}...")
                created = bwcli.create_personal_item(item_def)
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
        "folderId": real_folder_id,
    }
    if attachment_failures:
        ledger["failures"][vault_name] = attachment_failures

    status = "ok" if not attachment_failures else "partial"
    print(f"  [{vault_name}] Done — {imported_count} imported, {skipped_count} skipped, {len(attachment_failures)} failure(s). Status: {status}")
    return {"status": status}


def _print_owner_only_notice(vault_entry: dict) -> None:
    print(
        f"  [{vault_entry['vaultName']}] Owner-only collection imported."
        f" Regular members cannot see a new collection unless granted access,"
        f" but org Owners/Admins can see all collections by default (org setting)"
        f" — verify in the Bitwarden web vault (Admin Console > Organizations >"
        f" Collections > Manage access)."
        f" The bw CLI cannot read or set collection member permissions."
    )


def _print_shared_members_notice(vault_entry: dict) -> None:
    members = ", ".join(vault_entry.get("shareWith") or [])
    print(
        f"  [{vault_entry['vaultName']}] POST-IMPORT ACTION REQUIRED:"
        f" grant access to this collection for the following member(s) in the"
        f" Bitwarden web vault (Admin Console > Organizations > Collections >"
        f" Manage access): {members}."
        f" The bw CLI does not support setting collection member permissions."
    )


def _post_import_notices(vault_entry: dict, status: str) -> None:
    if status not in ("ok", "partial"):
        return
    if vault_entry.get("destination") == "owner-only":
        _print_owner_only_notice(vault_entry)
    if vault_entry.get("destination") == "shared" and vault_entry.get("shareWith"):
        _print_shared_members_notice(vault_entry)


def _is_personal(account: dict, args: argparse.Namespace) -> bool:
    return args.personal or account.get("mode") == "personal"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import per-vault Bitwarden files produced by split.py."
    )
    parser.add_argument("--config", default="config.json", metavar="PATH")
    parser.add_argument("--account", metavar="NAME")
    parser.add_argument("--vault", metavar="NAME", help="Process only this vault name")
    parser.add_argument("--force", action="store_true", help="Re-import even if ledger says done")
    parser.add_argument("--yes", action="store_true", help="Skip allow-policy confirmation prompt")
    parser.add_argument(
        "--personal", action="store_true",
        help="Personal mode: import into My vault (no org). Ledger stored as <account>-personal.json"
    )
    args = parser.parse_args()

    config = _load_config(Path(args.config))
    accounts = config.get("accounts", [])

    if args.account:
        accounts = [a for a in accounts if a.get("name") == args.account]
        if not accounts:
            sys.exit(f"Account '{args.account}' not found in config.")

    print("import.py — Bitwarden per-vault import")

    all_failures: list[tuple[str, str, str]] = []
    for account in accounts:
        name = account.get("name", "unknown")
        server = account.get("bitwardenServer", "us")
        personal = _is_personal(account, args)
        email = account.get("bitwardenEmail")
        if personal and not email:
            sys.exit(
                f"Account '{name}': bitwardenEmail is required for mode=personal entries.\n"
                f"Add \"bitwardenEmail\": \"you@example.com\" to this account in config.json."
            )
        bwcli.ensure_session(server, email)
        print("Syncing vault...")
        bwcli.sync()
        work_dir = Path("work") / name
        manifest_path = work_dir / "manifest.json"

        if not manifest_path.exists():
            print(f"\n[{name}] No manifest found at {manifest_path}. Run split.py first.")
            continue

        manifest = _load_json(manifest_path)

        if not personal:
            unclassified = [
                v["vaultName"] for v in manifest
                if not v.get("personal") and "destination" not in v
            ]
            if unclassified:
                names = ", ".join(f'"{n}"' for n in unclassified)
                sys.exit(
                    f"Account '{name}': manifest contains vaults with no destination: {names}\n"
                    f"Re-run split.py — it will require vaultDestination entries for these vaults."
                )

        ledger = _load_ledger(name, personal)
        print(f"\nAccount: {name}{' (personal mode)' if personal else ''}")

        vaults_to_process = manifest
        if args.vault:
            vaults_to_process = [v for v in manifest if v["vaultName"] == args.vault]
            if not vaults_to_process:
                print(f"  Vault '{args.vault}' not in manifest.")
                continue

        failures: list[tuple[str, str]] = []
        for vault_entry in vaults_to_process:
            if personal:
                if not vault_entry.get("personal"):
                    continue
                try:
                    _import_vault_personal(account, vault_entry, work_dir, ledger, args.yes, args.force)
                except Exception as e:
                    vname = vault_entry["vaultName"]
                    print(f"  [{vname}] FAILED: {e}")
                    ledger["failures"][vname] = [{"error": str(e)}]
                    failures.append((vname, str(e)))
            else:
                if vault_entry.get("personal"):
                    continue
                try:
                    result = _import_vault(account, vault_entry, work_dir, ledger, args.yes, args.force)
                except Exception as e:
                    vname = vault_entry["vaultName"]
                    print(f"  [{vname}] FAILED: {e}")
                    ledger["failures"][vname] = [{"error": str(e)}]
                    failures.append((vname, str(e)))
                else:
                    _post_import_notices(vault_entry, result.get("status"))
            _save_ledger(name, ledger, personal)
        all_failures.extend((name, v, e) for v, e in failures)

    if all_failures:
        print("\nFAILURES — these vaults did not import:")
        for account_name, vault_name, error in all_failures:
            print(f"  [{account_name} / {vault_name}] {error}")
        print("\nFix the cause and re-run; completed vaults are skipped from the ledger.")
        sys.exit(1)

    print("\nDone. Run verify.py to confirm the migration.")


if __name__ == "__main__":
    main()
