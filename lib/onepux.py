"""1pux parsing and conversion to Bitwarden org-import JSON."""

from __future__ import annotations

import datetime
import json
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CATEGORY_TO_BW_TYPE: dict[str, int] = {
    "001": 1,
    "005": 1,
    "111": 1,
    "002": 2,
    "003": 3,
    "004": 4,
    "006": 2,
    "112": 5,
}

_LOGIN_CATEGORIES = {"001", "005", "111"}
_CARD_CATEGORY = "003"
_IDENTITY_CATEGORY = "004"
_DOCUMENT_CATEGORY = "006"
_SSH_CATEGORY = "112"

_CC_FIELD_ID_MAP = {
    "ccnum": "number",
    "cvv": "code",
    "cardholder": "cardholderName",
    "type": "brand",
}

_IDENTITY_FIELD_ID_MAP = {
    "firstname": "firstName",
    "lastname": "lastName",
    "middlename": "middleName",
    "username": "username",
    "company": "company",
    "email": "email",
    "defphone": "phone",
    "phone": "phone",
    "cellphone": "phone",
    "sex": None,
    "birthdate": None,
    "jobtitle": None,
    "address1": "address1",
    "address2": "address2",
    "city": "city",
    "state": "state",
    "zip": "postalCode",
    "country": "country",
    "reminderans": None,
    "remindfreq": None,
    "homephone": None,
    "busphone": None,
    "title": "title",
    "initial": "middleName",
    "passportno": "passportNumber",
    "driverlicno": "licenseNumber",
    "ssn": "ssn",
}


_FIELD_VALUE_LIMIT = 4900
_NOTES_LIMIT = 9900


@dataclass
class ConversionResult:
    bulk_items: list[dict] = field(default_factory=list)
    attachment_items: list[dict] = field(default_factory=list)
    archived_count: int = 0
    dupe_count: int = 0
    oversized_fields: list[str] = field(default_factory=list)


def parse_export(pux_path: Path, extract_to: Path) -> tuple[dict, dict[str, Path]]:
    """
    Open pux_path, extract files/ entries to extract_to.
    Returns (export_data dict, {docId: extracted_path}).
    """
    extract_to.mkdir(parents=True, exist_ok=True)
    files_map: dict[str, Path] = {}

    with zipfile.ZipFile(pux_path, "r") as zf:
        raw = zf.read("export.data")
        export_data = json.loads(raw)

        for name in zf.namelist():
            if name.startswith("files/") and name != "files/":
                basename = Path(name).name
                doc_id = basename.split("___")[0]
                dest = extract_to / basename
                dest.write_bytes(zf.read(name))
                files_map[doc_id] = dest

    return export_data, files_map


def vaults(export_data: dict) -> list[dict]:
    accounts = export_data.get("accounts", [])
    if not accounts:
        return []
    return accounts[0].get("vaults", [])


def vault_slug(vault_attrs: dict) -> str:
    name = vault_attrs.get("name", vault_attrs.get("uuid", "vault"))
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", name).strip("_")
    return slug or "vault"


def make_collection(org_id: str, vault_name: str) -> tuple[str, dict]:
    coll_id = str(uuid.uuid4())
    return coll_id, {
        "id": coll_id,
        "organizationId": org_id,
        "name": vault_name,
        "externalId": None,
    }


def make_import_doc(org_id: str, collection: dict, items: list[dict]) -> dict:
    return {
        "encrypted": False,
        "collections": [collection],
        "items": items,
    }


def convert_vault_items(
    vault_items: list[dict],
    org_id: str,
    collection_id: str,
    files_map: dict[str, Path],
    include_archived: bool = False,
    ssh_key_supported: bool = True,
) -> ConversionResult:
    result = ConversionResult()
    seen_fingerprints: set[tuple] = set()

    for raw_item in vault_items:
        state = raw_item.get("state", "active")
        if state == "archived" and not include_archived:
            result.archived_count += 1
            continue

        bw_item, attach_paths, oversized = _convert_item(
            raw_item, org_id, collection_id, files_map, ssh_key_supported
        )
        result.oversized_fields.extend(oversized)

        fp = _item_fingerprint(bw_item)
        if fp in seen_fingerprints:
            result.dupe_count += 1
            continue
        seen_fingerprints.add(fp)

        if attach_paths:
            result.attachment_items.append({"item": bw_item, "files": attach_paths})
        else:
            result.bulk_items.append(bw_item)

    return result


def _item_fingerprint(item: dict) -> tuple:
    login = item.get("login") or {}
    uris = login.get("uris") or []
    primary_uri = uris[0]["uri"] if uris else ""
    return (
        item.get("type", 0),
        (item.get("name") or "").strip().lower(),
        (login.get("username") or "").lower(),
        primary_uri.lower(),
    )


def _convert_item(
    raw: dict,
    org_id: str,
    collection_id: str,
    files_map: dict[str, Path],
    ssh_key_supported: bool,
) -> tuple[dict, list[Path], list[str]]:
    overview = raw.get("overview") or {}
    details = raw.get("details") or {}
    cat = str(raw.get("categoryUuid", "002"))
    bw_type = CATEGORY_TO_BW_TYPE.get(cat, 2)

    if cat == _SSH_CATEGORY and not ssh_key_supported:
        bw_type = 2

    name = overview.get("title") or raw.get("uuid") or "Untitled"
    notes_parts: list[str] = []
    raw_notes = details.get("notesPlain") or ""
    if raw_notes:
        notes_parts.append(raw_notes)

    tags = overview.get("tags") or []
    if tags:
        notes_parts.append("Tags: " + ", ".join(tags))

    bw_item: dict[str, Any] = {
        "id": None,
        "organizationId": org_id,
        "collectionIds": [collection_id],
        "type": bw_type,
        "name": name,
        "notes": None,
        "favorite": (raw.get("favIndex") or 0) > 0,
        "fields": [],
        "passwordHistory": _convert_password_history(details.get("passwordHistory") or []),
        "reprompt": 0,
    }

    attach_paths: list[Path] = []
    oversized_fields: list[str] = []

    if bw_type == 1:
        _fill_login(bw_item, overview, details, notes_parts, oversized_fields)
    elif bw_type == 3:
        _fill_card(bw_item, details, notes_parts, oversized_fields)
    elif bw_type == 4:
        _fill_identity(bw_item, details, notes_parts, oversized_fields)
    elif bw_type == 5:
        _fill_ssh_key(bw_item, details, notes_parts, oversized_fields)
    else:
        bw_item["secureNote"] = {"type": 0}

    if cat == _DOCUMENT_CATEGORY:
        doc_attrs = details.get("documentAttributes") or {}
        doc_id = doc_attrs.get("documentId") or ""
        if doc_id and doc_id in files_map:
            attach_paths.append(files_map[doc_id])

    ref_paths = _collect_reference_attachments(details, files_map, bw_item)
    attach_paths.extend(ref_paths)

    notes = "\n".join(notes_parts) if notes_parts else None
    if notes and len(notes) > _NOTES_LIMIT:
        notes = notes[:_NOTES_LIMIT] + "…[truncated: exceeded Bitwarden's 10000-character notes limit]"
    bw_item["notes"] = notes
    return bw_item, attach_paths, oversized_fields


def _fill_login(
    bw_item: dict, overview: dict, details: dict, notes_parts: list[str],
    oversized_fields: list[str],
) -> None:
    username = ""
    password = ""
    totp = ""
    extra_login_fields: list[dict] = []
    item_name = bw_item["name"]

    for lf in details.get("loginFields") or []:
        designation = lf.get("designation") or ""
        value = lf.get("value") or ""
        if designation == "username":
            username = value
        elif designation == "password":
            password = value
        else:
            if value:
                bw_field = _make_bw_field(
                    lf.get("name") or lf.get("id") or "field", value, "string",
                    notes_parts, item_name, oversized_fields,
                )
                if bw_field:
                    extra_login_fields.append(bw_field)

    for section in details.get("sections") or []:
        section_title = section.get("title") or ""
        for f in section.get("fields") or []:
            ftype, fval = _extract_typed_value(f.get("value"))
            if ftype == "reference":
                continue
            if ftype == "totp" and not totp:
                totp = str(fval) if fval is not None else ""
                continue
            field_name = _field_display_name(section_title, f.get("title") or "")
            bw_field = _make_bw_field(field_name, fval, ftype, notes_parts, item_name, oversized_fields)
            if bw_field:
                bw_item["fields"].append(bw_field)

    bw_item["fields"].extend(extra_login_fields)

    urls = overview.get("urls") or []
    if not urls and overview.get("url"):
        urls = [{"url": overview["url"]}]

    bw_item["login"] = {
        "username": username or None,
        "password": password or None,
        "totp": totp or None,
        "uris": [{"match": None, "uri": u["url"]} for u in urls if u.get("url")],
    }


def _fill_card(
    bw_item: dict, details: dict, notes_parts: list[str], oversized_fields: list[str],
) -> None:
    card: dict[str, Any] = {
        "cardholderName": None,
        "brand": None,
        "number": None,
        "expMonth": None,
        "expYear": None,
        "code": None,
    }
    item_name = bw_item["name"]

    for section in details.get("sections") or []:
        section_title = section.get("title") or ""
        for f in section.get("fields") or []:
            field_id = f.get("id") or f.get("n") or ""
            clean_id = field_id.split(".")[0].lower()
            ftype, fval = _extract_typed_value(f.get("value"))

            if clean_id in _CC_FIELD_ID_MAP:
                bw_key = _CC_FIELD_ID_MAP[clean_id]
                if bw_key == "number":
                    card["number"] = str(fval) if fval is not None else None
                elif bw_key == "code":
                    card["code"] = str(fval) if fval is not None else None
                elif bw_key == "cardholderName":
                    card["cardholderName"] = str(fval) if fval is not None else None
                elif bw_key == "brand":
                    card["brand"] = str(fval) if fval is not None else None
            elif clean_id == "expiry" and ftype == "monthYear" and fval:
                ym = int(fval)
                card["expYear"] = str(ym // 100)
                card["expMonth"] = str(ym % 100)
            elif fval is not None:
                field_name = _field_display_name(section_title, f.get("title") or "")
                bw_field = _make_bw_field(field_name, fval, ftype, notes_parts, item_name, oversized_fields)
                if bw_field:
                    bw_item["fields"].append(bw_field)

    bw_item["card"] = card


def _fill_identity(
    bw_item: dict, details: dict, notes_parts: list[str], oversized_fields: list[str],
) -> None:
    identity: dict[str, Any] = {
        "title": None, "firstName": None, "middleName": None, "lastName": None,
        "address1": None, "address2": None, "address3": None,
        "city": None, "state": None, "postalCode": None, "country": None,
        "company": None, "email": None, "phone": None, "ssn": None,
        "username": None, "passportNumber": None, "licenseNumber": None,
    }
    item_name = bw_item["name"]

    for section in details.get("sections") or []:
        section_title = section.get("title") or ""
        for f in section.get("fields") or []:
            field_id = f.get("id") or f.get("n") or ""
            clean_id = field_id.split(".")[0].lower()
            ftype, fval = _extract_typed_value(f.get("value"))

            if clean_id in _IDENTITY_FIELD_ID_MAP:
                bw_key = _IDENTITY_FIELD_ID_MAP[clean_id]
                if bw_key and fval is not None:
                    if bw_key == "address1" and ftype == "address":
                        _unpack_address(identity, fval)
                    elif not identity[bw_key]:
                        identity[bw_key] = str(fval)
            elif ftype == "address" and fval:
                _unpack_address(identity, fval)
            elif fval is not None:
                field_name = _field_display_name(section_title, f.get("title") or "")
                bw_field = _make_bw_field(field_name, fval, ftype, notes_parts, item_name, oversized_fields)
                if bw_field:
                    bw_item["fields"].append(bw_field)

    bw_item["identity"] = identity


def _fill_ssh_key(
    bw_item: dict, details: dict, notes_parts: list[str], oversized_fields: list[str],
) -> None:
    private_key = ""
    public_key = ""
    fingerprint = ""
    item_name = bw_item["name"]

    for section in details.get("sections") or []:
        for f in section.get("fields") or []:
            field_id = (f.get("id") or "").lower()
            ftype, fval = _extract_typed_value(f.get("value"))
            val = str(fval) if fval is not None else ""
            if "private" in field_id:
                private_key = val
            elif "public" in field_id:
                public_key = val
            elif "fingerprint" in field_id:
                fingerprint = val
            elif val:
                bw_field = _make_bw_field(
                    f.get("title") or field_id, fval, ftype, notes_parts, item_name, oversized_fields,
                )
                if bw_field:
                    bw_item["fields"].append(bw_field)

    bw_item["sshKey"] = {
        "privateKey": private_key or None,
        "publicKey": public_key or None,
        "keyFingerprint": fingerprint or None,
    }


def _collect_reference_attachments(
    details: dict, files_map: dict[str, Path], bw_item: dict
) -> list[Path]:
    paths: list[Path] = []
    for section in details.get("sections") or []:
        for f in section.get("fields") or []:
            ftype, fval = _extract_typed_value(f.get("value"))
            if ftype == "reference" and fval:
                ref_id = str(fval)
                if ref_id in files_map:
                    paths.append(files_map[ref_id])
    return paths


def _unpack_address(identity: dict, addr: Any) -> None:
    if not isinstance(addr, dict):
        return
    if addr.get("street") and not identity["address1"]:
        identity["address1"] = addr["street"]
    if addr.get("city") and not identity["city"]:
        identity["city"] = addr["city"]
    if addr.get("state") and not identity["state"]:
        identity["state"] = addr["state"]
    if addr.get("zip") and not identity["postalCode"]:
        identity["postalCode"] = addr["zip"]
    if addr.get("country") and not identity["country"]:
        identity["country"] = addr["country"]


def _extract_typed_value(value_dict: Any) -> tuple[str, Any]:
    if not isinstance(value_dict, dict) or not value_dict:
        if value_dict is not None:
            return ("string", str(value_dict))
        return ("string", None)
    key = next(iter(value_dict))
    return (key, value_dict[key])


def _make_bw_field(
    name: str,
    value: Any,
    ftype: str,
    notes_parts: list[str],
    item_name: str,
    oversized_fields: list[str],
) -> dict | None:
    if value is None:
        return None
    if ftype == "reference":
        return None

    if ftype in ("concealed", "totp"):
        display = str(value)
        type_code = 1
    elif ftype == "date":
        try:
            dt = datetime.datetime.fromtimestamp(int(value), tz=datetime.timezone.utc)
            display = dt.strftime("%Y-%m-%d")
        except (ValueError, OSError):
            display = str(value)
        type_code = 0
    elif ftype == "monthYear":
        try:
            ym = int(value)
            display = f"{ym % 100:02d}/{ym // 100}"
        except (ValueError, TypeError):
            display = str(value)
        type_code = 0
    elif ftype == "address":
        if isinstance(value, dict):
            parts = [
                value.get("street", ""), value.get("city", ""),
                value.get("state", ""), value.get("zip", ""),
                value.get("country", ""),
            ]
            display = ", ".join(p for p in parts if p)
        else:
            display = str(value)
        type_code = 0
    else:
        display = str(value)
        type_code = 0

    display = _enforce_field_limit(name, display, notes_parts, item_name, oversized_fields)
    return {"name": name, "value": display, "type": type_code}


def _enforce_field_limit(
    name: str,
    value: str,
    notes_parts: list[str],
    item_name: str,
    oversized_fields: list[str],
) -> str:
    if len(value) <= _FIELD_VALUE_LIMIT:
        return value

    orig_len = len(value)
    candidate = f"{name}:\n{value}"
    prospective_notes = "\n".join(notes_parts + [candidate])
    if len(prospective_notes) <= _NOTES_LIMIT:
        notes_parts.append(candidate)
        oversized_fields.append(f"{item_name}: {name} ({orig_len} chars, moved to notes)")
        return "[full value moved to notes: exceeded Bitwarden's 5000-character field limit]"

    oversized_fields.append(f"{item_name}: {name} ({orig_len} chars, truncated)")
    return value[:_FIELD_VALUE_LIMIT] + f"…[truncated from {orig_len} characters: exceeded Bitwarden limits]"


def _field_display_name(section_title: str, field_title: str) -> str:
    if section_title and field_title:
        return f"{section_title}: {field_title}"
    return field_title or section_title or "field"


def _convert_password_history(history: list[dict]) -> list[dict]:
    result = []
    for entry in history:
        pw = entry.get("value") or entry.get("password") or ""
        ts = entry.get("time") or entry.get("lastUsedDate") or 0
        if not pw:
            continue
        if isinstance(ts, (int, float)):
            try:
                dt = datetime.datetime.fromtimestamp(int(ts), tz=datetime.timezone.utc)
                ts_str = dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            except (ValueError, OSError):
                ts_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        else:
            ts_str = str(ts)
        result.append({"lastUsedDate": ts_str, "password": pw})
    return result
