"""Android call events over the local network.

The phone runs KDE Connect. Desk speaks just enough of that protocol to
pair and receive telephony packets. Internet on this PC stays on ethernet.
"""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import select
import socket
import ssl
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from desk.library import library_path

logger = logging.getLogger(__name__)

PORT = 1716
DEVICE_NAME = "Desk"
_id_lock = threading.Lock()
_id_seq = 0


def _packet_id() -> int:
    global _id_seq
    with _id_lock:
        _id_seq = (_id_seq + 1) % 1000
        return int(time.time() * 1000) + _id_seq


def _prepare_socket(sock: socket.socket) -> None:
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        # Idle 20s, then a probe every 5s. The hotspot otherwise drops a quiet phone.
        sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 20_000, 5_000))
    except OSError:
        pass


class PacketBuffer:
    def __init__(self) -> None:
        self.buf = b""
        self.payload_left = 0

    def feed(self, data: bytes) -> list[dict]:
        self.buf += data
        packets: list[dict] = []
        while True:
            if self.payload_left:
                if len(self.buf) < self.payload_left:
                    return packets
                self.buf = self.buf[self.payload_left :]
                self.payload_left = 0
            mark = self.buf.find(b"\n")
            if mark < 0:
                return packets
            line = self.buf[:mark].strip()
            self.buf = self.buf[mark + 1 :]
            if not line:
                continue
            try:
                packet = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(packet, dict):
                continue
            try:
                self.payload_left = max(0, int(packet.get("payloadSize") or 0))
            except (TypeError, ValueError):
                self.payload_left = 0
            packets.append(packet)
        return packets


def call_text(body: dict) -> dict:
    event = str(body.get("event") or "")
    if body.get("isCancel"):
        return {"dismiss": True, "event": event}
    name = str(body.get("contactName") or "").strip()
    number = str(body.get("phoneNumber") or "").strip()
    who = name or number or "번호 없음"
    if event == "ringing":
        return {"title": "전화", "body": who, "sticky": True, "event": event}
    if event == "missedCall":
        return {"title": "부재중 전화", "body": who, "event": event}
    if event == "talking":
        return {"title": "통화 중", "body": who, "ttl": 8000, "event": event}
    return {}


class _Link:
    def __init__(self, sock: ssl.SSLSocket) -> None:
        self.sock = sock
        self.fingerprint = _fingerprint(sock)
        self.outbox: queue.Queue[dict] = queue.Queue()
        self.alive = True
        self.closing = False
        self.pair_sent = False

    def enqueue(self, packet: dict) -> None:
        if self.alive:
            self.outbox.put(packet)

    def close(self) -> None:
        self.alive = False
        self.closing = True
        try:
            self.sock.close()
        except OSError:
            pass


class PhoneLink:
    def __init__(self, on_event) -> None:
        self._on_event = on_event
        self._root = library_path().parent / "phone"
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._links: dict[str, _Link] = {}
        self._names: dict[str, str] = {}
        self._nearby: dict[str, dict] = {}
        self._pending: dict[str, float] = {}
        self._dialed: dict[str, float] = {}
        self._trusted = self._load_trusted()
        self._last_addr: dict[str, tuple[str, int]] = {}
        self._identity = self._load_identity()
        self._error = ""
        self._listening = False
        self.device_id = self._identity["deviceId"]
        self._udp: socket.socket | None = None
        self._tcp: socket.socket | None = None

    def start(self) -> None:
        threading.Thread(target=self._run, name="desk-phone", daemon=True).start()

    def state(self) -> dict:
        now = time.time()
        with self._lock:
            self._nearby = {
                key: item for key, item in self._nearby.items() if now - item["seen"] < 25
            }
            paired = []
            for device_id, item in self._trusted.items():
                paired.append(
                    {
                        "id": device_id,
                        "name": item.get("name") or "폰",
                        "online": bool(self._links.get(device_id) and self._links[device_id].alive),
                    }
                )
            nearby = []
            for device_id, item in self._nearby.items():
                if device_id in self._trusted:
                    continue
                nearby.append({"id": device_id, "name": item.get("name") or "폰"})
            pending = [
                {"id": device_id, "name": self._names.get(device_id) or "폰"}
                for device_id, at in self._pending.items()
                if now - at < 30
            ]
        return {
            "ok": True,
            "listening": self._listening,
            "error": self._error,
            "paired": paired,
            "nearby": nearby,
            "pending": pending,
        }

    def request_pair(self, device_id: str) -> dict:
        device_id = str(device_id or "")
        with self._lock:
            link = self._links.get(device_id)
        if link is None or not link.alive:
            self._dial_when_seen(device_id, pair=True)
            return {"ok": True}
        self._confirm_once(link)
        return {"ok": True}

    def accept(self, device_id: str) -> dict:
        device_id = str(device_id or "")
        with self._lock:
            link = self._links.get(device_id)
            fingerprint = link.fingerprint if link is not None else ""
            self._pending.pop(device_id, None)
            self._trust(device_id, fingerprint)
        if link is not None:
            self._confirm_once(link)
        logger.info("phone pair accepted")
        return self.state()

    def reject(self, device_id: str) -> dict:
        device_id = str(device_id or "")
        with self._lock:
            link = self._links.pop(device_id, None)
            self._pending.pop(device_id, None)
            self._trusted.pop(device_id, None)
            self._save_trusted()
        if link is not None:
            link.enqueue(self._pair_packet(False))
            link.closing = True
        return self.state()

    def _run(self) -> None:
        try:
            self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self._udp.bind(("0.0.0.0", PORT))
            self._udp.settimeout(1)
            self._tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._tcp.bind(("0.0.0.0", PORT))
            self._tcp.listen(4)
            self._tcp.settimeout(1)
        except OSError:
            self._error = "폰 연결 포트를 열지 못했습니다. 다른 KDE Connect가 켜져 있으면 끄세요."
            logger.exception("phone bind")
            return
        self._listening = True
        threading.Thread(target=self._accept_loop, name="desk-phone-tcp", daemon=True).start()
        next_hello = 0.0
        while not self._stop.is_set():
            now = time.time()
            if now >= next_hello:
                # A broadcast while the phone is already connected makes it open
                # another socket, and closing that socket makes the phone drop both.
                if self._needs_broadcast():
                    self._broadcast()
                    self._redial_known()
                next_hello = now + 8
            try:
                data, addr = self._udp.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            self._on_udp(data, addr)

    def _needs_broadcast(self) -> bool:
        with self._lock:
            return not any(link.alive for link in self._links.values())

    def _ipv4s(self) -> list[str]:
        found: list[str] = []
        try:
            infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        except OSError:
            return found
        for info in infos:
            ip = info[4][0]
            if ip.startswith("127.") or ip in found:
                continue
            found.append(ip)
        return found

    def _broadcast(self) -> None:
        packet = json.dumps(self._identity_packet()).encode("utf-8")
        ips = self._ipv4s()
        targets = [("255.255.255.255", PORT)]
        for ip in ips:
            parts = ip.split(".")
            if len(parts) == 4:
                targets.append((".".join(parts[:3] + ["255"]), PORT))
        sources = ips or [""]
        for source in sources:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                if source:
                    sock.bind((source, 0))
                for target in targets:
                    try:
                        sock.sendto(packet, target)
                    except OSError:
                        continue
            except OSError:
                continue
            finally:
                sock.close()

    def _redial_known(self) -> None:
        with self._lock:
            device_ids = [device_id for device_id in self._trusted if device_id not in self._links]
        for device_id in device_ids:
            self._dial_when_seen(device_id, pair=False)

    def _on_udp(self, data: bytes, addr) -> None:
        try:
            packet = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(packet, dict) or packet.get("type") != "kdeconnect.identity":
            return
        body = packet.get("body") or {}
        device_id = str(body.get("deviceId") or "")
        if not device_id or device_id == self.device_id:
            return
        name = _clean_name(str(body.get("deviceName") or "폰"))
        try:
            port = int(body.get("tcpPort") or PORT)
        except (TypeError, ValueError):
            port = PORT
        with self._lock:
            self._names[device_id] = name
            self._nearby[device_id] = {"name": name, "ip": addr[0], "port": port, "seen": time.time()}
            self._last_addr[device_id] = (addr[0], port)
            online = bool(self._links.get(device_id) and self._links[device_id].alive)
        if not online:
            self._dial_when_seen(device_id, pair=False)

    def _dial_when_seen(self, device_id: str, pair: bool) -> None:
        with self._lock:
            info = self._nearby.get(device_id)
            if info is None and device_id in self._last_addr:
                ip, port = self._last_addr[device_id]
                info = {
                    "name": self._names.get(device_id) or "폰",
                    "ip": ip,
                    "port": port,
                    "seen": time.time(),
                }
                self._nearby[device_id] = info
            current = self._links.get(device_id)
            if info is None or (current is not None and current.alive):
                return
            last = self._dialed.get(device_id, 0)
            if time.time() - last < 3:
                return
            self._dialed[device_id] = time.time()
            ip = info["ip"]
            port = info["port"]
        threading.Thread(
            target=self._dial,
            args=(device_id, ip, port, pair),
            name="desk-phone-dial",
            daemon=True,
        ).start()

    def _dial(self, device_id: str, ip: str, port: int, pair: bool) -> None:
        try:
            sock = socket.create_connection((ip, port), timeout=8)
            _prepare_socket(sock)
            sock.sendall(self._identity_line())
            remote = self._wrap_server(sock)
        except (OSError, ssl.SSLError) as exc:
            logger.info("phone dial failed: %s", exc)
            return
        self._serve(remote, device_id, pair)

    def _accept_loop(self) -> None:
        while not self._stop.is_set() and self._tcp is not None:
            try:
                client, _addr = self._tcp.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._inbound, args=(client,), daemon=True).start()

    def _inbound(self, client: socket.socket) -> None:
        client.settimeout(8)
        try:
            line = self._read_line(client)
            ident = json.loads(line.decode("utf-8"))
            device_id = str((ident.get("body") or {}).get("deviceId") or "")
            _prepare_socket(client)
            remote = self._wrap_client(client)
        except (OSError, ssl.SSLError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            self._close(client)
            return
        self._serve(remote, device_id, False)

    def _serve(self, remote: ssl.SSLSocket, device_id: str, pair: bool) -> None:
        link = _Link(remote)
        attached = ""
        reason = "closed"
        try:
            remote.settimeout(None)
            if device_id:
                self._attach(device_id, link)
                attached = device_id
            link.enqueue(self._identity_packet())
            if pair:
                self._confirm_once(link)
            buffer = PacketBuffer()
            while not self._stop.is_set():
                # TCP keepalive holds the socket. kdeconnect.ping is a visible
                # "ping" on the phone, and answering it with another ping loops.
                self._flush(link)
                if link.closing and link.outbox.empty():
                    break
                if link.closing:
                    reason = "send failed"
                    break
                try:
                    readable, _, _ = select.select([remote], [], [], 1.0)
                except OSError:
                    reason = "select failed"
                    break
                if not readable:
                    continue
                try:
                    chunk = remote.recv(65535)
                except (ssl.SSLWantReadError, TimeoutError):
                    continue
                except ssl.SSLError as exc:
                    text = str(exc).lower()
                    if "timed out" in text or "want read" in text:
                        continue
                    reason = str(exc)
                    logger.info("phone link ended %s", exc)
                    break
                if not chunk:
                    reason = "phone closed"
                    logger.info("phone link closed by phone")
                    break
                device_id, attached = self._consume(link, device_id, attached, buffer, chunk)
                while remote.pending():
                    try:
                        extra = remote.recv(65535)
                    except ssl.SSLWantReadError:
                        break
                    if not extra:
                        reason = "phone closed"
                        link.closing = True
                        break
                    device_id, attached = self._consume(link, device_id, attached, buffer, extra)
        except OSError as exc:
            reason = str(exc)
            logger.info("phone link ended %s", exc)
        finally:
            replaced = False
            if attached:
                with self._lock:
                    replaced = self._links.get(attached) is not link
                self._detach(attached, link)
            link.close()
            if not replaced:
                logger.info("phone link down %s (%s)", attached or device_id, reason)
                self._redial_soon(attached or device_id)

    def _consume(self, link: _Link, device_id: str, attached: str, buffer: PacketBuffer, chunk: bytes) -> tuple[str, str]:
        for packet in buffer.feed(chunk):
            found = self._handle(link, device_id, packet) or device_id
            if found and found != attached:
                self._attach(found, link)
                if attached:
                    self._detach(attached, link)
                attached = found
            device_id = found or device_id
        return device_id, attached

    def _redial_soon(self, device_id: str) -> None:
        if not device_id or self._stop.is_set():
            return
        with self._lock:
            current = self._links.get(device_id)
            if current is not None and current.alive:
                return
        if self._needs_broadcast():
            self._broadcast()
        self._dial_when_seen(device_id, pair=False)

    def _handle(self, link: _Link, device_id: str, packet: dict) -> str:
        kind = str(packet.get("type") or "")
        body = packet.get("body") if isinstance(packet.get("body"), dict) else {}
        if kind == "kdeconnect.identity":
            found = str(body.get("deviceId") or "")
            name = _clean_name(str(body.get("deviceName") or "폰"))
            if found:
                with self._lock:
                    self._names[found] = name
                    if found in self._trusted:
                        self._trusted[found]["name"] = name
                        self._save_trusted()
            return found or device_id
        if kind == "kdeconnect.pair":
            self._on_pair(link, device_id, bool(body.get("pair")))
            return device_id
        if kind == "kdeconnect.ping":
            return device_id
        if kind == "kdeconnect.telephony" and self._allows(device_id, link):
            text = call_text(body)
            if text:
                self._on_event({"kind": "call", "id": "call", **text})
            return device_id
        return device_id

    def _allows(self, device_id: str, link: _Link) -> bool:
        with self._lock:
            item = self._trusted.get(device_id)
        if not item:
            return False
        saved = str(item.get("fingerprint") or "")
        current = link.fingerprint
        return not saved or not current or saved == current

    def _on_pair(self, link: _Link, device_id: str, accept: bool) -> None:
        if not device_id:
            return
        if not accept:
            with self._lock:
                self._pending.pop(device_id, None)
                self._trusted.pop(device_id, None)
                self._save_trusted()
            return
        with self._lock:
            already = device_id in self._trusted
            name = self._names.get(device_id) or "폰"
            if not already:
                self._pending[device_id] = time.time()
        if already:
            return
        self._on_event({"kind": "pair", "title": "폰 연결", "body": f"{name}에서 연결 요청"})

    def _confirm_once(self, link: _Link) -> None:
        if link.pair_sent or not link.alive:
            return
        link.pair_sent = True
        link.enqueue(self._pair_packet(True))

    def _flush(self, link: _Link) -> None:
        while True:
            try:
                packet = link.outbox.get_nowait()
            except queue.Empty:
                return
            try:
                link.sock.sendall(json.dumps(packet, separators=(",", ":")).encode("utf-8") + b"\n")
            except OSError:
                link.closing = True
                return

    def _attach(self, device_id: str, link: _Link) -> None:
        if not device_id:
            return
        with self._lock:
            current = self._links.get(device_id)
            if current is link:
                return
            if current is not None and current.alive:
                # The phone treats a refused extra socket as "this PC is gone"
                # and closes the socket it already had. Take the new one.
                current.closing = True
                logger.info("phone link handed over %s", device_id)
            else:
                logger.info("phone link up %s", device_id)
            self._links[device_id] = link

    def _detach(self, device_id: str, link: _Link) -> None:
        if not device_id:
            return
        with self._lock:
            if self._links.get(device_id) is link:
                self._links.pop(device_id, None)

    def _trust(self, device_id: str, fingerprint: str = "") -> None:
        if not device_id:
            return
        current = self._trusted.get(device_id) or {}
        self._trusted[device_id] = {
            "name": self._names.get(device_id) or current.get("name") or "폰",
            "fingerprint": fingerprint or current.get("fingerprint") or "",
        }
        self._save_trusted()

    def _pair_packet(self, accept: bool) -> dict:
        body: dict = {"pair": accept}
        if accept:
            body["timestamp"] = int(time.time())
        return {"id": _packet_id(), "type": "kdeconnect.pair", "body": body}

    def _identity_packet(self) -> dict:
        return {
            "id": _packet_id(),
            "type": "kdeconnect.identity",
            "body": {
                "deviceId": self.device_id,
                "deviceName": DEVICE_NAME,
                "deviceType": "desktop",
                "incomingCapabilities": ["kdeconnect.telephony", "kdeconnect.ping"],
                "outgoingCapabilities": ["kdeconnect.ping"],
                "protocolVersion": 7,
                "tcpPort": PORT,
            },
        }

    def _identity_line(self) -> bytes:
        return json.dumps(self._identity_packet(), separators=(",", ":")).encode("utf-8") + b"\n"

    def _wrap_server(self, sock: socket.socket) -> ssl.SSLSocket:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(self._identity["cert"], self._identity["key"])
        context.verify_mode = ssl.CERT_NONE
        return context.wrap_socket(sock, server_side=True)

    def _wrap_client(self, sock: socket.socket) -> ssl.SSLSocket:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.load_cert_chain(self._identity["cert"], self._identity["key"])
        return context.wrap_socket(sock, server_hostname=None)

    def _read_line(self, sock: socket.socket) -> bytes:
        data = b""
        while b"\n" not in data:
            chunk = sock.recv(1)
            if not chunk:
                break
            data += chunk
            if len(data) > 65536:
                break
        line = data.split(b"\n", 1)[0].strip()
        if not line.startswith(b"{"):
            raise ValueError("identity")
        return line

    def _load_identity(self) -> dict:
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / "identity.json"
        cert = self._root / "desk.crt"
        key = self._root / "desk.key"
        device_id = ""
        if path.is_file():
            try:
                device_id = str(json.loads(path.read_text(encoding="utf-8")).get("deviceId") or "")
            except json.JSONDecodeError:
                device_id = ""
        if not cert.is_file() or not key.is_file() or len(device_id) < 32:
            device_id = uuid.uuid4().hex
            _write_cert(device_id, cert, key)
            path.write_text(json.dumps({"deviceId": device_id}), encoding="utf-8")
        return {"deviceId": device_id, "cert": str(cert), "key": str(key)}

    def _load_trusted(self) -> dict:
        path = self._root / "trusted.json"
        if not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        devices = raw.get("devices") if isinstance(raw, dict) else None
        return devices if isinstance(devices, dict) else {}

    def _save_trusted(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / "trusted.json"
        payload = json.dumps({"devices": self._trusted}, ensure_ascii=False, indent=2)
        path.write_text(payload, encoding="utf-8")

    def _close(self, sock) -> None:
        try:
            sock.close()
        except OSError:
            pass


def _fingerprint(sock) -> str:
    if sock is None:
        return ""
    try:
        der = sock.getpeercert(binary_form=True)
    except OSError:
        return ""
    if not der:
        return ""
    return hashlib.sha256(der).hexdigest()


def _clean_name(value: str) -> str:
    cleaned = "".join(ch for ch in value if ch not in "\"',;:.!?()[]<>").strip()
    return (cleaned or "폰")[:32]


def _write_cert(device_id: str, cert_path: Path, key_path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, device_id)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
