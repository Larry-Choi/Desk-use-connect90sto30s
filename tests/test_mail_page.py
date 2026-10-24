import json
import tempfile
import unittest
from datetime import datetime
from email.header import Header
from pathlib import Path

from desk.mail import decode_mime, mail_has_fresh, sender_name, short_date
from desk.secrets import delete_mail_password, read_mail_password, write_mail_password
from desk.settings import (
    load_settings,
    normalize_mail_address,
    normalize_page_url,
    save_settings,
)
from desk.library import LibraryError
from desk.phone import PacketBuffer, call_text
from desk.alert import alert_plan


class MailPageTests(unittest.TestCase):
    def test_mail_address_accepts_naver_and_hanmail(self):
        self.assertEqual(normalize_mail_address(" Name@Naver.com "), "name@naver.com")
        self.assertEqual(normalize_mail_address("a@hanmail.net"), "a@hanmail.net")
        with self.assertRaises(LibraryError):
            normalize_mail_address("a@gmail.com")

    def test_page_url_stays_http(self):
        self.assertEqual(normalize_page_url("https://claude.ai/artifact/abc"), "https://claude.ai/artifact/abc")
        with self.assertRaises(LibraryError):
            normalize_page_url("javascript:alert(1)")
        with self.assertRaises(LibraryError):
            normalize_page_url("file:///C:/Windows")

    def test_settings_do_not_keep_a_password(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "desk.json"
            save_settings(path, {"mailAddress": "a@naver.com", "pageUrl": "https://example.com", "openPageWithDesk": True, "password": "nope"})
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("password", raw)
            loaded = load_settings(path)
            self.assertEqual(loaded["mailAddress"], "a@naver.com")
            self.assertTrue(loaded["openPageWithDesk"])

    def test_mail_dot_stays_off_for_mail_already_seen_in_desk(self):
        self.assertTrue(mail_has_fresh(["10", "11"], ["10"]))
        self.assertFalse(mail_has_fresh(["10", "11"], ["10", "11"]))
        self.assertFalse(mail_has_fresh([], ["10"]))

    def test_header_and_sender(self):
        encoded = Header("홍길동", "utf-8").encode()
        self.assertEqual(decode_mime(encoded), "홍길동")
        self.assertEqual(sender_name('홍길동 <a@naver.com>'), "홍길동")
        when = short_date("23 Oct 2025 16:05:00", datetime(2025, 10, 23, 18, 0))
        self.assertEqual(when, "16:05")

    def test_credential_round_trip(self):
        target = "Desk/test-mail-roundtrip"
        try:
            self.assertTrue(write_mail_password("a@naver.com", "secret-값", target))
            self.assertEqual(read_mail_password(target), "secret-값")
        finally:
            delete_mail_password(target)
        self.assertEqual(read_mail_password(target), "")

    def test_call_packet_and_buffer(self):
        ringing = call_text({"event": "ringing", "contactName": "민준", "phoneNumber": "010"})
        self.assertEqual(ringing["title"], "전화")
        self.assertEqual(ringing["body"], "민준")
        self.assertTrue(call_text({"event": "ringing", "isCancel": True})["dismiss"])
        buffer = PacketBuffer()
        raw = b'{"type":"kdeconnect.telephony","body":{"event":"ringing"},"payloadSize":4}\n'
        self.assertEqual(buffer.feed(raw)[0]["type"], "kdeconnect.telephony")
        self.assertEqual(buffer.feed(b"abcd"), [])
        more = buffer.feed(b'{"type":"kdeconnect.ping","body":{}}\n')
        self.assertEqual(more[0]["type"], "kdeconnect.ping")

    def test_call_alert_rings_and_stops(self):
        ringing = alert_plan({"kind": "call", "event": "ringing", "title": "전화", "body": "민준"})
        self.assertEqual(ringing["ring"], "start")
        self.assertTrue(ringing["call"])
        self.assertEqual(alert_plan({"kind": "call", "event": "ringing", "dismiss": True})["ring"], "stop")
        self.assertEqual(alert_plan({"kind": "call", "event": "talking", "title": "통화 중"})["toast"], "hide")
        missed = alert_plan({"kind": "call", "event": "missedCall", "title": "부재중 전화", "body": "민준"})
        self.assertEqual(missed["ring"], "stop")
        self.assertFalse(missed["call"])
        self.assertEqual(alert_plan({"kind": "mail", "title": "새 메일", "body": "a"})["tag"], "mail")


if __name__ == "__main__":
    unittest.main()
