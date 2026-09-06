"""Thin wrapper around the op CLI for live vault sharing lookups.

Used by split.py to resolve real ACL data for a vault missing a
vaultDestination classification, before falling back to the name-substring
guess.  Every function returns None (or an empty result) rather than raising
when op is missing or a lookup fails, so callers can fall back silently.

op commands and the JSON fields read from each, confirmed via `op --help`:
  op whoami --format json [--account <acct>]
    -> object; reads "email"
  op user get --me --format json [--account <acct>]
    -> object; reads "email"  (fallback: whoami reports on CLI signin
    sessions and fails when authentication goes through desktop-app
    integration, while data commands authorize via the app)
  op vault user list <vault> --format json [--account <acct>]
    -> list of objects; reads "email" and "permissions" from each
  op vault group list <vault> --format json [--account <acct>]
    -> list of objects; reads "name" and "permissions" from each
  op user list --group <name> --format json [--account <acct>]
    -> list of objects; reads "email" from each

Only grants that include "allow_viewing" in "permissions" count as content
sharing. 1Password's built-in Owners/Administrators groups (and any user
entry granted only "allow_managing") appear on every vault for
administrative purposes without granting read access to its contents, and
are excluded from the emails/groups returned here. An entry with no
"permissions" list at all is included, since its access can't be judged.
"""

from __future__ import annotations

import json
import shutil
import subprocess


def _op_path() -> str | None:
    return shutil.which("op")


def _account_flag(op_account: str | None) -> list[str]:
    return ["--account", op_account] if op_account else []


def _run_json(args: list[str]):
    try:
        result = subprocess.run(args, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return None


def owner_email(op_account: str | None) -> str | None:
    op = _op_path()
    if not op:
        return None
    account_flag = _account_flag(op_account)
    for args in (
        [op, "whoami", "--format", "json"],
        [op, "user", "get", "--me", "--format", "json"],
    ):
        data = _run_json(args + account_flag)
        if isinstance(data, dict):
            email = data.get("email")
            if isinstance(email, str):
                return email
    return None


def _has_viewing_access(entry: dict) -> bool:
    permissions = entry.get("permissions")
    if not isinstance(permissions, list):
        return True
    return "allow_viewing" in permissions


def vault_members(vault_name: str, op_account: str | None) -> dict | None:
    op = _op_path()
    if not op:
        return None
    account_flag = _account_flag(op_account)

    users = _run_json(
        [op, "vault", "user", "list", vault_name, "--format", "json"] + account_flag
    )
    if not isinstance(users, list):
        return None
    emails = [
        u.get("email") for u in users
        if isinstance(u, dict) and isinstance(u.get("email"), str)
        and _has_viewing_access(u)
    ]

    groups_data = _run_json(
        [op, "vault", "group", "list", vault_name, "--format", "json"] + account_flag
    )
    groups = []
    if isinstance(groups_data, list):
        groups = [
            g.get("name") for g in groups_data
            if isinstance(g, dict) and isinstance(g.get("name"), str)
            and _has_viewing_access(g)
        ]

    failed_groups = []
    for group_name in groups:
        members = _run_json(
            [op, "user", "list", "--group", group_name, "--format", "json"] + account_flag
        )
        if not isinstance(members, list):
            failed_groups.append(group_name)
            continue
        emails.extend(
            m.get("email") for m in members
            if isinstance(m, dict) and isinstance(m.get("email"), str)
        )

    return {"emails": emails, "groups": groups, "failed_groups": failed_groups}
