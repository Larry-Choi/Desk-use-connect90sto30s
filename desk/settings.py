"""Small Desk preferences kept out of the app library."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from urllib.parse import urlparse

from desk.library import LibraryError, library_path
from desk.meters import DEFAULT_METERS, METER_IDS


def settings_path() -> Path:
    return library_path().with_name("desk.json")


def empty_settings() -> dict:
    return {
        "mailAddress": "",
        "pageUrl": "",
        "openPageWithDesk": False,
        "usageAlign": "start",
        "usageOrder": [],
        "usageHidden": [],
        "usageCustom": [],
        "usageMeters": list(DEFAULT_METERS),
        "usageMetersConfigured": False,
        "mailAcked": [],
    }


def load_settings(path: Path | None = None) -> dict:
    target = path or settings_path()
    data = empty_settings()
    if not target.is_file():
        return data
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return data
    if not isinstance(raw, dict):
        return data
    data["mailAddress"] = str(raw.get("mailAddress") or "").strip()
    data["pageUrl"] = str(raw.get("pageUrl") or "").strip()
    data["openPageWithDesk"] = bool(raw.get("openPageWithDesk"))
    data["usageAlign"] = "end" if raw.get("usageAlign") == "end" else "start"
    data["usageOrder"] = _names(raw.get("usageOrder"))
    data["usageHidden"] = _names(raw.get("usageHidden"))
    data["usageCustom"] = _customs(raw.get("usageCustom"))
    data["usageMetersConfigured"] = bool(raw.get("usageMetersConfigured"))
    if data["usageMetersConfigured"]:
        data["usageMeters"] = _meter_ids(raw.get("usageMeters"))
    data["mailAcked"] = _id_list(raw.get("mailAcked"))
    return data


def save_settings(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mailAddress": str(data.get("mailAddress") or "").strip(),
        "pageUrl": str(data.get("pageUrl") or "").strip(),
        "openPageWithDesk": bool(data.get("openPageWithDesk")),
        "usageAlign": "end" if data.get("usageAlign") == "end" else "start",
        "usageOrder": _names(data.get("usageOrder")),
        "usageHidden": _names(data.get("usageHidden")),
        "usageCustom": _customs(data.get("usageCustom")),
        "usageMeters": _meter_ids(data.get("usageMeters")),
        "usageMetersConfigured": bool(data.get("usageMetersConfigured")),
        "mailAcked": _id_list(data.get("mailAcked")),
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _id_list(value, limit: int = 400) -> list[str]:
    if not isinstance(value, list):
        return []
    chosen = []
    for item in value:
        text = str(item or "").strip()
        if text.isdigit() and text not in chosen:
            chosen.append(text[:24])
    return chosen[-limit:]


def _meter_ids(value) -> list[str]:
    if not isinstance(value, list):
        return []
    chosen = []
    allowed = set(METER_IDS)
    for item in value:
        text = str(item or "").strip()
        if text in allowed and text not in chosen:
            chosen.append(text)
    return chosen


def _names(value) -> list[str]:
    if not isinstance(value, list):
        return []
    names = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in names:
            names.append(text[:40])
    return names[:24]


def _percent(value):
    if value in ("", None):
        return None
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return None


def _customs(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    items = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()[:24]
        session = _percent(raw.get("session"))
        week = _percent(raw.get("week"))
        if not name or (session is None and week is None):
            continue
        item_id = str(raw.get("id") or "").strip()
        if not item_id.startswith("custom-"):
            continue
        items.append(
            {
                "id": item_id[:48],
                "name": name,
                "session": session,
                "week": week,
                "sessionLeft": str(raw.get("sessionLeft") or "").strip()[:16],
                "weekLeft": str(raw.get("weekLeft") or "").strip()[:16],
            }
        )
    return items[:12]


def prepare_usage(payload: dict) -> dict:
    custom = []
    for raw in payload.get("custom") or []:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("id") or "").strip()
        if not item_id.startswith("custom-"):
            item_id = "custom-" + uuid.uuid4().hex[:8]
        custom.append({**raw, "id": item_id})
    meters = []
    for item in payload.get("meters") or []:
        text = str(item or "").strip()
        if text in set(METER_IDS) and text not in meters:
            meters.append(text)
    return {
        "usageAlign": payload.get("align"),
        "usageOrder": payload.get("order"),
        "usageHidden": payload.get("hidden"),
        "usageCustom": custom,
        "usageMeters": meters,
        "usageMetersConfigured": True,
    }


def normalize_mail_address(value: str) -> str:
    address = str(value or "").strip().lower()
    if "@" not in address:
        raise LibraryError("네이버 메일 주소를 입력하세요.")
    local, _, domain = address.partition("@")
    if not local or domain not in {"naver.com", "hanmail.net"}:
        raise LibraryError("네이버 메일 주소만 연결합니다.")
    return address


def normalize_page_url(value: str) -> str:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise LibraryError("http 또는 https 주소만 열 수 있습니다.")
    return raw
