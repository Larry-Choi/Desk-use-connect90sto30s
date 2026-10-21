from __future__ import annotations

import ctypes
import logging
import sys
import traceback
from pathlib import Path

import webview

from desk.library import (
    LibraryError,
    _norm,
    add_app,
    counts,
    delete_app,
    discover,
    launch_target,
    library_path,
    load_or_create,
    merge_discovered,
    move_app,
    reorder,
    save,
    update_app,
)
from desk.media import MediaBridge
from desk.programs import collect_shortcuts, icon_data_url


def _log_path() -> Path:
    path = library_path().with_name("desk.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _configure_logging() -> None:
    handler = logging.FileHandler(_log_path(), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


class DeskApi:
    def __init__(self) -> None:
        self._window = None
        self._path = library_path()
        self._media = MediaBridge()

    def bind(self, window) -> None:
        self._window = window

    def get_library(self) -> dict:
        try:
            data = load_or_create(self._path)
        except LibraryError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "storagePath": str(self._path), **_view(data)}

    def rescan(self) -> dict:
        try:
            data = load_or_create(self._path)
            added = merge_discovered(data, discover())
            save(self._path, data)
        except LibraryError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "added": len(added), "storagePath": str(self._path), **_view(data)}

    def pick_target(self) -> str:
        window = self._window
        if window is None:
            return ""
        selected = window.create_file_dialog(
            webview.FileDialog.OPEN,
            directory=str(Path.home()),
            allow_multiple=False,
            file_types=(
                "실행 파일 (*.exe;*.lnk)",
                "모든 파일 (*.*)",
            ),
        )
        if not selected:
            return ""
        return selected[0]

    def save_app(self, payload: dict) -> dict:
        try:
            data = load_or_create(self._path)
            app_id = str((payload or {}).get("id") or "").strip()
            if app_id:
                update_app(data, app_id, payload)
            else:
                add_app(data, payload)
            save(self._path, data)
        except LibraryError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "storagePath": str(self._path), **_view(data)}

    def remove_app(self, app_id: str) -> dict:
        try:
            data = load_or_create(self._path)
            delete_app(data, str(app_id))
            save(self._path, data)
        except LibraryError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "storagePath": str(self._path), **_view(data)}

    def move(self, app_id: str, delta: int) -> dict:
        try:
            data = load_or_create(self._path)
            move_app(data, str(app_id), int(delta))
            save(self._path, data)
        except LibraryError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "storagePath": str(self._path), **_view(data)}

    def reorder_apps(self, ids: list[str]) -> dict:
        try:
            data = load_or_create(self._path)
            reorder(data, [str(item) for item in ids])
            save(self._path, data)
        except LibraryError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "storagePath": str(self._path), **_view(data)}

    def launch(self, app_id: str) -> dict:
        try:
            data = load_or_create(self._path)
            app = next((item for item in data["apps"] if item["id"] == str(app_id)), None)
            if app is None:
                raise LibraryError("앱을 찾지 못했습니다.")
            launch_target(app["target"])
        except LibraryError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "name": app["name"]}

    def launch_path(self, path: str) -> dict:
        try:
            launch_target(str(path or ""))
        except LibraryError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def list_programs(self) -> dict:
        try:
            data = load_or_create(self._path)
            known = {_norm(app["target"]) for app in data["apps"]}
            programs = []
            for item in collect_shortcuts():
                programs.append(
                    {
                        "name": item["name"],
                        "path": item["path"],
                        "icon": "",
                        "added": _norm(item["path"]) in known,
                    }
                )
        except LibraryError as exc:
            return {"ok": False, "error": str(exc), "programs": []}
        return {"ok": True, "programs": programs}

    def program_icon(self, path: str) -> dict:
        target = str(path or "")
        if not Path(target).is_file():
            return {"ok": True, "icon": ""}
        return {"ok": True, "icon": icon_data_url(target)}

    def media_state(self) -> dict:
        return self._media.snapshot()

    def set_needle(self, playing: bool) -> dict:
        want = playing is True or str(playing).lower() == "true"
        return self._media.set_playing(want)

    def skip_track(self, delta: int) -> dict:
        try:
            step = int(delta)
        except (TypeError, ValueError):
            step = 1
        return self._media.skip(step)


def _view(data: dict) -> dict:
    apps = []
    for app in data["apps"]:
        item = dict(app)
        item["icon"] = icon_data_url(app.get("target", ""))
        apps.append(item)
    return {
        "categories": data["categories"],
        "apps": apps,
        "counts": counts(data),
    }


def _check() -> None:
    data = load_or_create(library_path())
    summary = counts(data)
    print(library_path())
    print(
        "registered={registered} favorites={favorites} by={byCategory}".format(**summary)
    )
    for app in data["apps"]:
        print(f"{app['position']:02d} {app['categoryId']:5} {app['name']}  {app['target']}")


def main() -> None:
    _configure_logging()
    if "--check" in sys.argv:
        _check()
        return
    try:
        api = DeskApi()
        html = Path(__file__).resolve().parent / "ui" / "index.html"
        window = webview.create_window(
            "Desk",
            url=html.as_uri(),
            js_api=api,
            width=1380,
            height=860,
            x=48,
            y=32,
            min_size=(1080, 680),
            background_color="#070707",
            text_select=True,
        )
        api.bind(window)
        webview.start(gui="edgechromium")
    except Exception as exc:  # shown to the person starting the app
        logging.exception("Desk failed to start")
        ctypes.windll.user32.MessageBoxW(0, f"{exc}\n\n{traceback.format_exc()[-800:]}", "Desk", 0x10)


if __name__ == "__main__":
    main()
