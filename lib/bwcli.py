"""Thin wrapper around the bw CLI.

bw list items flag confirmed via `bw list --help` (bw 2026.8.0):
  --collectionid <id>      filter by collection id (singular, lowercase)
  --organizationid <id>    filter by org id

bw import flag confirmed via `bw import --help`:
  --organizationid <id>    org to import into

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


def check_prereqs() -> None:
    _bw_path()
    status_raw = _run(["status", "--raw"])
    try:
        status = json.loads(status_raw)
    except json.JSONDecodeError:
        raise BWError(f"bw status returned non-JSON: {status_raw!r}")
    if status.get("status") == "unauthenticated":
        raise NotLoggedIn("bw: not logged in — run: bw login")
    if status.get("status") == "locked":
        raise NotUnlocked("bw: vault is locked — run: bw unlock and export BW_SESSION")


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
