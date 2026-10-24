"""Read Naver mail over IMAP. Sending mail is out of scope."""

from __future__ import annotations

import imaplib
from datetime import datetime
from email import message_from_bytes
from email.header import decode_header
from email.utils import parsedate_to_datetime

from desk.library import LibraryError

IMAP_HOST = "imap.naver.com"
IMAP_PORT = 993


class MailError(LibraryError):
    pass


def decode_mime(value: str) -> str:
    if not value:
        return ""
    parts: list[str] = []
    for text, charset in decode_header(value):
        if isinstance(text, bytes):
            parts.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(text)
    return "".join(parts).replace("\r", " ").replace("\n", " ").strip()


def sender_name(value: str) -> str:
    text = decode_mime(value)
    if "<" in text:
        name = text.split("<", 1)[0].strip().strip('"')
        if name:
            return name
    return text


def short_date(value: str, now: datetime | None = None) -> str:
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return decode_mime(value)
    if when is None:
        return decode_mime(value)
    if when.tzinfo is not None:
        when = when.astimezone()
    today = now or datetime.now().astimezone()
    clock = f"{when.hour:02d}:{when.minute:02d}"
    if when.date() == today.date():
        return clock
    return f"{when.month}/{when.day} {clock}"


def mail_has_fresh(unseen_uids, acked) -> bool:
    seen = {str(item) for item in acked or []}
    return any(str(uid) not in seen for uid in unseen_uids or [])


def unseen_headers(address: str, password: str, limit: int = 5) -> list[dict]:
    count = max(1, min(int(limit), 10))
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=12)
    except (TimeoutError, OSError) as exc:
        raise MailError("네이버 메일에 연결하지 못했습니다.") from exc
    try:
        try:
            imap.login(address, password)
            imap.select("INBOX", readonly=True)
            status, data = imap.uid("SEARCH", None, "UNSEEN")
        except imaplib.IMAP4.error as exc:
            raise MailError("받은편지함을 확인하지 못했습니다.") from exc
        if status != "OK" or not data or not data[0]:
            return []
        uids = data[0].split()[-count:]
        found = []
        for uid in reversed(uids):
            found.append(_uid_header(imap, uid))
        return found
    finally:
        try:
            imap.logout()
        except imaplib.IMAP4.error:
            pass


def _uid_header(imap: imaplib.IMAP4_SSL, uid: bytes) -> dict:
    status, data = imap.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
    raw = b""
    if status == "OK":
        for item in data:
            if isinstance(item, tuple):
                raw = item[1] or b""
    parsed = message_from_bytes(raw)
    return {
        "uid": uid.decode("ascii", errors="replace"),
        "from": sender_name(parsed.get("From", "")),
        "subject": decode_mime(parsed.get("Subject", "")) or "(제목 없음)",
        "date": short_date(parsed.get("Date", "")),
    }


def fetch_inbox(address: str, password: str, limit: int = 12) -> dict:
    count = max(1, min(int(limit), 30))
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=12)
    except (TimeoutError, OSError) as exc:
        raise MailError("네이버 메일에 연결하지 못했습니다.") from exc
    try:
        try:
            imap.login(address, password)
            imap.select("INBOX", readonly=True)
        except imaplib.IMAP4.error as exc:
            raise MailError(
                "주소나 애플리케이션 비밀번호가 맞지 않습니다. "
                "네이버 로그인 비밀번호가 아니라 2단계 인증에서 만든 애플리케이션 비밀번호를 넣고, "
                "네이버 메일 환경설정에서 IMAP/SMTP를 사용함으로 켜 두세요."
            ) from exc
        unseen_uids = _uid_search(imap, "UNSEEN")
        ids = _uid_search(imap, "ALL")
        messages = []
        for uid in reversed(ids[-count:]):
            messages.append(_uid_message(imap, uid))
        return {"unseen": len(unseen_uids), "unseenUids": unseen_uids, "messages": messages}
    finally:
        try:
            imap.logout()
        except imaplib.IMAP4.error:
            pass


def _uid_search(imap: imaplib.IMAP4_SSL, criteria: str) -> list[str]:
    status, data = imap.uid("SEARCH", None, criteria)
    if status != "OK" or not data or not data[0]:
        return []
    return [item.decode("ascii", errors="replace") for item in data[0].split() if item]


def _uid_message(imap: imaplib.IMAP4_SSL, uid: str) -> dict:
    status, data = imap.uid("FETCH", uid, "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
    raw = b""
    flags = ""
    if status == "OK":
        for item in data:
            if isinstance(item, tuple):
                flags = item[0].decode("utf-8", errors="replace")
                raw = item[1] or b""
    parsed = message_from_bytes(raw)
    return {
        "uid": uid,
        "from": sender_name(parsed.get("From", "")),
        "subject": decode_mime(parsed.get("Subject", "")) or "(제목 없음)",
        "date": short_date(parsed.get("Date", "")),
        "unseen": "\\Seen" not in flags,
    }
