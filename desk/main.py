from __future__ import annotations

import ctypes
import json
import logging
import sys
import threading
import time
import traceback
from ctypes import wintypes
from pathlib import Path

# Later, when Desk itself is finished: float above the desktop the way Steam
# does, and add a recycle bin, because it would stand in for the desktop.
# Do not build that until the rest is complete.

import webview

from desk.library import (
    LibraryError,
    _norm,
    add_app,
    add_track,
    counts,
    delete_app,
    discover,
    launch_target,
    library_path,
    load_or_create,
    merge_discovered,
    move_app,
    remove_track,
    reorder,
    replace_categories,
    save,
    update_app,
)
from desk.mail import MailError, fetch_inbox, mail_has_fresh, unseen_headers
from desk.media import MediaBridge
from desk.pane import PagePane
from desk.phone import PhoneLink
from desk.programs import collect_shortcuts, icon_data_url, list_running_apps
from desk.secrets import delete_mail_password, read_mail_password, write_mail_password
from desk.usage import UsageMonitor
from desk.settings import (
    load_settings,
    normalize_mail_address,
    normalize_page_url,
    prepare_usage,
    save_settings,
    settings_path,
)


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
        self._settings = settings_path()
        self._media = MediaBridge()
        self._pane = PagePane()
        self._phone = PhoneLink(self._notify)
        self._usage = UsageMonitor()
        self._usage_script = ""
        self._links_started = False

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

    def pick_folder(self) -> str:
        window = self._window
        if window is None:
            return ""
        selected = window.create_file_dialog(
            webview.FileDialog.FOLDER,
            directory=str(Path.home()),
            allow_multiple=False,
        )
        if not selected:
            return ""
        return selected[0]

    def launch_category(self, category_id: str) -> dict:
        try:
            data = load_or_create(self._path)
            category = str(category_id or "")
            apps = [
                app
                for app in data["apps"]
                if app.get("categoryId") == category and app.get("favorite", True)
            ]
            if not apps:
                raise LibraryError("이 분류에 켤 항목이 없습니다.")
        except LibraryError as exc:
            return {"ok": False, "error": str(exc)}

        def run() -> None:
            for index, app in enumerate(apps):
                if index:
                    time.sleep(0.14)
                try:
                    launch_target(app["target"])
                except LibraryError:
                    continue

        threading.Thread(target=run, name="desk-group", daemon=True).start()
        return {"ok": True, "count": len(apps)}

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

    def save_categories(self, payload) -> dict:
        items = payload.get("categories") if isinstance(payload, dict) else payload
        try:
            data = load_or_create(self._path)
            replace_categories(data, items)
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

    def list_running(self) -> dict:
        try:
            data = load_or_create(self._path)
            known = {_norm(app["target"]) for app in data["apps"]}
            programs = []
            for item in list_running_apps():
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

    def add_track(self, raw: str) -> dict:
        try:
            data = load_or_create(self._path)
            track = add_track(data, str(raw or ""))
            save(self._path, data)
        except LibraryError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "track": track, "storagePath": str(self._path), **_view(data)}

    def remove_track(self, track_id: str) -> dict:
        try:
            data = load_or_create(self._path)
            remove_track(data, str(track_id))
            save(self._path, data)
        except LibraryError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "storagePath": str(self._path), **_view(data)}

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

    def mail_state(self) -> dict:
        settings = load_settings(self._settings)
        address = settings["mailAddress"]
        connected = bool(address and read_mail_password())
        return {"ok": True, "address": address, "connected": connected}

    def save_mail(self, address: str, password: str) -> dict:
        try:
            cleaned = normalize_mail_address(address)
        except LibraryError as exc:
            return {"ok": False, "error": str(exc)}
        secret = str(password or "")
        if secret:
            if not write_mail_password(cleaned, secret):
                return {"ok": False, "error": "비밀번호를 이 PC에 저장하지 못했습니다."}
        elif not read_mail_password():
            return {"ok": False, "error": "애플리케이션 비밀번호를 입력하세요."}
        settings = load_settings(self._settings)
        settings["mailAddress"] = cleaned
        save_settings(self._settings, settings)
        return self.mail_inbox()

    def forget_mail(self) -> dict:
        delete_mail_password()
        settings = load_settings(self._settings)
        settings["mailAddress"] = ""
        save_settings(self._settings, settings)
        return {"ok": True, "connected": False, "address": "", "dot": False, "unseen": 0, "messages": []}

    def mail_inbox(self) -> dict:
        settings = load_settings(self._settings)
        address = settings["mailAddress"]
        password = read_mail_password()
        if not address or not password:
            return {
                "ok": True,
                "connected": False,
                "address": address,
                "dot": False,
                "unseen": 0,
                "messages": [],
            }
        try:
            inbox = fetch_inbox(address, password)
        except MailError as exc:
            return {"ok": False, "connected": True, "address": address, "error": str(exc)}
        inbox["dot"] = mail_has_fresh(inbox.get("unseenUids"), settings.get("mailAcked"))
        return {"ok": True, "connected": True, "address": address, **inbox}

    def mail_open(self) -> dict:
        inbox = self.mail_inbox()
        if not inbox.get("ok") or not inbox.get("connected"):
            return inbox
        settings = load_settings(self._settings)
        acked = list(settings.get("mailAcked") or [])
        for uid in inbox.get("unseenUids") or []:
            if uid not in acked:
                acked.append(uid)
        settings["mailAcked"] = acked
        save_settings(self._settings, settings)
        inbox["dot"] = False
        return inbox

    def page_state(self) -> dict:
        settings = load_settings(self._settings)
        return {
            "ok": True,
            "url": settings["pageUrl"],
            "openWithDesk": settings["openPageWithDesk"],
        }

    def save_page(self, url: str, open_with_desk: bool) -> dict:
        try:
            cleaned = normalize_page_url(url)
        except LibraryError as exc:
            return {"ok": False, "error": str(exc)}
        settings = load_settings(self._settings)
        settings["pageUrl"] = cleaned
        settings["openPageWithDesk"] = open_with_desk is True or str(open_with_desk).lower() == "true"
        save_settings(self._settings, settings)
        return {"ok": True, "url": cleaned, "openWithDesk": settings["openPageWithDesk"]}

    def sync_page(self, x: float, y: float, width: float, height: float, scale: float) -> dict:
        settings = load_settings(self._settings)
        url = settings["pageUrl"]
        if not url:
            return {"ok": False, "error": "주소가 없습니다."}
        form = getattr(self._window, "native", None)
        try:
            error = self._pane.sync(form, url, float(x), float(y), float(width), float(height), float(scale or 1))
        except Exception:
            logging.exception("page pane")
            return {"ok": False, "error": "페이지 칸을 열지 못했습니다."}
        if error:
            return {"ok": False, "error": error}
        return {"ok": True, "url": url}

    def hide_page(self) -> dict:
        form = getattr(self._window, "native", None)
        self._pane.hide(form)
        return {"ok": True}

    def usage_state(self) -> dict:
        return self._usage.snapshot()

    def usage_settings(self) -> dict:
        return self._usage.catalog()

    def save_usage(self, payload) -> dict:
        if not isinstance(payload, dict):
            payload = {}
        settings = load_settings(self._settings)
        settings.update(prepare_usage(payload))
        save_settings(self._settings, settings)
        self._usage.remember(settings)
        self._usage.resample()
        state = self._usage.snapshot()
        self._push_usage(state)
        return state

    def phone_state(self) -> dict:
        return self._phone.state()

    def phone_pair(self, device_id: str) -> dict:
        return self._phone.request_pair(str(device_id or ""))

    def phone_accept(self, device_id: str) -> dict:
        return self._phone.accept(str(device_id or ""))

    def phone_reject(self, device_id: str) -> dict:
        return self._phone.reject(str(device_id or ""))

    def start_links(self) -> None:
        if self._links_started:
            return
        self._links_started = True
        self._phone.start()
        self._usage.start(self._push_usage)
        threading.Thread(target=self._watch_mail, name="desk-mail", daemon=True).start()

    def _push_usage(self, payload: dict) -> None:
        window = self._window
        if window is None:
            return
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if encoded == self._usage_script:
            return
        script = "paintUsage(" + encoded + ")"
        try:
            window.evaluate_js(script)
        except Exception:
            self._usage_script = ""
            logging.exception("usage")
        else:
            self._usage_script = encoded

    def _watch_mail(self) -> None:
        known: set[str] | None = None
        while True:
            time.sleep(8 if known is None else 45)
            settings = load_settings(self._settings)
            address = settings["mailAddress"]
            password = read_mail_password()
            if not address or not password:
                known = None
                continue
            try:
                items = unseen_headers(address, password)
            except MailError:
                continue
            uids = {item["uid"] for item in items}
            if known is None:
                known = uids
                continue
            fresh = [item for item in items if item["uid"] not in known]
            known = uids
            for item in fresh[:3]:
                self._notify(
                    {
                        "kind": "mail",
                        "id": f"mail-{item['uid']}",
                        "title": "새 메일",
                        "body": f"{item['from']} · {item['subject']}" if item["from"] else item["subject"],
                    }
                )

    def _notify(self, payload: dict) -> None:
        try:
            from desk.alert import present

            present(payload)
        except Exception:
            logging.exception("alert")
        window = self._window
        if window is None:
            return
        script = "pushNotice(" + json.dumps(payload, ensure_ascii=False) + ")"
        try:
            window.evaluate_js(script)
        except Exception:
            logging.exception("notice")


def _view(data: dict) -> dict:
    apps = []
    for app in data["apps"]:
        item = dict(app)
        item["icon"] = icon_data_url(app.get("target", ""))
        apps.append(item)
    return {
        "categories": data["categories"],
        "apps": apps,
        "tracks": list(data.get("tracks") or []),
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


def _claim_taskbar() -> None:
    # pythonw is shared with Spyder. Without our own id, Windows shows Spyder's icon.
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Larry.Desk")


def _open_saved_page(api: DeskApi) -> None:
    def run() -> None:
        time.sleep(0.6)
        api.start_links()
        settings = load_settings(api._settings)
        if settings["openPageWithDesk"] and settings["pageUrl"] and api._window is not None:
            try:
                api._window.evaluate_js("enterPage()")
            except Exception:
                logging.exception("open saved page")

    threading.Thread(target=run, daemon=True).start()


def _apply_window_icon() -> None:
    icon_path = Path(__file__).resolve().parent / "ui" / "desk.ico"
    if not icon_path.is_file():
        return
    user32 = ctypes.windll.user32
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.LoadImageW.argtypes = [
        wintypes.HINSTANCE,
        wintypes.LPCWSTR,
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = wintypes.LPARAM
    hwnd = user32.FindWindowW(None, "Desk")
    if not hwnd:
        return
    hicon = user32.LoadImageW(None, str(icon_path), 1, 0, 0, 0x10)
    if not hicon:
        return
    user32.SendMessageW(hwnd, 0x80, 1, hicon)
    user32.SendMessageW(hwnd, 0x80, 0, hicon)


def main() -> None:
    _configure_logging()
    _claim_taskbar()
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
        window.events.shown += _apply_window_icon
        window.events.shown += lambda: _open_saved_page(api)
        api.bind(window)
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
        webview.start(
            gui="edgechromium",
            private_mode=False,
            storage_path=str(library_path().parent / "webview"),
        )
    except Exception as exc:  # shown to the person starting the app
        logging.exception("Desk failed to start")
        ctypes.windll.user32.MessageBoxW(0, f"{exc}\n\n{traceback.format_exc()[-800:]}", "Desk", 0x10)


if __name__ == "__main__":
    main()
