"""Shared passkey inventory loading and fingerprint matching.

Used by split.py (--mark-passkeys) and passkeys.py (--gap-report).
Accepts two inventory formats:
  - Markdown: lines like "- Site | username | url"
  - Bridge JSON: {"entries": [{"title": ..., "username": ..., "url": ...}]}
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

MARKER = (
    "[1Password migration] This login had a passkey that was NOT migrated. "
    "Re-add it via iOS Credential Exchange (see README), or replace this item "
    "with the CXP-transferred one."
)


def load_inventory(file_path: Path) -> list[dict]:
    """Load a passkey inventory file, auto-detecting markdown vs. Bridge JSON."""
    text = file_path.read_text(encoding="utf-8")
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            data = json.loads(stripped)
            return _load_bridge_json(data)
        except json.JSONDecodeError:
            pass
    return _load_markdown(text)


def _load_bridge_json(data: object) -> list[dict]:
    if isinstance(data, dict) and "entries" in data:
        entries = data["entries"]
    elif isinstance(data, list):
        entries = data
    else:
        return []

    result = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        title = (e.get("title") or e.get("site") or "").strip()
        if not title:
            continue
        result.append({
            "site": title,
            "username": (e.get("username") or "").strip(),
            "url": (e.get("url") or "").strip(),
        })
    return result


def _load_markdown(text: str) -> list[dict]:
    result = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or not line.startswith("-"):
            continue
        body = line.lstrip("-").strip()
        parts = [p.strip() for p in body.split("|")]
        site = parts[0] if parts else ""
        if not site:
            continue
        username = parts[1] if len(parts) > 1 else ""
        url = parts[2] if len(parts) > 2 else ""
        result.append({"site": site, "username": username, "url": url})
    return result


def _host(url: str) -> str:
    """Extract lowercased hostname from a URL, or return the url itself."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        return (parsed.hostname or url).lower()
    except Exception:
        return url.lower()


def _urls_match(inv_url: str, item_url: str) -> bool:
    """True if URLs match exactly or share the same hostname.

    If the inventory entry has no URL, match on title+username alone.
    """
    if not inv_url:
        return True
    if inv_url == item_url:
        return True
    return _host(inv_url) == _host(item_url)


def item_fingerprint(bw_item: dict) -> tuple:
    """(type, normalized_title, lowercased_username, lowercased_primary_uri)"""
    login = bw_item.get("login") or {}
    uris = login.get("uris") or []
    primary_uri = uris[0].get("uri", "") if uris else ""
    return (
        bw_item.get("type", 0),
        (bw_item.get("name") or "").strip().lower(),
        (login.get("username") or "").lower(),
        primary_uri.lower(),
    )


def matches_inventory(bw_item: dict, inventory: list[dict]) -> bool:
    """True if a Bitwarden item fingerprint matches any inventory entry."""
    if bw_item.get("type") != 1:
        return False
    fp = item_fingerprint(bw_item)
    item_title = fp[1]
    item_user = fp[2]
    item_url = fp[3]

    for entry in inventory:
        inv_title = (entry.get("site") or entry.get("title") or "").strip().lower()
        inv_user = (entry.get("username") or "").lower()
        inv_url = (entry.get("url") or "").lower()

        if inv_title != item_title:
            continue
        if inv_user != item_user:
            continue
        if _urls_match(inv_url, item_url):
            return True
    return False


def append_marker(notes: str | None) -> str:
    """Append the passkey migration marker line to item notes."""
    if not notes:
        return MARKER
    return notes + "\n" + MARKER


def gap_report(inventory: list[dict], bw_items: list[dict]) -> dict:
    """Compare inventory against Bitwarden passkey items.

    Returns a dict with:
      matched        — list of (inv_entry, bw_item) pairs
      missing_from_bw — inventory entries with no matching BW passkey item
      unexpected_in_bw — BW passkey items with no matching inventory entry
    """
    bw_passkey_items = [
        item for item in bw_items
        if item.get("type") == 1
        and (item.get("login") or {}).get("fido2Credentials")
    ]

    matched_inv: set[int] = set()
    matched_bw: set[int] = set()
    matches: list[tuple] = []

    for i, inv_entry in enumerate(inventory):
        inv_title = (inv_entry.get("site") or inv_entry.get("title") or "").strip().lower()
        inv_user = (inv_entry.get("username") or "").lower()
        inv_url = (inv_entry.get("url") or "").lower()

        for j, bw_item in enumerate(bw_passkey_items):
            if j in matched_bw:
                continue
            fp = item_fingerprint(bw_item)
            if fp[1] != inv_title or fp[2] != inv_user:
                continue
            if _urls_match(inv_url, fp[3]):
                matched_inv.add(i)
                matched_bw.add(j)
                matches.append((inv_entry, bw_item))
                break

    missing = [e for i, e in enumerate(inventory) if i not in matched_inv]
    unexpected = [b for j, b in enumerate(bw_passkey_items) if j not in matched_bw]

    return {
        "matched": matches,
        "missing_from_bw": missing,
        "unexpected_in_bw": unexpected,
    }
