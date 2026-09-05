"""Thin wrapper around the bw CLI.

bw list items flag confirmed via `bw list --help` (bw 2026.8.0):
  --collectionid <id>      filter by collection id (singular, lowercase)
  --organizationid <id>    filter by org id

bw import flag confirmed via `bw import --help`:
  --organizationid <id>    org to import into

bw login/unlock flags confirmed via --help (bw 2026.8.0):
  bw login --raw           print only the session key to stdout
  bw login --apikey        log in with BW_CLIENTID / BW_CLIENTSECRET env vars
  bw unlock --raw          print only the session key to stdout
  bw unlock --passwordenv  env var name whose value is the master password

bw config server confirmed via --help (bw 2026.8.0):
  bw config server         print the currently configured server URL
  bw config server <url>   set the server URL (global CLI state)
  No per-invocation env var override exists; state is global.
  Switching server invalidates any existing session.

SSH key type 5 is supported in bw 2026.8.0.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


class BWError(Exception):
    pass


class NotLoggedIn(BWError):
    pass


class NotUnlocked(BWError):
    pass


_SERVER_MAP: dict[str, str] = {
    "us": "https://vault.bitwarden.com",
    "eu": "https://vault.bitwarden.eu",
}


def resolve_server(spec: str) -> str:
    """Return the canonical server URL for spec.

    Accepts 'us', 'eu', or any full https:// URL.  Raises BWError for anything else.
    """
    if spec in _SERVER_MAP:
        return _SERVER_MAP[spec]
    if spec.startswith("https://"):
        return spec
    raise BWError(
        f"Invalid bitwardenServer {spec!r}: must be 'us', 'eu', or a full https:// URL"
    )


def _current_server() -> str:
    """Return the server URL currently configured in the bw CLI."""
    result = subprocess.run(
        [_bw_path(), "config", "server"],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    if result.returncode != 0:
        raise BWError(f"bw config server failed: {result.stderr.strip()}")
    return result.stdout.strip()


def ensure_server(spec: str) -> None:
    """Switch the bw CLI server if it does not match spec.

    Prints a note to stderr when switching.  Clears BW_SESSION when the server
    changes because the existing session is no longer valid for the new server.
    """
    desired = resolve_server(spec)
    current = _current_server()
    if current == desired:
        return
    print(f"Bitwarden server: {desired}", file=sys.stderr)
    result = subprocess.run(
        [_bw_path(), "config", "server", desired],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    if result.returncode != 0:
        raise BWError(f"bw config server failed: {result.stderr.strip()}")
    if os.environ.get("BW_SESSION"):
        print(
            "Bitwarden server changed — existing session is no longer valid; will re-authenticate.",
            file=sys.stderr,
        )
        del os.environ["BW_SESSION"]


def _run(args: list[str], input_data: str | None = None) -> str:
    session = os.environ.get("BW_SESSION", "")
    env = os.environ.copy()

    cmd = [_bw_path()] + args
    if session:
        cmd += ["--session", session]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            input=input_data,
            env=env,
        )
    except FileNotFoundError:
        raise BWError("bw not found on PATH — install the Bitwarden CLI first")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "not logged in" in stderr.lower():
            raise NotLoggedIn("bw: not logged in — run: bw login")
        if "vault is locked" in stderr.lower():
            raise NotUnlocked("bw: vault is locked — run: bw unlock and export BW_SESSION")
        raise BWError(f"bw {args[0]} failed (exit {result.returncode}): {stderr}")

    return result.stdout


def _bw_path() -> str:
    path = shutil.which("bw")
    if not path:
        raise BWError("bw not found on PATH — install the Bitwarden CLI first")
    return path


def _bw_status() -> str:
    """Return the vault status string from `bw status`."""
    try:
        raw = _run(["status"])
    except BWError:
        return "unauthenticated"
    try:
        return json.loads(raw).get("status", "unauthenticated")
    except json.JSONDecodeError:
        raise BWError(f"bw status returned non-JSON: {raw!r}")


def _spawn(cmd: list[str], *, capture_stdout: bool = False) -> str:
    """Run an auth command with stdin/stderr inherited; optionally capture stdout."""
    try:
        result = subprocess.run(
            cmd,
            stdin=sys.stdin,
            stderr=sys.stderr,
            stdout=subprocess.PIPE if capture_stdout else None,
            text=capture_stdout,
            env=os.environ.copy(),
        )
    except FileNotFoundError:
        raise BWError("bw not found on PATH — install the Bitwarden CLI first")
    if result.returncode != 0:
        raise BWError(f"bw {cmd[1]} failed (exit {result.returncode})")
    return result.stdout.strip() if capture_stdout else ""


def ensure_session(server: str = "us") -> None:
    """Log in and unlock Bitwarden, storing the session key in os.environ['BW_SESSION'].

    Non-interactive env vars (all optional):
      BW_CLIENTID / BW_CLIENTSECRET  — API key login (skips email/password prompt)
      BW_PASSWORD                    — master password for non-interactive unlock

    2FA is handled by the interactive terminal flow when env vars are absent.
    The session key is never printed or written to disk.

    server: 'us' (default), 'eu', or a full https:// URL.  ensure_server() runs
    first so a server switch surfaces as unauthenticated and triggers login.
    """
    ensure_server(server)
    bw = _bw_path()
    vault_status = _bw_status()

    if vault_status == "unlocked":
        return

    if vault_status == "unauthenticated":
        if os.environ.get("BW_CLIENTID") and os.environ.get("BW_CLIENTSECRET"):
            print("Not logged in to Bitwarden — starting bw login (API key)...", file=sys.stderr)
            _spawn([bw, "login", "--apikey"])
        else:
            print("Not logged in to Bitwarden — starting bw login...", file=sys.stderr)
            session = ""
            try:
                session = _spawn([bw, "login", "--raw"], capture_stdout=True)
            except BWError:
                # --raw failed; fall back to fully interactive login then unlock
                _spawn([bw, "login"])
            if session:
                os.environ["BW_SESSION"] = session
                if _bw_status() != "unlocked":
                    raise BWError("Bitwarden vault is not unlocked after login")
                return

    print("Unlocking vault...", file=sys.stderr)
    unlock_cmd = [bw, "unlock"]
    if os.environ.get("BW_PASSWORD"):
        unlock_cmd += ["--passwordenv", "BW_PASSWORD"]
    unlock_cmd.append("--raw")
    session = _spawn(unlock_cmd, capture_stdout=True)
    if not session:
        raise BWError("bw unlock --raw returned no session key")
    os.environ["BW_SESSION"] = session

    if _bw_status() != "unlocked":
        raise BWError("Bitwarden vault is not unlocked after unlock attempt")


def check_prereqs() -> None:
    ensure_session()


def sync() -> None:
    _run(["sync"])


def list_org_collections(org_id: str) -> list[dict]:
    raw = _run(["list", "org-collections", "--organizationid", org_id, "--raw"])
    return json.loads(raw) if raw.strip() else []


def list_items_in_collection(collection_id: str, org_id: str) -> list[dict]:
    raw = _run([
        "list", "items",
        "--collectionid", collection_id,
        "--organizationid", org_id,
        "--raw",
    ])
    return json.loads(raw) if raw.strip() else []


def item_fingerprint(item: dict) -> tuple:
    login = item.get("login") or {}
    uris = login.get("uris") or []
    primary_uri = uris[0].get("uri", "") if uris else ""
    return (
        item.get("type", 0),
        (item.get("name") or "").strip().lower(),
        (login.get("username") or "").lower(),
        primary_uri.lower(),
    )


def fingerprints_in_collection(collection_id: str, org_id: str) -> set[tuple]:
    items = list_items_in_collection(collection_id, org_id)
    return {item_fingerprint(it) for it in items}


def encode_item(item: dict) -> str:
    raw = json.dumps(item)
    return base64.b64encode(raw.encode()).decode()


def create_item(item: dict, org_id: str) -> dict:
    payload = dict(item)
    payload["organizationId"] = org_id
    encoded = encode_item(payload)
    raw = _run(["create", "item", encoded, "--raw"])
    return json.loads(raw)


def create_attachment(file_path: Path, item_id: str) -> dict:
    raw = _run(["create", "attachment", "--file", str(file_path), "--itemid", item_id, "--raw"])
    return json.loads(raw)


def bulk_import(import_file: Path, org_id: str, format_name: str = "bitwardenjson") -> None:
    _run(["import", format_name, str(import_file), "--organizationid", org_id])


def get_item(item_id: str) -> dict:
    raw = _run(["get", "item", item_id, "--raw"])
    return json.loads(raw)


def list_folders() -> list[dict]:
    raw = _run(["list", "folders", "--raw"])
    return json.loads(raw) if raw.strip() else []


def list_items_in_folder(folder_id: str) -> list[dict]:
    raw = _run(["list", "items", "--folderid", folder_id, "--raw"])
    return json.loads(raw) if raw.strip() else []


def list_all_items() -> list[dict]:
    raw = _run(["list", "items", "--raw"])
    return json.loads(raw) if raw.strip() else []


def fingerprints_in_folder(folder_id: str) -> set[tuple]:
    items = list_items_in_folder(folder_id)
    return {item_fingerprint(it) for it in items}


def bulk_import_personal(import_file: Path, format_name: str = "bitwardenjson") -> None:
    _run(["import", format_name, str(import_file)])


def create_personal_item(item: dict) -> dict:
    encoded = encode_item(item)
    raw = _run(["create", "item", encoded, "--raw"])
    return json.loads(raw)


def edit_item(item_id: str, item: dict) -> dict:
    """Edit an existing item by ID.

    Syntax confirmed from bw 2026.8.0 --help:
      bw edit item <id> [encodedJson] [options]
    The encoded JSON is the full item object, base64-encoded.
    """
    encoded = encode_item(item)
    raw = _run(["edit", "item", item_id, encoded, "--raw"])
    return json.loads(raw)


def delete_item(item_id: str) -> None:
    """Move an item to trash (soft delete). Use --permanent flag explicitly to hard-delete."""
    _run(["delete", "item", item_id])


def list_personal_items() -> list[dict]:
    """Return items from the personal vault only (organizationId is null)."""
    all_items = list_all_items()
    return [it for it in all_items if not it.get("organizationId")]
