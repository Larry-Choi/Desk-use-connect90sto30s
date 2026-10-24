"""Saved launcher library.

Counts stay separate on purpose:
- registered: every saved app
- favorites: apps shown on the desk
- by_category: registered apps in each category

"전체" is a filter in the window, not a category. Category titles are the
ones that were readable on the reference screen.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
import urllib.parse
from pathlib import Path

CATEGORY_AI = "ai"
CATEGORY_VIDEO = "video"
CATEGORY_TOOLS = "tools"

DEFAULT_CATEGORIES = (
    {"id": CATEGORY_AI, "title": "AI 작업실", "position": 0},
    {"id": CATEGORY_VIDEO, "title": "영상 편집실", "position": 1},
    {"id": CATEGORY_TOOLS, "title": "도구 서랍", "position": 2},
)


class LibraryError(Exception):
    pass


def library_path() -> Path:
    root = Path(os.environ.get("APPDATA", Path.home()))
    return root / "Desk" / "library.json"


def empty_library() -> dict:
    return {
        "version": 1,
        "categories": [dict(item) for item in DEFAULT_CATEGORIES],
        "apps": [],
        "tracks": [],
    }


def counts(data: dict) -> dict:
    categories = data.get("categories") or []
    known = {item["id"] for item in categories}
    by_category = {item["id"]: 0 for item in categories}
    favorites_by_category = {item["id"]: 0 for item in categories}
    favorites = 0
    for app in data.get("apps") or []:
        if app.get("favorite", True):
            favorites += 1
        category_id = app.get("categoryId")
        if category_id in known:
            by_category[category_id] += 1
            if app.get("favorite", True):
                favorites_by_category[category_id] += 1
    return {
        "registered": len(data.get("apps") or []),
        "favorites": favorites,
        "byCategory": by_category,
        "favoritesByCategory": favorites_by_category,
    }


def ensure_shape(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise LibraryError("저장 파일 형식이 올바르지 않습니다.")
    data = empty_library()
    incoming = raw.get("categories")
    if isinstance(incoming, list) and incoming:
        cleaned = []
        for item in incoming:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            category_id = str(item.get("categoryId") or item.get("id") or "").strip()
            if not category_id or not title or title == "전체":
                continue
            cleaned.append(
                {
                    "id": category_id,
                    "title": title,
                    "position": int(item.get("position", len(cleaned))),
                }
            )
        if cleaned:
            data["categories"] = cleaned
    apps = []
    known = {item["id"] for item in data["categories"]}
    for item in raw.get("apps") or []:
        if not isinstance(item, dict):
            continue
        target = str(item.get("target", "")).strip()
        name = str(item.get("name", "")).strip()
        if not target or not name:
            continue
        category_id = str(item.get("categoryId", "")).strip()
        if category_id not in known:
            category_id = CATEGORY_TOOLS
        apps.append(
            {
                "id": str(item.get("id") or uuid.uuid4().hex[:12]),
                "name": name[:40],
                "categoryId": category_id,
                "target": target,
                "favorite": bool(item.get("favorite", True)),
                "position": int(item.get("position", len(apps))),
            }
        )
    data["apps"] = apps
    data["tracks"] = _clean_tracks(raw.get("tracks"))
    return normalize(data)


def normalize(data: dict) -> dict:
    categories = sorted(data["categories"], key=lambda item: item.get("position", 0))
    for index, item in enumerate(categories):
        item["position"] = index
    data["categories"] = categories
    apps = sorted(data["apps"], key=lambda item: (item.get("position", 0), item["name"]))
    for index, item in enumerate(apps):
        item["position"] = index
    data["apps"] = apps
    return data


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(normalize(data), ensure_ascii=False, indent=2)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def load_or_create(path: Path, catalog: list[dict] | None = None) -> dict:
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise LibraryError(f"저장 파일을 읽지 못했습니다. {path}") from exc
        return ensure_shape(raw)
    data = empty_library()
    merge_discovered(data, discover(catalog))
    save(path, data)
    return data


def _norm(target: str) -> str:
    return os.path.normcase(os.path.normpath(target))


def _new_id(data: dict) -> str:
    used = {app["id"] for app in data["apps"]}
    while True:
        app_id = uuid.uuid4().hex[:12]
        if app_id not in used:
            return app_id


def merge_discovered(data: dict, found: list[dict]) -> list[dict]:
    known = {_norm(app["target"]) for app in data["apps"]}
    categories = {item["id"] for item in data["categories"]}
    added = []
    position = len(data["apps"])
    for item in found:
        target = item["target"]
        key = _norm(target)
        if key in known:
            continue
        category_id = item.get("categoryId", CATEGORY_TOOLS)
        if category_id not in categories:
            category_id = CATEGORY_TOOLS
        app = {
            "id": _new_id(data),
            "name": item["name"][:40],
            "categoryId": category_id,
            "target": str(Path(target)),
            "favorite": True,
            "position": position,
        }
        data["apps"].append(app)
        known.add(key)
        added.append(app)
        position += 1
    normalize(data)
    return added


def usable_path(path: Path) -> bool:
    try:
        return path.is_file() or path.exists()
    except OSError:
        return False


def discover(catalog: list[dict] | None = None) -> list[dict]:
    found = []
    seen = set()
    for entry in catalog if catalog is not None else default_catalog():
        target = resolve_entry(entry)
        if not target:
            continue
        key = _norm(target)
        if key in seen:
            continue
        seen.add(key)
        found.append(
            {
                "name": entry["name"],
                "categoryId": entry.get("categoryId", CATEGORY_TOOLS),
                "target": target,
            }
        )
    return found


def resolve_entry(entry: dict) -> str | None:
    for raw in entry.get("paths") or []:
        path = Path(raw)
        if usable_path(path):
            return str(path)
    for command in entry.get("commands") or []:
        resolved = shutil.which(command)
        if not resolved:
            continue
        path = Path(resolved)
        if usable_path(path):
            return str(path)
    return None


def default_catalog() -> list[dict]:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    pf86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    programs = local / "Programs"
    return [
        {
            "name": "Cursor",
            "categoryId": CATEGORY_AI,
            "paths": [programs / "cursor" / "Cursor.exe"],
        },
        {
            "name": "LM Studio",
            "categoryId": CATEGORY_AI,
            "paths": [
                programs / "LM Studio" / "LM Studio.exe",
                local / "LM Studio" / "LM Studio.exe",
            ],
        },
        {
            "name": "Ollama",
            "categoryId": CATEGORY_AI,
            "paths": [
                programs / "Ollama" / "Ollama.exe",
                programs / "Ollama" / "ollama.exe",
            ],
        },
        {
            "name": "Obsidian",
            "categoryId": CATEGORY_TOOLS,
            "paths": [
                local / "Obsidian" / "Obsidian.exe",
                programs / "Obsidian" / "Obsidian.exe",
            ],
        },
        {
            "name": "Visual Studio Code",
            "categoryId": CATEGORY_TOOLS,
            "paths": [programs / "Microsoft VS Code" / "Code.exe"],
        },
        {
            "name": "Microsoft Edge",
            "categoryId": CATEGORY_TOOLS,
            "paths": [pf86 / "Microsoft" / "Edge" / "Application" / "msedge.exe"],
        },
        {
            "name": "Windows Terminal",
            "categoryId": CATEGORY_TOOLS,
            "commands": ["wt.exe"],
        },
        {
            "name": "파일 탐색기",
            "categoryId": CATEGORY_TOOLS,
            "commands": ["explorer.exe"],
        },
        {
            "name": "메모장",
            "categoryId": CATEGORY_TOOLS,
            "commands": ["notepad.exe"],
        },
        {
            "name": "그림판",
            "categoryId": CATEGORY_TOOLS,
            "commands": ["mspaint.exe"],
        },
        {
            "name": "계산기",
            "categoryId": CATEGORY_TOOLS,
            "commands": ["calc.exe"],
        },
    ]


def validate_fields(payload: dict, categories: list[dict]) -> dict:
    if not isinstance(payload, dict):
        raise LibraryError("입력 형식이 올바르지 않습니다.")
    name = str(payload.get("name", "")).strip()
    if not name or len(name) > 40:
        raise LibraryError("이름은 1자에서 40자까지 입력해 주세요.")
    category_id = str(payload.get("categoryId", "")).strip()
    known = {item["id"] for item in categories}
    if category_id not in known:
        raise LibraryError("분류를 선택해 주세요.")
    target = str(payload.get("target", "")).strip().strip('"')
    lowered = target.lower()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        raise LibraryError("웹 주소는 앱 목록에 넣지 않습니다. 유튜브는 바늘 아래 곡 칸에 넣으세요.")
    if not target:
        raise LibraryError("파일이나 폴더를 선택해 주세요.")
    path = Path(target)
    if not usable_path(path):
        raise LibraryError("파일이나 폴더를 찾지 못했습니다.")
    return {
        "name": name,
        "categoryId": category_id,
        "target": str(path),
        "favorite": bool(payload.get("favorite", True)),
    }


def add_app(data: dict, payload: dict) -> dict:
    fields = validate_fields(payload, data["categories"])
    if any(_norm(app["target"]) == _norm(fields["target"]) for app in data["apps"]):
        raise LibraryError("이미 같은 파일이 등록되어 있습니다.")
    app = {
        "id": _new_id(data),
        "position": len(data["apps"]),
        **fields,
    }
    data["apps"].append(app)
    normalize(data)
    return app


def update_app(data: dict, app_id: str, payload: dict) -> dict:
    fields = validate_fields(payload, data["categories"])
    for app in data["apps"]:
        if app["id"] != app_id:
            continue
        if any(
            other["id"] != app_id and _norm(other["target"]) == _norm(fields["target"])
            for other in data["apps"]
        ):
            raise LibraryError("이미 같은 파일이 등록되어 있습니다.")
        app.update(fields)
        normalize(data)
        return app
    raise LibraryError("수정할 앱을 찾지 못했습니다.")


def delete_app(data: dict, app_id: str) -> None:
    remaining = [app for app in data["apps"] if app["id"] != app_id]
    if len(remaining) == len(data["apps"]):
        raise LibraryError("삭제할 앱을 찾지 못했습니다.")
    data["apps"] = remaining
    normalize(data)


def replace_categories(data: dict, items: list) -> None:
    if not isinstance(items, list) or not items:
        raise LibraryError("분류를 하나 이상 남겨 주세요.")
    known = {item["id"] for item in data["categories"]}
    cleaned = []
    seen_ids = set()
    seen_titles = set()
    for raw in items:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title or title == "전체" or len(title) > 24:
            raise LibraryError("분류 이름은 1자에서 24자까지입니다. 전체는 분류 이름이 아닙니다.")
        folded = title.casefold()
        if folded in seen_titles:
            raise LibraryError("같은 이름의 분류가 있습니다.")
        seen_titles.add(folded)
        item_id = str(raw.get("id") or "").strip()
        if item_id in {"", "all"} or item_id not in known or item_id in seen_ids:
            item_id = "cat-" + uuid.uuid4().hex[:8]
            while item_id in known or item_id in seen_ids:
                item_id = "cat-" + uuid.uuid4().hex[:8]
        seen_ids.add(item_id)
        cleaned.append({"id": item_id, "title": title, "position": len(cleaned)})
    if not cleaned:
        raise LibraryError("분류를 하나 이상 남겨 주세요.")
    keep = {item["id"] for item in cleaned}
    fallback = cleaned[0]["id"]
    for app in data["apps"]:
        if app.get("categoryId") not in keep:
            app["categoryId"] = fallback
    data["categories"] = cleaned
    normalize(data)


def move_app(data: dict, app_id: str, delta: int) -> None:
    apps = sorted(data["apps"], key=lambda item: item["position"])
    index = next((i for i, app in enumerate(apps) if app["id"] == app_id), None)
    if index is None:
        raise LibraryError("앱을 찾지 못했습니다.")
    target = index + int(delta)
    if target < 0 or target >= len(apps):
        return
    apps[index], apps[target] = apps[target], apps[index]
    data["apps"] = apps
    normalize(data)


def reorder(data: dict, ids: list[str]) -> None:
    current = [app["id"] for app in sorted(data["apps"], key=lambda item: item["position"])]
    if sorted(ids) != sorted(current) or len(ids) != len(current):
        raise LibraryError("순서를 저장하지 못했습니다.")
    by_id = {app["id"]: app for app in data["apps"]}
    data["apps"] = [by_id[app_id] for app_id in ids]
    normalize(data)


def launch_target(target: str) -> None:
    path = Path(target)
    if not path.exists():
        raise LibraryError("경로가 없습니다. 다시 지정해 주세요.")
    try:
        os.startfile(str(path))  # noqa: S606 - user-selected local file or folder
    except OSError as exc:
        raise LibraryError(f"실행하지 못했습니다. {exc.strerror or exc}") from exc


_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def youtube_video_id(value: str) -> str:
    text = str(value or "").strip()
    if _VIDEO_ID.fullmatch(text):
        return text
    try:
        parsed = urllib.parse.urlparse(text)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if host.endswith("youtu.be"):
        part = parsed.path.strip("/").split("/")[0]
        return part if _VIDEO_ID.fullmatch(part) else ""
    if "youtube.com" not in host and not host.endswith("youtube-nocookie.com"):
        return ""
    query = urllib.parse.parse_qs(parsed.query)
    video = (query.get("v") or [""])[0]
    if _VIDEO_ID.fullmatch(video):
        return video
    match = re.search(r"/(?:embed|shorts|live)/([A-Za-z0-9_-]{11})", parsed.path)
    return match.group(1) if match else ""


def _clean_tracks(raw) -> list[dict]:
    tracks = []
    seen = set()
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        video_id = youtube_video_id(str(item.get("videoId") or ""))
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        title = str(item.get("title") or "").strip() or f"YouTube {video_id}"
        tracks.append(
            {
                "id": str(item.get("id") or uuid.uuid4().hex[:12]),
                "title": title[:80],
                "videoId": video_id,
            }
        )
    return tracks


def add_track(data: dict, raw: str) -> dict:
    video_id = youtube_video_id(raw)
    if not video_id:
        raise LibraryError("유튜브 주소가 아닙니다.")
    for track in data.setdefault("tracks", []):
        if track["videoId"] == video_id:
            return track
    track = {"id": uuid.uuid4().hex[:12], "title": f"YouTube {video_id}", "videoId": video_id}
    data["tracks"].append(track)
    return track


def remove_track(data: dict, track_id: str) -> None:
    remaining = [track for track in data.get("tracks") or [] if track["id"] != track_id]
    if len(remaining) == len(data.get("tracks") or []):
        raise LibraryError("뺄 곡을 찾지 못했습니다.")
    data["tracks"] = remaining
