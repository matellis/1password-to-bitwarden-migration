#!/usr/bin/env python3
"""adopt.py — merge passkeys from personal vault into org items after a late CXP transfer.

*** EXPERIMENTAL ***

This script is untested against a live Bitwarden server. The Bitwarden server
may or may not accept fido2Credentials on a bw edit call — this has not been
verified. Read the warnings below before using --apply.

Safe use procedure:
  1. Run without --apply (dry-run). Review the plan carefully.
  2. If the plan looks right, run --apply with --collection on a SINGLE item
     by first moving others out of scope manually.
  3. Sign in to the site with the passkey to verify it works.
  4. Only then run --apply for remaining items.

Manual fallback (if adopt.py does not work):
  In the Bitwarden web vault, move the CXP-transferred item into the target org
  collection, then delete the script-imported login that was marked with the
  passkey notice.

bw edit syntax (from bw 2026.8.0 --help):
  bw edit item <id> [encodedJson] [options]
  encodedJson is the full item JSON, base64-encoded, same as bw create item.
  Exit 0 on success; stdout is the updated item JSON when --raw is passed.

  NOTE: Whether the server preserves fido2Credentials across an edit is
  unverified. On first real use, verify with bw get item <id> after editing
  that fido2Credentials is non-empty before proceeding.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import bwcli, passkey_inventory as pkinv


def _find_collection_id(org_id: str, collection_name: str) -> str:
    collections = bwcli.list_org_collections(org_id)
    for c in collections:
        if (c.get("name") or "").strip().lower() == collection_name.strip().lower():
            return c["id"]
    names = [c.get("name", "?") for c in collections]
    sys.exit(
        f"Collection '{collection_name}' not found in org '{org_id}'.\n"
        f"Available collections: {', '.join(names) or '(none)'}"
    )


def _personal_passkey_items() -> list[dict]:
    """Return personal-vault login items that carry at least one passkey."""
    bwcli.sync()
    items = bwcli.list_personal_items()
    return [
        it for it in items
        if it.get("type") == 1
        and (it.get("login") or {}).get("fido2Credentials")
    ]


def _org_items_in_collection(org_id: str, collection_id: str) -> list[dict]:
    bwcli.sync()
    return bwcli.list_items_in_collection(collection_id, org_id)


def _build_plan(personal_items: list[dict], org_items: list[dict]) -> list[dict]:
    """Return list of match dicts: {personal_item, org_item}.

    Matches on fingerprint (type=1, title, username, primary URI).
    """
    plan = []
    used_org_ids: set[str] = set()

    for p_item in personal_items:
        p_login = p_item.get("login") or {}
        if not p_login.get("fido2Credentials"):
            continue
        p_fp = pkinv.item_fingerprint(p_item)
        p_login = p_item.get("login") or {}
        p_uris = p_login.get("uris") or []
        p_url = p_uris[0].get("uri", "") if p_uris else ""

        for o_item in org_items:
            if o_item.get("id") in used_org_ids:
                continue
            o_fp = pkinv.item_fingerprint(o_item)
            if o_fp[1] != p_fp[1] or o_fp[2] != p_fp[2]:
                continue
            o_url = o_fp[3]
            inv_entry = {"site": p_fp[1], "username": p_fp[2], "url": p_url}
            if not pkinv._urls_match(p_url.lower(), o_url):
                continue
            used_org_ids.add(o_item["id"])
            plan.append({"personal_item": p_item, "org_item": o_item})
            break

    return plan


def _apply_match(match: dict) -> bool:
    """Copy fido2Credentials from personal item into org item, verify, then trash personal.

    Returns True on success, False on any failure (never raises).
    """
    personal = match["personal_item"]
    org = match["org_item"]
    org_id = org.get("id")
    personal_id = personal.get("id")
    name = org.get("name", "?")

    fido2 = (personal.get("login") or {}).get("fido2Credentials") or []

    updated_org = json.loads(json.dumps(org))
    if not updated_org.get("login"):
        updated_org["login"] = {}
    updated_org["login"]["fido2Credentials"] = fido2

    print(f"  Editing org item '{name}' ({org_id}) ...")
    try:
        bwcli.edit_item(org_id, updated_org)
    except bwcli.BWError as exc:
        print(f"    FAILED to edit: {exc}")
        return False

    print(f"  Verifying passkey landed in org item ...")
    try:
        refreshed = bwcli.get_item(org_id)
    except bwcli.BWError as exc:
        print(f"    FAILED to re-read org item after edit: {exc}")
        print(f"    Personal item NOT deleted. Verify manually.")
        return False

    if not (refreshed.get("login") or {}).get("fido2Credentials"):
        print(f"    Verification FAILED: fido2Credentials empty after edit.")
        print(f"    Personal item NOT deleted. Investigate before retrying.")
        return False

    print(f"  Passkey confirmed. Trashing personal item ({personal_id}) ...")
    try:
        bwcli.delete_item(personal_id)
    except bwcli.BWError as exc:
        print(f"    FAILED to trash personal item: {exc}")
        print(f"    Passkey is in the org item — trash the personal item manually.")
        return False

    print(f"  Done.")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "*** EXPERIMENTAL *** Merge passkeys from personal-vault CXP items "
            "into matching org items after a late iOS transfer. "
            "Default is dry-run; use --apply to write."
        )
    )
    parser.add_argument("--config", default="config.json", metavar="PATH")
    parser.add_argument("--account", metavar="NAME", required=True,
                        help="Account name from config (required)")
    parser.add_argument("--organizationid", metavar="ID",
                        help="Bitwarden org UUID (overrides config)")
    parser.add_argument("--collection", metavar="NAME", required=True,
                        help="Target collection name to match org items against")
    parser.add_argument("--apply", action="store_true",
                        help="Write changes. Without this flag, only the plan is printed.")
    args = parser.parse_args()

    print("adopt.py *** EXPERIMENTAL — read the warnings in the module docstring ***")

    config_path = Path(args.config)
    if not config_path.exists():
        sys.exit(f"Config not found: {config_path}")
    with open(config_path) as f:
        config = json.load(f)

    accounts = {a["name"]: a for a in config.get("accounts", [])}
    if args.account not in accounts:
        sys.exit(f"Account '{args.account}' not found in config.")
    account = accounts[args.account]

    org_id = args.organizationid or account.get("bitwardenOrgId") or ""
    if not org_id:
        sys.exit("No org ID. Pass --organizationid or set bitwardenOrgId in config.")

    server = account.get("bitwardenServer", "us")
    email = account.get("bitwardenEmail")
    bwcli.ensure_session(server, email)

    print(f"\nLooking up collection '{args.collection}' in org {org_id} ...")
    collection_id = _find_collection_id(org_id, args.collection)
    print(f"Found: {collection_id}")

    print("\nLoading personal vault passkey items ...")
    personal_items = _personal_passkey_items()
    print(f"  {len(personal_items)} personal login(s) with passkeys")

    print(f"\nLoading org items from collection '{args.collection}' ...")
    org_items = _org_items_in_collection(org_id, collection_id)
    print(f"  {len(org_items)} org item(s)")

    plan = _build_plan(personal_items, org_items)
    unmatched_personal = [p for p in personal_items if not any(m["personal_item"] is p for m in plan)]

    print(f"\nPlan: {len(plan)} match(es), {len(unmatched_personal)} personal item(s) unmatched")
    if not plan:
        print("Nothing to do.")
        return

    print("\nMatches:")
    for m in plan:
        p = m["personal_item"]
        o = m["org_item"]
        login = (p.get("login") or {})
        creds = login.get("fido2Credentials") or []
        print(
            f"  personal '{p.get('name', '?')}' ({p.get('id', '?')[:8]}…)"
            f"  →  org '{o.get('name', '?')}' ({o.get('id', '?')[:8]}…)"
            f"  [{len(creds)} passkey(s)]"
        )

    if unmatched_personal:
        print("\nUnmatched personal passkey items (no org item found — skip these):")
        for p in unmatched_personal:
            print(f"  '{p.get('name', '?')}' ({p.get('id', '?')[:8]}…)")

    if not args.apply:
        print(
            "\nDry-run complete. Add --apply to write changes.\n"
            "IMPORTANT: verify with a real passkey sign-in after the first --apply run."
        )
        return

    print("\nApplying ...")
    ok = 0
    fail = 0
    for m in plan:
        result = _apply_match(m)
        if result:
            ok += 1
        else:
            fail += 1

    print(f"\nDone: {ok} succeeded, {fail} failed.")
    if fail:
        print("Failed items were not deleted. Review output above and retry or use the manual fallback.")
        sys.exit(1)


if __name__ == "__main__":
    main()
