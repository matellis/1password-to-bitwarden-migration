#!/usr/bin/env python3
"""Field-level verification of 1pux source vs live Bitwarden org collections.

Per vault: count check, title multiset check, per-item field equality
(username, password, TOTP seed normalized, primary URI, notes, custom fields),
attachment counts, extras detection.  Policy-aware (refuse/allow vs skip).

--personal mode compares against items in the personal folder instead of an org
collection.

Exits 0 only when all checked vaults pass.
Optional --op: compare live 1Password per-vault item counts against 1pux counts.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from lib import bwcli, onepux


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
    p = Path("state") / f"{_ledger_name(account_name, personal)}.json"
    if p.exists():
        return _load_json(p)
    return {"imported": {}, "failures": {}}


def _normalize_totp(seed: str | None) -> str:
    if not seed:
        return ""
    seed = seed.strip()
    if seed.lower().startswith("otpauth://"):
        m = re.search(r"[?&]secret=([^&]+)", seed, re.IGNORECASE)
        if m:
            seed = m.group(1)
    seed = re.sub(r"[\s\-]", "", seed).upper()
    missing = (8 - len(seed) % 8) % 8
    padded = seed + "=" * missing
    try:
        base64.b32decode(padded)
        return seed
    except Exception:
        return seed


def _primary_uri(item: dict) -> str:
    login = item.get("login") or {}
    uris = login.get("uris") or []
    return uris[0].get("uri", "").lower() if uris else ""


def _custom_fields_multiset(fields: list[dict]) -> Counter:
    return Counter(
        (f.get("name", ""), str(f.get("value", "")), f.get("type", 0))
        for f in fields
    )


def _compare_item(source: dict, live: dict, expected_attach_count: int = 0) -> list[str]:
    diffs: list[str] = []

    src_login = source.get("login") or {}
    live_login = live.get("login") or {}

    for attr, label in [("username", "username"), ("password", "password")]:
        sv = (src_login.get(attr) or "").strip()
        lv = (live_login.get(attr) or "").strip()
        if sv != lv:
            diffs.append(f"  {label}: expected {sv!r}, got {lv!r}")

    if _normalize_totp(src_login.get("totp")) != _normalize_totp(live_login.get("totp")):
        diffs.append(f"  totp seed mismatch")

    src_uri = _primary_uri(source)
    live_uri = _primary_uri(live)
    if src_uri != live_uri:
        diffs.append(f"  primary URI: expected {src_uri!r}, got {live_uri!r}")

    src_notes = (source.get("notes") or "").strip()
    live_notes = (live.get("notes") or "").strip()
    if src_notes != live_notes:
        diffs.append(f"  notes mismatch (lengths {len(src_notes)} vs {len(live_notes)})")

    src_fields = _custom_fields_multiset(source.get("fields") or [])
    live_fields = _custom_fields_multiset(live.get("fields") or [])
    if src_fields != live_fields:
        missing = src_fields - live_fields
        extra = live_fields - src_fields
        if missing:
            diffs.append(f"  missing custom fields: {list(missing)}")
        if extra:
            diffs.append(f"  unexpected custom fields: {list(extra)}")

    live_attach = len(live.get("attachments") or [])
    if expected_attach_count != live_attach:
        diffs.append(f"  attachment count: expected {expected_attach_count}, got {live_attach}")

    return diffs


def _render_item_label(item: dict) -> str:
    name = (item.get("name") or "").strip()
    username = ((item.get("login") or {}).get("username") or "").strip()
    return f"{name} ({username})" if username else name


def _sort_key(item: dict) -> str:
    return json.dumps(item, sort_keys=True, default=str)


def _verify_items(
    source_items: list[tuple[dict, int]], live_items: list[dict], policy: str
) -> list[str]:
    """Pair source and live items by fingerprint (not title) and report diffs.

    Items that share a title but differ in username/URI/type get distinct
    fingerprints and are never compared against each other. When a
    fingerprint occurs N times on both sides, pair deterministically by
    sorting each side's items and zipping them.
    """
    issues: list[str] = []

    src_by_fp: dict[tuple, list[tuple[dict, int]]] = {}
    for src_item, attach_count in source_items:
        src_by_fp.setdefault(bwcli.item_fingerprint(src_item), []).append((src_item, attach_count))

    live_by_fp: dict[tuple, list[dict]] = {}
    for it in live_items:
        live_by_fp.setdefault(bwcli.item_fingerprint(it), []).append(it)

    for fp in src_by_fp.keys() | live_by_fp.keys():
        src_list = sorted(src_by_fp.get(fp, []), key=lambda t: _sort_key(t[0]))
        live_list = sorted(live_by_fp.get(fp, []), key=_sort_key)
        n = min(len(src_list), len(live_list))

        for (src_item, attach_count), live_item in zip(src_list[:n], live_list[:n]):
            item_diffs = _compare_item(src_item, live_item, attach_count)
            if item_diffs:
                issues.append(f"Item {_render_item_label(src_item)!r} field mismatches:")
                issues.extend(item_diffs)

        if policy != "skip":
            for src_item, _ in src_list[n:]:
                issues.append(f"Missing: {_render_item_label(src_item)}")

        for live_item in live_list[n:]:
            if policy == "skip":
                issues.append(f"Pre-existing item (expected under skip policy): {_render_item_label(live_item)}")
            else:
                issues.append(f"Unexpected live item: {_render_item_label(live_item)}")

    return issues


def _load_source_items(bulk_path: Path, attach_path: Path) -> list[tuple[dict, int]]:
    """Return (item_dict, expected_attach_count) pairs from both source files."""
    items: list[tuple[dict, int]] = []
    if bulk_path.exists():
        bulk_doc = _load_json(bulk_path)
        for item in bulk_doc.get("items", []):
            items.append((item, 0))
    if attach_path.exists():
        attach_doc = _load_json(attach_path)
        for entry in attach_doc.get("items", []):
            items.append((entry["item"], len(entry.get("files", []))))
    return items


def _verify_vault(
    vault_name: str,
    collection_name: str,
    org_id: str,
    policy: str,
    ledger_entry: dict | None,
    bulk_path: Path,
    attach_path: Path,
) -> tuple[bool, list[str]]:
    issues: list[str] = []

    source_items = _load_source_items(bulk_path, attach_path)

    existing_collections = bwcli.list_org_collections(org_id)
    match = next((c for c in existing_collections if c.get("name") == collection_name), None)
    if not match:
        if not source_items:
            return True, [f"Collection '{collection_name}' not found, but source vault has 0 items. OK (empty vault)."]
        return False, [f"Collection '{collection_name}' not found in org."]

    coll_id = match["id"]
    live_items = bwcli.list_items_in_collection(coll_id, org_id)

    if policy == "skip" and ledger_entry:
        expected_count = ledger_entry.get("importedCount", len(source_items))
    else:
        expected_count = len(source_items)

    live_count = len(live_items)
    if policy == "skip":
        if live_count < expected_count:
            issues.append(f"Count: expected at least {expected_count} live items, got {live_count}")
    else:
        if live_count != expected_count:
            issues.append(f"Count: expected {expected_count}, got {live_count}")

    issues.extend(_verify_items(source_items, live_items, policy))

    return len(issues) == 0, issues


def _verify_vault_personal(
    vault_name: str,
    folder_name: str,
    policy: str,
    ledger_entry: dict | None,
    bulk_path: Path,
    attach_path: Path,
) -> tuple[bool, list[str]]:
    issues: list[str] = []

    folders = bwcli.list_folders()
    match = next((f for f in folders if f.get("name") == folder_name), None)
    if not match:
        return False, [f"Folder '{folder_name}' not found in My vault."]

    folder_id = match["id"]
    live_items = bwcli.list_items_in_folder(folder_id)

    source_items = _load_source_items(bulk_path, attach_path)

    if policy == "skip" and ledger_entry:
        expected_count = ledger_entry.get("importedCount", len(source_items))
    else:
        expected_count = len(source_items)

    live_count = len(live_items)
    if policy == "skip":
        if live_count < expected_count:
            issues.append(f"Count: expected at least {expected_count} live items, got {live_count}")
    else:
        if live_count != expected_count:
            issues.append(f"Count: expected {expected_count}, got {live_count}")

    issues.extend(_verify_items(source_items, live_items, policy))

    return len(issues) == 0, issues


def _op_check(pux_path: Path, account_name: str, op_account: str | None = None) -> None:
    import subprocess, shutil, zipfile as _zipfile
    op = shutil.which("op")
    if not op:
        print("  --op: 'op' CLI not found on PATH, skipping 1Password cross-check.")
        return

    with _zipfile.ZipFile(pux_path) as zf:
        export_data = json.loads(zf.read("export.data"))
    vault_list = onepux.vaults(export_data)

    print(f"\n  1Password cross-check (op CLI) for account: {account_name}")
    for vault in vault_list:
        attrs = vault.get("attrs") or {}
        vault_name = attrs.get("name", "?")
        # op item list excludes archived-state items; match that on the 1pux side.
        active_items = [it for it in (vault.get("items") or []) if it.get("state", "active") != "archived"]
        expected = len(active_items)

        argv = [op, "item", "list", "--vault", vault_name, "--format", "json"]
        if op_account:
            argv += ["--account", op_account]
        try:
            result = subprocess.run(
                argv,
                capture_output=True, text=True
            )
            if result.returncode != 0:
                print(f"    {vault_name}: op error — {result.stderr.strip()}")
                continue
            live_items = json.loads(result.stdout)
            live_count = len(live_items)
            match = "OK" if live_count == expected else "MISMATCH"
            print(f"    {vault_name}: 1pux={expected}, op live={live_count} [{match}]")
            if live_count != expected:
                expected_titles = Counter(
                    (it.get("overview") or {}).get("title") or "Untitled" for it in active_items
                )
                live_titles = Counter((it.get("title") or "").strip() for it in live_items)
                missing = expected_titles - live_titles
                extra = live_titles - expected_titles
                if missing:
                    print(f"      missing from op: {dict(missing)}")
                if extra:
                    print(f"      unexpected in op: {dict(extra)}")
        except Exception as e:
            print(f"    {vault_name}: op check failed — {e}")


def _is_personal(account: dict, args: argparse.Namespace) -> bool:
    return args.personal or account.get("mode") == "personal"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify 1pux source vs live Bitwarden org collections."
    )
    parser.add_argument("--config", default="config.json", metavar="PATH")
    parser.add_argument("--account", metavar="NAME")
    parser.add_argument("--vault", metavar="NAME")
    parser.add_argument("--op", action="store_true", help="Cross-check live 1Password via op CLI")
    parser.add_argument(
        "--personal", action="store_true",
        help="Personal mode: verify against My vault folders instead of org collections"
    )
    args = parser.parse_args()

    config = _load_config(Path(args.config))
    accounts = config.get("accounts", [])

    if args.account:
        accounts = [a for a in accounts if a.get("name") == args.account]
        if not accounts:
            sys.exit(f"Account '{args.account}' not found in config.")

    print("verify.py — field-level 1pux vs Bitwarden verification")

    all_pass = True
    results: list[tuple[str, str, bool]] = []

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
        bwcli.sync()
        policy = account.get("onExisting", "refuse")
        work_dir = Path("work") / name
        manifest_path = work_dir / "manifest.json"

        if not manifest_path.exists():
            print(f"\n[{name}] No manifest found at {manifest_path}. Run split.py first.")
            continue

        manifest = _load_json(manifest_path)
        ledger = _load_ledger(name, personal)
        print(f"\nAccount: {name}{' (personal mode)' if personal else ''}")

        vaults_to_check = manifest
        if args.vault:
            vaults_to_check = [v for v in manifest if v["vaultName"] == args.vault]

        for vault_entry in vaults_to_check:
            vault_name = vault_entry["vaultName"]
            is_personal_entry = vault_entry.get("personal", False)

            if personal != is_personal_entry:
                continue

            slug = vault_entry["slug"]
            bulk_path = work_dir / f"{slug}.json"
            attach_path = work_dir / f"{slug}.attachments.json"
            ledger_entry = ledger.get("imported", {}).get(vault_name)

            if vault_name not in ledger.get("imported", {}):
                print(f"  [{vault_name}] Not in ledger — skipping (not yet imported).")
                continue

            print(f"  [{vault_name}] Checking...", end=" ", flush=True)

            if personal:
                folder_name = vault_entry.get("folderName", vault_name)
                passed, issues = _verify_vault_personal(
                    vault_name, folder_name, policy, ledger_entry, bulk_path, attach_path
                )
            else:
                org_id = account["bitwardenOrgId"]
                passed, issues = _verify_vault(
                    vault_name, vault_entry["collectionName"], org_id,
                    policy, ledger_entry, bulk_path, attach_path
                )

            status = "PASS" if passed else "FAIL"
            print(status)
            if not passed:
                all_pass = False
                for issue in issues:
                    print(f"    {issue}")
            results.append((name, vault_name, passed))

        if args.op and not personal:
            pux_path = Path(account.get("puxPath", ""))
            if pux_path.exists():
                _op_check(pux_path, name, account.get("opAccount"))

    print("\nSummary:")
    for acct, vault, passed in results:
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {acct} / {vault}")

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
