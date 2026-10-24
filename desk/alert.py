"""Windows toast plus the incoming-call ringtone.

In-app cards stay. A call also rings even when Desk is the front window,
because Windows often swallows a toast sound in that case.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import winsound
from ctypes import wintypes
from pathlib import Path

logger = logging.getLogger(__name__)

AUMID = "Larry.Desk"
RING = Path(r"C:\Windows\Media\Ring01.wav")

_lock = threading.Lock()
_ringing = False
_ready = False
_notifier = None
_shown: dict[str, object] = {}


def alert_plan(payload: dict) -> dict:
    kind = str(payload.get("kind") or "")
    event = str(payload.get("event") or "")
    title = str(payload.get("title") or "")
    body = str(payload.get("body") or "")
    if payload.get("dismiss"):
        return {"ring": "stop", "toast": "hide", "tag": "call", "title": title, "body": body, "call": False}
    if kind == "call" and event == "ringing":
        return {"ring": "start", "toast": "show", "tag": "call", "title": title or "전화", "body": body, "call": True}
    if kind == "call" and event == "talking":
        return {"ring": "stop", "toast": "hide", "tag": "call", "title": title, "body": body, "call": False}
    if kind == "call":
        return {"ring": "stop", "toast": "show", "tag": "call", "title": title or "전화", "body": body, "call": False}
    if kind in {"mail", "pair"}:
        return {"ring": None, "toast": "show", "tag": kind, "title": title or "Desk", "body": body, "call": False}
    return {"ring": None, "toast": None, "tag": "", "title": title, "body": body, "call": False}


def present(payload: dict) -> None:
    plan = alert_plan(payload)
    if plan["ring"] == "stop":
        stop_ring()
    elif plan["ring"] == "start":
        start_ring()
    if plan["toast"] == "hide":
        hide_toast(plan["tag"])
    elif plan["toast"] == "show":
        show_toast(plan["tag"], plan["title"], plan["body"], call=plan["call"])


def start_ring() -> None:
    global _ringing
    with _lock:
        if _ringing:
            return
        _ringing = True
    flags = winsound.SND_ASYNC | winsound.SND_LOOP
    if RING.is_file():
        winsound.PlaySound(str(RING), flags | winsound.SND_FILENAME)
    else:
        winsound.PlaySound("SystemExclamation", flags | winsound.SND_ALIAS)


def stop_ring() -> None:
    global _ringing
    with _lock:
        if not _ringing:
            return
        _ringing = False
    winsound.PlaySound(None, winsound.SND_PURGE)


def show_toast(tag: str, title: str, body: str, call: bool = False) -> None:
    _apartment()
    try:
        _ensure()
        notifier = _toast_notifier()
        if notifier is None:
            return
        note = _build(title, body, call)
        if note is None:
            return
        note.tag = tag[:16]
        note.group = "desk"
        notifier.show(note)
        _shown[tag] = note
    except Exception:
        logger.exception("toast")
        if call:
            try:
                notifier = _toast_notifier()
                note = _build(title, body, False)
                if notifier is not None and note is not None:
                    note.tag = tag[:16]
                    note.group = "desk"
                    notifier.show(note)
                    _shown[tag] = note
            except Exception:
                logger.exception("toast fallback")


def hide_toast(tag: str) -> None:
    _apartment()
    note = _shown.pop(tag, None)
    try:
        notifier = _toast_notifier()
        if notifier is not None and note is not None:
            notifier.hide(note)
    except Exception:
        logger.info("toast hide failed")
    try:
        from winrt.windows.ui.notifications import ToastNotificationManager

        ToastNotificationManager.history.remove_grouped_tag_with_id(tag[:16], "desk", AUMID)
    except Exception:
        return


def _build(title: str, body: str, call: bool):
    from winrt.windows.data.xml.dom import XmlDocument
    from winrt.windows.ui.notifications import ToastNotification

    scenario = ' scenario="incomingCall"' if call else ""
    audio = '<audio silent="true"/>' if call else '<audio src="ms-winsoundevent:Notification.Default"/>'
    xml = (
        f"<toast{scenario}>"
        "<visual><binding template=\"ToastGeneric\">"
        f"<text>{_xml(title)}</text><text>{_xml(body)}</text>"
        f"</binding></visual>{audio}</toast>"
    )
    document = XmlDocument()
    document.load_xml(xml)
    return ToastNotification(document)


def _toast_notifier():
    global _notifier
    if _notifier is not None:
        return _notifier
    try:
        from winrt.windows.ui.notifications import ToastNotificationManager

        _notifier = ToastNotificationManager.create_toast_notifier_with_id(AUMID)
    except Exception:
        logger.exception("toast notifier")
        return None
    return _notifier


def _ensure() -> None:
    global _ready
    with _lock:
        if _ready:
            return
        try:
            _write_shortcut()
        except Exception:
            logger.exception("toast shortcut")
        _ready = True


def _apartment() -> None:
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoInitializeEx(None, 0)


def _xml(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _write_shortcut() -> None:
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        sibling = exe.with_name("pythonw.exe")
        if sibling.is_file():
            exe = sibling
    root = Path(__file__).resolve().parents[1]
    icon = Path(__file__).resolve().parent / "ui" / "desk.ico"
    folder = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    folder.mkdir(parents=True, exist_ok=True)
    _shell_link(
        str(folder / "Desk.lnk"),
        str(exe),
        "-m desk",
        str(root),
        str(icon) if icon.is_file() else "",
        AUMID,
    )


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", wintypes.DWORD)]


class _PROPVARIANT(ctypes.Structure):
    _fields_ = [
        ("vt", wintypes.USHORT),
        ("r1", wintypes.USHORT),
        ("r2", wintypes.USHORT),
        ("r3", wintypes.USHORT),
        ("psz", wintypes.LPWSTR),
        ("pad", ctypes.c_ulonglong),
    ]


def _shell_link(path: str, target: str, args: str, workdir: str, icon: str, aumid: str) -> None:
    ole32 = ctypes.WinDLL("ole32")
    propsys = ctypes.WinDLL("propsys")
    ole32.CoInitialize.argtypes = [ctypes.c_void_p]
    ole32.CoInitialize.restype = ctypes.c_long
    ole32.CLSIDFromString.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(_GUID)]
    ole32.CLSIDFromString.restype = ctypes.c_long
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(_GUID),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.c_long
    ole32.CoTaskMemAlloc.argtypes = [ctypes.c_size_t]
    ole32.CoTaskMemAlloc.restype = ctypes.c_void_p
    ole32.PropVariantClear.argtypes = [ctypes.POINTER(_PROPVARIANT)]
    ole32.PropVariantClear.restype = ctypes.c_long
    propsys.PSGetPropertyKeyFromName.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(_PROPERTYKEY)]
    propsys.PSGetPropertyKeyFromName.restype = ctypes.c_long

    ole32.CoInitialize(None)

    def guid(text: str) -> _GUID:
        value = _GUID()
        hr = ole32.CLSIDFromString(text, ctypes.byref(value))
        if hr < 0:
            raise OSError(f"guid {text}")
        return value

    clsid = guid("{00021401-0000-0000-C000-000000000046}")
    shell_iid = guid("{000214F9-0000-0000-C000-000000000046}")
    link = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(shell_iid), ctypes.byref(link))
    if hr < 0 or not link.value:
        raise OSError(f"shortcut {hr & 0xFFFFFFFF:08X}")

    def call(unk, index: int, restype, argtypes, *args):
        table = ctypes.cast(unk, ctypes.POINTER(ctypes.c_void_p))[0]
        slot = ctypes.cast(table, ctypes.POINTER(ctypes.c_void_p))[index]
        proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
        return proto(slot)(unk, *args)

    def check(hr, what: str) -> None:
        if hr < 0:
            raise OSError(f"{what} {hr & 0xFFFFFFFF:08X}")

    check(call(link, 20, ctypes.c_long, [wintypes.LPCWSTR], target), "path")
    check(call(link, 11, ctypes.c_long, [wintypes.LPCWSTR], args), "args")
    check(call(link, 9, ctypes.c_long, [wintypes.LPCWSTR], workdir), "workdir")
    if icon:
        check(call(link, 17, ctypes.c_long, [wintypes.LPCWSTR, ctypes.c_int], icon, 0), "icon")

    store_iid = guid("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")
    store = ctypes.c_void_p()
    check(call(link, 0, ctypes.c_long, [ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(store_iid), ctypes.byref(store)), "store")
    key = _PROPERTYKEY()
    check(propsys.PSGetPropertyKeyFromName("System.AppUserModel.ID", ctypes.byref(key)), "key")
    variant = _PROPVARIANT()
    encoded = (aumid + "\0").encode("utf-16-le")
    memory = ole32.CoTaskMemAlloc(len(encoded))
    if not memory:
        raise OSError("aumid")
    ctypes.memmove(memory, encoded, len(encoded))
    variant.vt = 31
    variant.psz = ctypes.cast(memory, wintypes.LPWSTR)
    check(
        call(
            store,
            6,
            ctypes.c_long,
            [ctypes.POINTER(_PROPERTYKEY), ctypes.POINTER(_PROPVARIANT)],
            ctypes.byref(key),
            ctypes.byref(variant),
        ),
        "set",
    )
    check(call(store, 7, ctypes.c_long, []), "commit")
    ole32.PropVariantClear(ctypes.byref(variant))

    file_iid = guid("{0000010B-0000-0000-C000-000000000046}")
    persist = ctypes.c_void_p()
    check(call(link, 0, ctypes.c_long, [ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(file_iid), ctypes.byref(persist)), "file")
    check(call(persist, 6, ctypes.c_long, [wintypes.LPCWSTR, wintypes.BOOL], path, True), "save")

    for unk in (persist, store, link):
        if unk and unk.value:
            call(unk, 2, ctypes.c_ulong, [])
