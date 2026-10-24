"""Start-menu shortcuts the launcher can add and run."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import os
import threading
from ctypes import wintypes
from pathlib import Path

_SKIP = ("uninstall", "설치 제거", "제거", "readme")
_ICON_LOCK = threading.Lock()
_ICON_CACHE: dict[str, str] = {}


def include_name(name: str) -> bool:
    lowered = name.casefold()
    if lowered in {"help", "도움말"}:
        return False
    return not any(part in lowered for part in _SKIP)


def start_menu_roots() -> list[Path]:
    roots = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    program_data = os.environ.get("ProgramData")
    if program_data:
        roots.append(Path(program_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return [root for root in roots if root.is_dir()]


def collect_shortcuts(roots: list[Path] | None = None) -> list[dict]:
    found = []
    for root in roots if roots is not None else start_menu_roots():
        if not root.is_dir():
            continue
        for path in root.rglob("*.lnk"):
            if not path.is_file():
                continue
            name = path.stem.strip()
            if not name or not include_name(name):
                continue
            found.append({"name": name, "path": str(path), "group": path.parent.name})
    counts: dict[str, int] = {}
    for item in found:
        counts[item["name"]] = counts.get(item["name"], 0) + 1
    for item in found:
        if counts[item["name"]] > 1 and item["group"] and item["group"] != "Programs":
            item["name"] = f"{item['name']} ({item['group']})"
        item.pop("group", None)
    found.sort(key=lambda item: item["name"].casefold())
    return found


_SKIP_PROCESSES = {
    "applicationframehost.exe",
    "textinputhost.exe",
    "shellexperiencehost.exe",
    "searchhost.exe",
    "startmenuexperiencehost.exe",
    "systemsettings.exe",
    "lockapp.exe",
    "python.exe",
    "pythonw.exe",
}


def list_running_apps() -> list[dict]:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    found: list[tuple[str, str]] = []

    def full_path(pid: int) -> str:
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return ""
        try:
            buffer = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return buffer.value
            return ""
        finally:
            kernel32.CloseHandle(handle)

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        path = full_path(int(pid.value))
        if path:
            found.append((path, title.value))
        return True

    user32.EnumWindows(visit, 0)
    unique: dict[str, dict] = {}
    for path, title in found:
        file = Path(path)
        if file.name.lower() in _SKIP_PROCESSES or not file.is_file():
            continue
        key = os.path.normcase(path)
        if key in unique:
            continue
        unique[key] = {"name": file.stem, "path": path, "title": title}
    rows = list(unique.values())
    rows.sort(key=lambda item: item["name"].casefold())
    return rows


def icon_data_url(path: str) -> str:
    if not path:
        return ""
    key = hashlib.sha1(os.path.normcase(path).encode("utf-8")).hexdigest()
    with _ICON_LOCK:
        cached = _ICON_CACHE.get(key)
        if cached is not None:
            return cached
    folder = Path(os.environ.get("APPDATA", Path.home())) / "Desk" / "icons"
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / f"{key}.png"
    if file.is_file() and file.stat().st_size > 0:
        encoded = base64.b64encode(file.read_bytes()).decode("ascii")
        url = f"data:image/png;base64,{encoded}"
        with _ICON_LOCK:
            _ICON_CACHE[key] = url
        return url
    raw = _extract_png(path)
    if not raw:
        with _ICON_LOCK:
            _ICON_CACHE[key] = ""
        return ""
    file.write_bytes(raw)
    url = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    with _ICON_LOCK:
        _ICON_CACHE[key] = url
    return url


def _extract_png(path: str) -> bytes:
    try:
        import clr

        clr.AddReference("System.Drawing")
        from System.Drawing import Icon
        from System.Drawing.Imaging import ImageFormat
        from System.IO import MemoryStream

        icon = Icon.ExtractAssociatedIcon(path)
        if icon is None:
            return b""
        bitmap = icon.ToBitmap()
        stream = MemoryStream()
        try:
            bitmap.Save(stream, ImageFormat.Png)
            return bytes(bytearray(stream.ToArray()))
        finally:
            stream.Dispose()
            bitmap.Dispose()
            icon.Dispose()
    except Exception:
        return b""
