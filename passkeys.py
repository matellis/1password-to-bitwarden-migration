#!/usr/bin/env python3
"""Build a passkey transfer checklist from 1Password and Bitwarden sources.

Four input modes, all combinable:

  --from-pux PATH      Defensively scan a .1pux export for any key or string
                       value matching /passkey|webauthn|fido/.  Reports item
                       title + vault + JSON path of each hit.  Desktop exports
                       do not carry passkey credentials; zero hits is normal.

  --from-bitwarden     Read-only bw list items: report login items whose
                       login.fido2Credentials is non-empty.  Use this after
                       migration to confirm which items now hold passkeys in
                       Bitwarden.

  --manual FILE        Parse a transcribed list built from the 1Password app's
                       =passkey search filter.  Format: one entry per line:
                         - Site name | username | https://url
                       Fields after the first are optional.

  --bridge FILE        Parse a Bridge/checklist JSON file produced by the iOS
                       companion app (format: {entries:[{title,username,url}]}).
                       Also accepted by --mark-passkeys in split.py.

Gap report mode:

  --gap-report         Compare the loaded inventory (--manual / --bridge) with
                       passkey items already in Bitwarden (--from-bitwarden).
                       Prints per-account: expected, confirmed, missing, and
                       unexpected passkeys.  Always exits 0.
                       Note: org items are visible to the admin; personal-vault
                       passkeys require each user to run this themselves.

Output:
  work/passkeys/<name>.json    Structured passkey list for each source run.
  work/passkeys/index.html     Self-contained mobile checklist (no external
                               resources).  One card per passkey, checkboxes
                               persisted to localStorage, grouped by account
                               and source, with a progress count.

Use --account NAME to label the data source.  Defaults to the file stem for
--from-pux and --manual; defaults to "bitwarden" for --from-bitwarden.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from lib import passkey_inventory as pkinv

_PASSKEY_RE = re.compile(r"passkey|webauthn|fido", re.IGNORECASE)
_WORK_DIR = Path("work") / "passkeys"


# --- File helpers ---

def _make_work_dir() -> Path:
    old_umask = os.umask(0o077)
    try:
        _WORK_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    finally:
        os.umask(old_umask)
    return _WORK_DIR


def _write_secure(path: Path, data: Any) -> None:
    old_umask = os.umask(0o177)
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(path, 0o600)
    finally:
        os.umask(old_umask)


def _write_html_secure(path: Path, content: str) -> None:
    old_umask = os.umask(0o177)
    try:
        path.write_text(content, encoding="utf-8")
        os.chmod(path, 0o600)
    finally:
        os.umask(old_umask)


# --- Recursive JSON scanner ---

def _scan_json(value: Any, path: str, hits: list[tuple[str, str, str]]) -> None:
    """Walk value recursively; append (kind, path, snippet) for every passkey hit."""
    if isinstance(value, dict):
        for k, v in value.items():
            child_path = f"{path}.{k}"
            if _PASSKEY_RE.search(str(k)):
                hits.append(("key", child_path, repr(v)[:120]))
            _scan_json(v, child_path, hits)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _scan_json(v, f"{path}[{i}]", hits)
    elif isinstance(value, str):
        if _PASSKEY_RE.search(value):
            hits.append(("value", path, value[:120]))


def _item_login_info(item: dict) -> tuple[str, str]:
    """Return (username, primary_url) from a raw 1pux item, best-effort."""
    details = item.get("details") or {}
    overview = item.get("overview") or {}

    username = ""
    for lf in details.get("loginFields") or []:
        if lf.get("designation") == "username":
            username = lf.get("value") or ""
            break

    urls = overview.get("urls") or []
    url = urls[0].get("url", "") if urls else overview.get("url", "")

    return username, url


# --- Mode: --from-pux ---

def from_pux(pux_path: Path, account_name: str) -> list[dict]:
    """Scan a .1pux export for passkey/webauthn/fido markers.

    Returns one entry per affected item (not per hit), with hits attached.
    """
    with zipfile.ZipFile(pux_path) as zf:
        raw = zf.read("export.data")
    export_data = json.loads(raw)

    accounts = export_data.get("accounts") or []
    entries: list[dict] = []

    for acc in accounts:
        for vault in (acc.get("vaults") or []):
            vault_name = (vault.get("attrs") or {}).get("name", "?")
            for i, item in enumerate(vault.get("items") or []):
                title = (item.get("overview") or {}).get("title") or item.get("uuid") or "?"
                hits: list[tuple[str, str, str]] = []
                _scan_json(item, f"items[{i}]", hits)

                if hits:
                    username, url = _item_login_info(item)
                    entries.append({
                        "account": account_name,
                        "source": "pux",
                        "site": title,
                        "username": username,
                        "url": url,
                        "vault": vault_name,
                        "hits": [
                            {"kind": h[0], "path": h[1], "snippet": h[2]}
                            for h in hits
                        ],
                    })

    return entries


# --- Mode: --from-bitwarden ---

def from_bitwarden(account_name: str) -> list[dict]:
    """Return login items in Bitwarden that have non-empty fido2Credentials."""
    from lib import bwcli
    bwcli.sync()
    all_items = bwcli.list_all_items()

    entries: list[dict] = []
    for item in all_items:
        if item.get("type") != 1:
            continue
        login = item.get("login") or {}
        if not (login.get("fido2Credentials") or []):
            continue

        username = login.get("username") or ""
        uris = login.get("uris") or []
        url = uris[0].get("uri", "") if uris else ""

        entries.append({
            "account": account_name,
            "source": "bitwarden",
            "site": item.get("name") or "?",
            "username": username,
            "url": url,
        })

    return entries


# --- Mode: --bridge ---

def parse_bridge(file_path: Path, account_name: str) -> list[dict]:
    """Parse a Bridge/checklist JSON file ({entries:[{title,username,url}]})."""
    raw = pkinv.load_inventory(file_path)
    entries = []
    for e in raw:
        entries.append({
            "account": account_name,
            "source": "bridge",
            "site": e.get("site") or e.get("title") or "?",
            "username": e.get("username") or "",
            "url": e.get("url") or "",
        })
    return entries


# --- Mode: --manual ---

def parse_manual(file_path: Path, account_name: str) -> list[dict]:
    """Parse lines like: - Site name | username | https://url"""
    entries: list[dict] = []
    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
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
        entries.append({
            "account": account_name,
            "source": "manual",
            "site": site,
            "username": username,
            "url": url,
        })
    return entries


# --- Mode: --gap-report ---

def print_gap_report(inventory_entries: list[dict], bw_entries: list[dict]) -> None:
    """Print a per-account gap report comparing inventory against BW passkey items."""

    def _to_pkinv(entries: list[dict]) -> list[dict]:
        return [{"site": e["site"], "username": e["username"], "url": e["url"]} for e in entries]

    def _to_bw_item(entry: dict) -> dict:
        return {
            "type": 1,
            "name": entry["site"],
            "login": {
                "username": entry["username"],
                "uris": [{"uri": entry["url"]}] if entry.get("url") else [],
                "fido2Credentials": [{}],
            },
        }

    accounts = sorted({e["account"] for e in inventory_entries} | {e["account"] for e in bw_entries})

    for account in accounts:
        inv = [e for e in inventory_entries if e["account"] == account]
        bw = [e for e in bw_entries if e["account"] == account]

        inv_plain = _to_pkinv(inv)
        bw_items = [_to_bw_item(e) for e in bw]

        report = pkinv.gap_report(inv_plain, bw_items)

        print(f"\nAccount: {account}")
        print(f"  Expected (from inventory):     {len(inv)}")
        print(f"  Confirmed in Bitwarden:        {len(bw)}")
        print(f"  Matched:                       {len(report['matched'])}")
        if report["missing_from_bw"]:
            print(f"  Missing from Bitwarden ({len(report['missing_from_bw'])}):")
            for e in report["missing_from_bw"]:
                user = f" ({e['username']})" if e.get("username") else ""
                print(f"    - {e['site']}{user}")
        else:
            print("  Missing from Bitwarden:        none")
        if report["unexpected_in_bw"]:
            print(f"  Unexpected in Bitwarden ({len(report['unexpected_in_bw'])}):")
            for item in report["unexpected_in_bw"]:
                login = item.get("login") or {}
                user = f" ({login.get('username', '')})" if login.get("username") else ""
                print(f"    - {item.get('name', '?')}{user}")
        else:
            print("  Unexpected in Bitwarden:       none")


# --- HTML generation ---

def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


_SOURCE_LABELS = {
    "pux": "Found in 1Password export (review — may not be real passkeys)",
    "bitwarden": "Confirmed in Bitwarden",
    "manual": "Transcribed from 1Password app",
    "bridge": "From Bridge iOS app checklist",
}


def build_html(all_entries: list[dict]) -> str:
    """Generate a self-contained mobile-first checklist HTML string.

    No external resources.  Checkboxes persist via localStorage.
    No secret fields (password values) appear anywhere in the output.
    """
    groups: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for entry in all_entries:
        groups[entry.get("account", "?")][entry.get("source", "manual")].append(entry)

    total = len(all_entries)

    parts: list[str] = []
    parts.append("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Passkey Transfer Checklist</title>
<style>
*,*::before,*::after{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  max-width:640px;margin:0 auto;padding:1rem 1rem 3rem;background:#f5f5f7;
  color:#1d1d1f;font-size:16px}
h1{font-size:1.4rem;margin:0 0 0.25rem}
#progress{font-weight:600;color:#007aff;margin:0 0 1.25rem;font-size:1rem}
h2{font-size:1rem;font-weight:700;margin:1.75rem 0 0.25rem;color:#1d1d1f}
h3{font-size:0.78rem;text-transform:uppercase;letter-spacing:0.06em;
  color:#888;margin:0.5rem 0 0.5rem;font-weight:600}
.cards{display:flex;flex-direction:column;gap:0.5rem}
.card{background:#fff;border-radius:12px;padding:0.75rem 1rem;
  box-shadow:0 1px 4px rgba(0,0,0,0.07)}
.card label{display:flex;align-items:flex-start;gap:0.75rem;cursor:pointer}
input[type=checkbox]{margin-top:2px;width:20px;height:20px;
  accent-color:#007aff;cursor:pointer;flex-shrink:0}
.card-body{flex:1;min-width:0}
.site{font-weight:600;font-size:1rem;word-break:break-word}
.username{font-size:0.85rem;color:#555;margin-top:2px;word-break:break-all}
.url{margin-top:4px}
.url a{font-size:0.8rem;color:#007aff;text-decoration:none;word-break:break-all}
.done .site{text-decoration:line-through;color:#aaa}
.empty{color:#888;font-style:italic;padding:1rem 0}
</style>
</head>
<body>
<h1>Passkey Transfer Checklist</h1>
<p id="progress">Loading…</p>
""")

    idx = 0
    for account in sorted(groups):
        parts.append(f'<section><h2>{_esc(account)}</h2>')
        for source in sorted(groups[account]):
            label = _SOURCE_LABELS.get(source, source)
            parts.append(f'<h3>{_esc(label)}</h3><div class="cards">')
            for entry in groups[account][source]:
                key = f"pk-{_esc(account)}-{_esc(source)}-{idx}"
                idx += 1
                site = _esc(entry.get("site") or "?")
                username = _esc(entry.get("username") or "")
                url_raw = entry.get("url") or ""
                url_html = (
                    f'<div class="url"><a href="{_esc(url_raw)}" target="_blank" rel="noopener">'
                    f'{_esc(url_raw)}</a></div>'
                    if url_raw else ""
                )
                username_html = f'<div class="username">{username}</div>' if username else ""
                parts.append(
                    f'<div class="card" id="card-{idx}">'
                    f'<label>'
                    f'<input type="checkbox" class="pk-check" data-key="{key}">'
                    f'<div class="card-body">'
                    f'<div class="site">{site}</div>'
                    f'{username_html}'
                    f'{url_html}'
                    f'</div></label></div>'
                )
            parts.append('</div>')
        parts.append('</section>')

    if total == 0:
        parts.append('<p class="empty">No passkey entries yet. Add entries with --manual, --from-pux, or --from-bitwarden.</p>')

    parts.append(f"""
<script>
(function(){{
var checks=document.querySelectorAll('.pk-check');
var total={total};
function update(){{
  var done=0;
  checks.forEach(function(c){{
    var card=c.closest('.card');
    if(c.checked){{done++;card.classList.add('done');}}
    else{{card.classList.remove('done');}}
  }});
  document.getElementById('progress').textContent=done+' of '+total+' transferred';
}}
checks.forEach(function(c){{
  if(localStorage.getItem(c.dataset.key)==='1')c.checked=true;
  c.addEventListener('change',function(){{
    localStorage.setItem(c.dataset.key,c.checked?'1':'0');
    update();
  }});
}});
update();
}})();
</script>
</body>
</html>""")

    return "\n".join(parts)


# --- Main ---

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a passkey transfer checklist."
    )
    parser.add_argument("--account", metavar="NAME",
                        help="Label for this data source (used in output filenames and checklist grouping)")
    parser.add_argument("--from-pux", metavar="PATH",
                        help="Scan a .1pux export for passkey/webauthn/fido markers")
    parser.add_argument("--from-bitwarden", action="store_true",
                        help="List Bitwarden login items with non-empty fido2Credentials (read-only)")
    parser.add_argument("--manual", metavar="FILE",
                        help="Parse a transcribed passkey list (- Site | username | url)")
    parser.add_argument("--bridge", metavar="FILE",
                        help="Parse a Bridge iOS app checklist JSON ({entries:[{title,username,url}]})")
    parser.add_argument("--gap-report", action="store_true",
                        help=(
                            "Compare loaded inventory (--manual/--bridge) against Bitwarden "
                            "(--from-bitwarden) and print missing/unexpected passkeys. Always exits 0."
                        ))
    parser.add_argument(
        "--server", metavar="SPEC", default="us",
        help="Bitwarden server for --from-bitwarden: 'us' (default), 'eu', or full https:// URL",
    )
    args = parser.parse_args()

    if not (args.from_pux or args.from_bitwarden or args.manual or args.bridge or args.gap_report):
        parser.print_help()
        sys.exit(1)

    work_dir = _make_work_dir()

    if args.from_pux:
        pux_path = Path(args.from_pux)
        if not pux_path.exists():
            sys.exit(f"File not found: {pux_path}")
        account_name = args.account or pux_path.stem
        print(f"Scanning {pux_path} for passkey/webauthn/fido markers...")
        entries = from_pux(pux_path, account_name)
        out_path = work_dir / f"{account_name}-pux.json"
        _write_secure(out_path, entries)
        if not entries:
            print(
                "No passkey markers found.\n"
                "Desktop .1pux exports do not carry passkey credentials — this is expected.\n"
                "To build your passkey inventory: open the 1Password app, search =passkey,\n"
                "transcribe results to a markdown file, then re-run with --manual <file>."
            )
        else:
            print(f"{len(entries)} item(s) with passkey-related markers found.")
            print("Review the hits field in each entry — most will be login items with")
            print("passkey field labels, not the passkey credential itself.")
        print(f"Written to {out_path}")

    if args.from_bitwarden:
        from lib import bwcli as _bwcli
        _bwcli.ensure_session(args.server)
        account_name = args.account or "bitwarden"
        print("Syncing and checking Bitwarden for passkey-bearing items...")
        entries = from_bitwarden(account_name)
        out_path = work_dir / f"{account_name}-bitwarden.json"
        _write_secure(out_path, entries)
        if not entries:
            print("No passkey items found (fido2Credentials empty on all login items).")
        else:
            print(f"{len(entries)} passkey item(s) confirmed in Bitwarden.")
        print(f"Written to {out_path}")

    if args.manual:
        manual_path = Path(args.manual)
        if not manual_path.exists():
            sys.exit(f"File not found: {manual_path}")
        account_name = args.account or manual_path.stem
        print(f"Parsing {manual_path}...")
        entries = parse_manual(manual_path, account_name)
        out_path = work_dir / f"{account_name}-manual.json"
        _write_secure(out_path, entries)
        print(f"{len(entries)} passkey entry/entries parsed.")
        print(f"Written to {out_path}")

    if args.bridge:
        bridge_path = Path(args.bridge)
        if not bridge_path.exists():
            sys.exit(f"File not found: {bridge_path}")
        account_name = args.account or bridge_path.stem
        print(f"Parsing Bridge JSON {bridge_path}...")
        entries = parse_bridge(bridge_path, account_name)
        out_path = work_dir / f"{account_name}-bridge.json"
        _write_secure(out_path, entries)
        print(f"{len(entries)} passkey entry/entries loaded from Bridge JSON.")
        print(f"Written to {out_path}")

    if args.gap_report:
        # Collect inventory entries from all JSON files in work/passkeys/ that are
        # not from bitwarden source (manual + bridge = expected set).
        inv_entries: list[dict] = []
        bw_entries: list[dict] = []
        for json_file in sorted(work_dir.glob("*.json")):
            try:
                with open(json_file) as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    continue
                for e in data:
                    if e.get("source") == "bitwarden":
                        bw_entries.append(e)
                    elif e.get("source") in ("manual", "bridge"):
                        inv_entries.append(e)
            except Exception:
                pass
        print_gap_report(inv_entries, bw_entries)

    # Rebuild the combined checklist from all JSON files in work/passkeys/.
    all_entries: list[dict] = []
    for json_file in sorted(work_dir.glob("*.json")):
        try:
            with open(json_file) as f:
                data = json.load(f)
            if isinstance(data, list):
                all_entries.extend(data)
        except Exception:
            pass

    html_path = work_dir / "index.html"
    _write_html_secure(html_path, build_html(all_entries))
    print(f"\nChecklist: {html_path} ({len(all_entries)} total entries)")
    print("Open in a mobile browser to track passkey transfers.")


if __name__ == "__main__":
    main()
