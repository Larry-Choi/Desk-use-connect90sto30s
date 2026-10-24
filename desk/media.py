"""YouTube-style playback through the Windows media session.

The needle follows the browser session that is already playing. Pausing in the
browser or with a media key drops the needle. Putting the needle on the record
sends play to that same session, which resumes it. A phone, or a tab that has
been closed, does not appear here.
"""

from __future__ import annotations

import asyncio
import base64
import ctypes
import logging
import queue
import threading
import time
from ctypes import wintypes
from pathlib import Path

PLAYING = 4
PAUSED = 5
STOPPED = 3

_BROWSERS = (
    ("chrome", "Chrome", "chrome.exe"),
    ("msedge", "Edge", "msedge.exe"),
    ("microsoftedge", "Edge", "msedge.exe"),
    ("firefox", "Firefox", "firefox.exe"),
    ("opera", "Opera", "opera.exe"),
    ("brave", "Brave", "brave.exe"),
)

_BROWSER_EXES = {exe for _, _, exe in _BROWSERS}


def classify(aumid: str, titles: list[str], youtube_window: bool = False) -> dict | None:
    raw = aumid or ""
    lowered = raw.lower()
    title_hit = any("youtube" in (title or "").lower() for title in titles)
    app_name = ""
    browser = False
    for token, name, _exe in _BROWSERS:
        if token in lowered:
            browser = True
            app_name = name
            break
    youtube = "youtube" in lowered or title_hit or (browser and youtube_window)
    if not youtube and not browser:
        return None
    return {
        "youtube": youtube,
        "browser": browser,
        "source": "YouTube" if youtube else app_name or "브라우저",
    }


def choose(candidates: list[dict]) -> dict | None:
    ranked = []
    for item in candidates:
        kind = classify(
            str(item.get("aumid") or ""),
            list(item.get("titles") or []),
            bool(item.get("youtube_window")),
        )
        if kind is None:
            continue
        ranked.append({**item, **kind, "status": int(item.get("status", 0))})
    if not ranked:
        return None
    ranked.sort(
        key=lambda item: (
            1 if item["youtube"] else 0,
            1 if item["status"] == PLAYING else 0,
            1 if item["status"] == PAUSED else 0,
        ),
        reverse=True,
    )
    return ranked[0]


def empty_state() -> dict:
    return {
        "ok": True,
        "ready": False,
        "playing": False,
        "title": "",
        "artist": "",
        "source": "",
        "canPlay": False,
        "canPause": False,
        "canNext": False,
        "canPrev": False,
        "thumbnail": "",
        "error": "",
    }


async def artwork_url(props) -> str:
    thumb = getattr(props, "thumbnail", None)
    if thumb is None:
        return ""
    stream = await thumb.open_read_async()
    size = int(getattr(stream, "size", 0) or 0)
    if size <= 32 or size > 1_500_000:
        return ""
    from winrt.windows.storage.streams import DataReader

    reader = DataReader(stream)
    loaded = int(await reader.load_async(size) or 0)
    if loaded <= 0:
        reader.close()
        return ""
    raw = bytes(reader.read_buffer(loaded))
    reader.close()
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        kind = "png"
    elif raw.startswith(b"\xff\xd8\xff"):
        kind = "jpeg"
    elif raw.startswith(b"RIFF") and b"WEBP" in raw[:16]:
        kind = "webp"
    else:
        return ""
    return "data:image/" + kind + ";base64," + base64.b64encode(raw).decode("ascii")


def window_rows() -> list[tuple[str, str]]:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    rows: list[tuple[str, str]] = []

    def process_name(pid: int) -> str:
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return ""
        try:
            buffer = ctypes.create_unicode_buffer(520)
            size = wintypes.DWORD(520)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return Path(buffer.value).name.lower()
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
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rows.append((process_name(int(pid.value)), buffer.value))
        return True

    user32.EnumWindows(visit, 0)
    return rows


class MediaBridge:
    def __init__(self) -> None:
        self._commands: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._state = empty_state()
        self._hold_playing: bool | None = None
        self._hold_until = 0.0
        self._windows_at = 0.0
        self._windows_cache: list[tuple[str, str]] | None = None
        self._art_key = ""
        self._art = ""
        self._thread = threading.Thread(target=self._thread_main, name="desk-media", daemon=True)
        self._thread.start()

    def snapshot(self) -> dict:
        with self._lock:
            state = dict(self._state)
            if self._hold_playing is not None and time.time() < self._hold_until:
                state["playing"] = self._hold_playing
            return state

    def set_playing(self, playing: bool) -> dict:
        return self._call(("playing", bool(playing)))

    def skip(self, delta: int) -> dict:
        return self._call(("skip", 1 if int(delta) > 0 else -1))

    def _call(self, command: tuple) -> dict:
        reply: queue.Queue = queue.Queue(1)
        self._commands.put((command, reply))
        try:
            return reply.get(timeout=4)
        except queue.Empty:
            state = self.snapshot()
            state["ok"] = False
            state["error"] = "재생 응답이 늦습니다."
            return state

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._loop())
        except Exception:
            logging.exception("media loop stopped")
            with self._lock:
                self._state = empty_state() | {
                    "ready": True,
                    "ok": False,
                    "error": "재생 상태를 읽지 못했습니다.",
                }

    async def _loop(self) -> None:
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager

        manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
        with self._lock:
            self._state["ready"] = True
        while True:
            await self._drain(manager)
            await self._refresh(manager)
            await asyncio.sleep(0.7)

    async def _drain(self, manager) -> None:
        while True:
            try:
                command, reply = self._commands.get_nowait()
            except queue.Empty:
                return
            kind, value = command
            try:
                if kind == "playing":
                    await self._set_playing(manager, bool(value))
                elif kind == "skip":
                    await self._skip(manager, int(value))
                reply.put(self.snapshot())
            except Exception as exc:  # keep the loop alive for the next command
                logging.exception("media command failed")
                state = self.snapshot()
                state["ok"] = False
                state["error"] = str(exc) or "재생을 바꾸지 못했습니다."
                reply.put(state)

    async def _set_playing(self, manager, playing: bool) -> None:
        session = self._winner(manager)
        if session is None:
            with self._lock:
                self._state["ok"] = False
                self._state["error"] = "이어 재생할 브라우저 음악이 없습니다. 유튜브를 열어 두세요."
                self._state["playing"] = False
            return
        if playing:
            await session.try_play_async()
        else:
            await session.try_pause_async()
        with self._lock:
            self._hold_playing = playing
            self._hold_until = time.time() + 1.6
            self._state["ok"] = True
            self._state["error"] = ""
            self._state["playing"] = playing

    async def _skip(self, manager, delta: int) -> None:
        session = self._winner(manager)
        if session is None:
            with self._lock:
                self._state["ok"] = False
                self._state["error"] = "넘어갈 재생이 없습니다."
            return
        if delta > 0:
            await session.try_skip_next_async()
        else:
            await session.try_skip_previous_async()
        with self._lock:
            self._state["ok"] = True
            self._state["error"] = ""

    def _windows(self) -> list[tuple[str, str]]:
        now = time.monotonic()
        if self._windows_cache is not None and now - self._windows_at < 1.5:
            return self._windows_cache
        self._windows_cache = window_rows()
        self._windows_at = now
        return self._windows_cache

    def _winner(self, manager):
        chosen, sessions = self._describe(manager)
        if chosen is None:
            return None
        return sessions.get(chosen["key"])

    def _describe(self, manager):
        titles = self._windows()
        youtube_window = any(
            exe in _BROWSER_EXES and "youtube" in title.lower() for exe, title in titles
        )
        described = []
        sessions = {}
        for index, session in enumerate(list(manager.get_sessions())):
            info = session.get_playback_info()
            key = index
            sessions[key] = session
            described.append(
                {
                    "key": key,
                    "aumid": session.source_app_user_model_id or "",
                    "status": int(info.playback_status),
                    "titles": [],
                    "youtube_window": youtube_window,
                    "canPlay": bool(info.controls.is_play_enabled),
                    "canPause": bool(info.controls.is_pause_enabled),
                    "canNext": bool(info.controls.is_next_enabled),
                    "canPrev": bool(info.controls.is_previous_enabled),
                }
            )
        return choose(described), sessions

    async def _refresh(self, manager) -> None:
        try:
            chosen, sessions = self._describe(manager)
        except Exception:
            logging.exception("media refresh failed")
            return
        if chosen is None:
            with self._lock:
                ready = self._state.get("ready", False)
                self._state = empty_state() | {"ready": ready, "ok": True}
                self._art_key = ""
                self._art = ""
                self._clear_hold_if_matched(False)
            return
        session = sessions[chosen["key"]]
        title = ""
        artist = ""
        props = None
        try:
            props = await session.try_get_media_properties_async()
            if props is not None:
                title = props.title or ""
                artist = props.artist or ""
        except Exception:
            logging.exception("media properties failed")
        art_key = f"{title}\0{artist}"
        if art_key != self._art_key:
            self._art_key = art_key
            self._art = ""
            if props is not None and title:
                try:
                    self._art = await artwork_url(props)
                except Exception:
                    logging.exception("media artwork")
                    self._art = ""
        playing = chosen["status"] == PLAYING
        with self._lock:
            self._clear_hold_if_matched(playing)
            held = self._hold_playing is not None and time.time() < self._hold_until
            self._state = {
                "ok": True,
                "ready": True,
                "playing": self._hold_playing if held else playing,
                "title": title,
                "artist": artist,
                "source": chosen["source"],
                "thumbnail": self._art,
                "canPlay": chosen["canPlay"],
                "canPause": chosen["canPause"],
                "canNext": chosen["canNext"],
                "canPrev": chosen["canPrev"],
                "error": "",
            }

    def _clear_hold_if_matched(self, playing: bool) -> None:
        if self._hold_playing is None:
            return
        if playing == self._hold_playing or time.time() >= self._hold_until:
            self._hold_playing = None
            self._hold_until = 0.0
