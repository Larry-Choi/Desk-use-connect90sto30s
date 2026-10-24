"""AI quotas along the bottom of Desk.

Codex comes from the official app-server rate limit, then from Orca if that
app is running. Claude comes from the Claude Code login, then Orca, then the
Claude desktop session. Antigravity comes from `agy /usage`: the bar is the
model with the least remaining quota, not an average. Cursor is omitted
because that widget's Cursor number stayed at 100%. Tokens are not logged.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from desk.meters import METER_NAMES, MachineMeters, enabled_meter_ids
from desk.settings import load_settings

logger = logging.getLogger(__name__)


class UsageError(Exception):
    pass


def countdown(reset: str | None, now: datetime | None = None) -> str:
    if not reset:
        return "--"
    text = reset.replace("Z", "+00:00")
    try:
        target = datetime.fromisoformat(text)
    except ValueError:
        return "--"
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    minutes = int((target - current).total_seconds() // 60)
    if minutes <= 0:
        return "0m"
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def _bar(label: str, used: int, reset: str | None, hint: str = "") -> dict:
    used = max(0, min(100, int(used)))
    bar = {"label": label, "used": used, "left": countdown(reset)}
    if hint:
        bar["hint"] = hint
    return bar


def _item(item_id: str, name: str, status: str, detail: str = "", bars: list | None = None, lines: list | None = None) -> dict:
    return {
        "id": item_id,
        "name": name,
        "status": status,
        "detail": detail,
        "bars": bars or [],
        "lines": lines or [],
    }


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _epoch_iso(value) -> str | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    seconds = number / 1000 if number > 10_000_000_000 else number
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def _percent_bar(label: str, window, hint: str = "") -> dict | None:
    if not isinstance(window, dict):
        return None
    used = _number(window.get("usedPercent"))
    if used is None:
        return None
    bar = _bar(label, round(used), _epoch_iso(window.get("resetsAt")), hint)
    if bar["left"] == "--" and window.get("resetDescription"):
        bar["left"] = str(window["resetDescription"])[:32]
    return bar


def parse_agy(payload: dict) -> dict:
    if payload.get("status") and payload.get("status") != "SUCCESS":
        text = str(payload.get("response") or "")
        if "auth" in text.lower() or "log in" in text.lower() or "sign in" in text.lower():
            raise UsageError("agy 로그인이 필요합니다")
        raise UsageError("사용량을 받지 못했습니다")
    groups = ((payload.get("command") or {}).get("data") or {}).get("groups")
    if not isinstance(groups, list):
        raise UsageError("사용량 형식을 읽지 못했습니다")
    primary: tuple[float, str | None, str] | None = None
    weekly: tuple[float, str | None, str] | None = None
    lines: list[str] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        name = str(group.get("name") or "Antigravity")
        tightest: tuple[float, str | None] | None = None
        for bucket in group.get("buckets") or []:
            if not isinstance(bucket, dict):
                continue
            fraction = _number(bucket.get("remaining_fraction"))
            if fraction is None or not 0 <= fraction <= 1:
                continue
            reset = str(bucket.get("reset_time") or "") or None
            if tightest is None or fraction < tightest[0]:
                tightest = (fraction, reset)
            window = bucket.get("window")
            picked = (fraction, reset, name)
            if window == "weekly" and (weekly is None or fraction < weekly[0]):
                weekly = picked
            elif window == "5h" and (primary is None or fraction < primary[0]):
                primary = picked
        if tightest is not None:
            lines.append(f"{name} {100 - round(tightest[0] * 100)}%")
    if primary is None and weekly is None:
        raise UsageError("5시간 한도와 주간 한도를 찾지 못했습니다")
    bars = []
    if primary is not None:
        bars.append(_bar("5H", 100 - round(primary[0] * 100), primary[1], primary[2]))
    if weekly is not None:
        bars.append(_bar("WK", 100 - round(weekly[0] * 100), weekly[1], weekly[2]))
    return _item(
        "antigravity",
        "Antigravity",
        "ready",
        "agy가 알려 준 모델 중 가장 적게 남은 한도",
        bars,
        lines[:4],
    )


def claude_bars(payload: dict) -> list[dict] | None:
    five = payload.get("five_hour")
    if not isinstance(five, dict):
        return None
    used = _number(five.get("utilization"))
    if used is None:
        return None
    bars = [_bar("5H", round(used), five.get("resets_at") if isinstance(five.get("resets_at"), str) else None)]
    week = payload.get("seven_day")
    if isinstance(week, dict):
        week_used = _number(week.get("utilization"))
        if week_used is not None:
            bars.append(_bar("WK", round(week_used), week.get("resets_at") if isinstance(week.get("resets_at"), str) else None))
    return bars


def codex_from_limits(plan: str, rate_limits: dict) -> dict:
    primary = rate_limits.get("primary") if isinstance(rate_limits.get("primary"), dict) else {}
    secondary = rate_limits.get("secondary") if isinstance(rate_limits.get("secondary"), dict) else {}
    primary_mins = _number(primary.get("windowDurationMins")) or 0
    plan_name = _plan_name(plan or str(rate_limits.get("planType") or ""))
    weekly_only = plan_name == "ChatGPT Pro" or primary_mins >= 1440
    bars = []
    if weekly_only:
        source = primary if primary_mins >= 1440 or not secondary else secondary or primary
        bar = _percent_bar("WK", source)
        if bar:
            bars.append(bar)
    else:
        session = _percent_bar("5H", primary)
        week = _percent_bar("WK", secondary)
        if session:
            bars.append(session)
        if week:
            bars.append(week)
    if not bars:
        raise UsageError("Codex 한도 숫자를 받지 못했습니다")
    return _item("codex", "Codex", "ready", plan_name, bars)


def _plan_name(raw: str) -> str:
    names = {
        "plus": "ChatGPT Plus",
        "pro": "ChatGPT Pro",
        "team": "ChatGPT Team",
        "enterprise": "ChatGPT Enterprise",
        "ent26": "ChatGPT Enterprise",
        "free": "ChatGPT Free",
    }
    return names.get(raw.lower(), raw[:32] or "Codex")


def _hidden_run(args: list[str], **kwargs):
    return subprocess.run(
        args,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        **kwargs,
    )


def _find_agy() -> Path | None:
    roots = [Path(part) for part in os.environ.get("PATH", "").split(os.pathsep) if part]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "agy" / "bin")
    for root in roots:
        for name in ("agy.exe", "agy.cmd", "agy"):
            candidate = root / name
            if candidate.is_file():
                return candidate
    return None


def _run_agy(path: Path) -> dict:
    env = dict(os.environ)
    env["AGY_CLI_DISABLE_AUTO_UPDATE"] = "true"
    try:
        done = _hidden_run(
            [str(path), "-p", "/usage", "--output-format", "json"],
            capture_output=True,
            timeout=15,
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UsageError("agy 응답이 없습니다") from exc
    if done.returncode != 0:
        raise UsageError("agy 사용량을 받지 못했습니다")
    try:
        payload = json.loads(done.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise UsageError("agy 응답을 해석하지 못했습니다") from exc
    if not isinstance(payload, dict):
        raise UsageError("agy 응답을 해석하지 못했습니다")
    return payload


def _agy_item() -> dict | None:
    path = _find_agy()
    if path is None:
        return None
    try:
        return parse_agy(_run_agy(path))
    except UsageError as exc:
        return _item("antigravity", "Antigravity", "error", str(exc))


def _get_json(url: str, headers: dict[str, str]) -> dict | None:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            if response.status >= 300:
                return None
            raw = response.read(1_000_000)
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _claude_cli() -> dict | None:
    path = Path.home() / ".claude" / ".credentials.json"
    if not path.is_file():
        return None
    try:
        creds = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _item("claude", "Claude", "error", "Claude 로그인 파일을 읽지 못했습니다")
    oauth = creds.get("claudeAiOauth") if isinstance(creds, dict) else None
    token = oauth.get("accessToken") if isinstance(oauth, dict) else ""
    if not token:
        return _item("claude", "Claude", "error", "Claude 로그인이 필요합니다")
    payload = _get_json(
        "https://api.anthropic.com/api/oauth/usage",
        {"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "claude-code/0.2.29"},
    )
    bars = claude_bars(payload) if payload else None
    if not bars:
        return _item("claude", "Claude", "error", "Claude 사용량을 받지 못했습니다")
    return _item("claude", "Claude", "ready", "Claude Code 로그인", bars)


def _claude_data_dirs() -> list[Path]:
    dirs: list[Path] = []
    appdata = os.environ.get("APPDATA")
    local = os.environ.get("LOCALAPPDATA")
    if appdata:
        dirs.append(Path(appdata) / "Claude")
    if local:
        relocated = Path(local) / "Claude-Data"
        if relocated.is_dir():
            dirs.extend(path for path in relocated.iterdir() if path.is_dir() and path.name.lower().startswith("claude_"))
        packages = Path(local) / "Packages"
        if packages.is_dir():
            for path in packages.iterdir():
                if not path.is_dir():
                    continue
                name = path.name
                if name.lower().startswith("claude_") or ("claude" in name.lower() and "anthropic" in name.lower()):
                    dirs.append(path / "LocalCache" / "Roaming" / "Claude")
    return dirs


def _unprotect(wrapped: bytes) -> bytes:
    script = (
        "$ErrorActionPreference='Stop'; "
        "Add-Type -AssemblyName System.Security; "
        "$wrapped=[Convert]::FromBase64String([Console]::In.ReadToEnd().Trim()); "
        "$key=[Security.Cryptography.ProtectedData]::Unprotect($wrapped,$null,"
        "[Security.Cryptography.DataProtectionScope]::CurrentUser); "
        "[Console]::Out.Write([Convert]::ToBase64String($key))"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    system = os.environ.get("SystemRoot", r"C:\Windows")
    exe = str(Path(system) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
    try:
        done = _hidden_run(
            [exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            input=base64.b64encode(wrapped),
            capture_output=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UsageError("Claude 앱 키를 열지 못했습니다") from exc
    if done.returncode != 0:
        raise UsageError("Claude 앱 키를 열지 못했습니다")
    try:
        key = base64.b64decode(done.stdout.strip())
    except ValueError as exc:
        raise UsageError("Claude 앱 키를 열지 못했습니다") from exc
    if len(key) != 32:
        raise UsageError("Claude 앱 키를 열지 못했습니다")
    return key


def _decrypt_cache(encoded: str, key: bytes):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    blob = base64.b64decode(encoded)
    if len(blob) < 31 or blob[:3] not in (b"v10", b"v11"):
        raise UsageError("Claude 앱 로그인 형식을 읽지 못했습니다")
    return json.loads(AESGCM(key).decrypt(blob[3:15], blob[15:], None))


def _desktop_token(cache: dict, active: str) -> tuple[str, str]:
    now_ms = time.time() * 1000
    best: tuple[float, str, str] | None = None
    marker = ":https://api.anthropic.com:"
    for key, raw in cache.items():
        if not key.startswith("acct:") or "|" not in key:
            continue
        account_id, rest = key[5:].split("|", 1)
        if account_id.lower() != active.lower():
            continue
        host = rest.find(marker)
        if host < 0:
            continue
        scopes = rest[host + len(marker):].split(" ")
        if "user:sessions:claude_code" not in scopes or not isinstance(raw, dict):
            continue
        token = raw.get("token")
        expires = _number(raw.get("expiresAt"))
        if not isinstance(token, str) or not token or expires is None or expires <= now_ms:
            continue
        if best is None or expires > best[0]:
            best = (expires, token, str(raw.get("subscriptionType") or ""))
    if best is None:
        raise UsageError("Claude 앱 세션이 없거나 만료되었습니다")
    return best[1], best[2]


def _claude_desktop() -> dict | None:
    found = False
    for folder in _claude_data_dirs():
        config_path = folder / "config.json"
        state_path = folder / "Local State"
        if not config_path.is_file() or not state_path.is_file():
            continue
        found = True
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            state = json.loads(state_path.read_text(encoding="utf-8"))
            encoded = config.get("oauth:tokenCacheV2") if isinstance(config, dict) else None
            wrapped_text = ((state.get("os_crypt") or {}).get("encrypted_key") if isinstance(state, dict) else "") or ""
            if not isinstance(encoded, str) or not isinstance(wrapped_text, str):
                continue
            wrapped = base64.b64decode(wrapped_text)
            if not wrapped.startswith(b"DPAPI"):
                continue
            key = _unprotect(wrapped[5:])
            try:
                cache = _decrypt_cache(encoded, key)
            finally:
                key = b""
            if not isinstance(cache, dict):
                continue
            active = config.get("lastKnownAccountUuid") or config.get("activeAccountId") or ""
            if not isinstance(active, str) or not active:
                ids = {item[5:].split("|", 1)[0] for item in cache if item.startswith("acct:") and "|" in item}
                active = next(iter(ids)) if len(ids) == 1 else ""
            token, tier = _desktop_token(cache, active)
            payload = _get_json(
                "https://api.anthropic.com/api/oauth/usage",
                {
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "anthropic-beta": "oauth-2025-04-20",
                },
            )
            bars = claude_bars(payload) if payload else None
            if bars:
                return _item("claude", "Claude", "ready", tier or "Claude 앱", bars)
        except (OSError, json.JSONDecodeError, ValueError, UsageError, Exception):
            continue
    if found:
        return _item("claude", "Claude", "error", "Claude 앱 사용량을 받지 못했습니다")
    return None


def _orca_accounts() -> dict | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    path = Path(appdata) / "orca" / "orca-runtime.json"
    if not path.is_file():
        return None
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(meta, dict):
        return None
    transports = meta.get("transports") if isinstance(meta.get("transports"), list) else []
    endpoint = ""
    for item in transports:
        if isinstance(item, dict) and item.get("kind") == "named-pipe" and item.get("endpoint"):
            endpoint = str(item["endpoint"])
            break
    token = meta.get("authToken")
    if not endpoint or not isinstance(token, str) or not token:
        return None
    payload = json.dumps({"id": str(uuid.uuid4()), "authToken": token, "method": "accounts.list", "params": {}}) + "\n"
    box: queue.Queue = queue.Queue()

    def read() -> None:
        try:
            with open(endpoint, "r+b", buffering=0) as pipe:
                pipe.write(payload.encode("utf-8"))
                data = b""
                while b"\n" not in data and len(data) < 1_000_000:
                    chunk = pipe.read(4096)
                    if not chunk:
                        break
                    data += chunk
            box.put(data.split(b"\n", 1)[0])
        except OSError:
            box.put(b"")

    threading.Thread(target=read, daemon=True).start()
    try:
        raw = box.get(timeout=3)
    except queue.Empty:
        return None
    if not raw:
        return None
    try:
        message = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    result = message.get("result") if isinstance(message, dict) and message.get("ok") else None
    return result if isinstance(result, dict) else None


def _from_orca(kind: str, name: str, detail: str, data: dict | None) -> dict | None:
    limits = ((data or {}).get("rateLimits") or {}).get(kind) if data else None
    if not isinstance(limits, dict) or limits.get("status") != "ok":
        return None
    session = _percent_bar("5H", limits.get("session"))
    week = _percent_bar("WK", limits.get("weekly"))
    bars = [bar for bar in (session, week) if bar]
    if not bars:
        return None
    return _item(kind if kind != "codex" else "codex", name, "ready", detail, bars)


def _claude_item(orca: dict | None) -> dict | None:
    cli = _claude_cli()
    if cli and cli["status"] == "ready":
        return cli
    live = _from_orca("claude", "Claude", "Orca", orca)
    if live:
        return live
    desktop = _claude_desktop()
    if desktop and desktop["status"] == "ready":
        return desktop
    return cli or desktop


def _find_codex() -> Path | None:
    chosen = os.environ.get("CODEX_EXECUTABLE")
    if chosen and Path(chosen).is_file():
        return Path(chosen)
    candidates: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        root = Path(local) / "OpenAI" / "Codex" / "bin"
        if root.is_dir():
            candidates.extend(root.glob("*/codex.exe"))
    home = Path.home()
    for folder in (home / ".vscode" / "extensions", home / ".cursor" / "extensions", home / ".windsurf" / "extensions"):
        if folder.is_dir():
            candidates.extend(folder.glob("openai.chatgpt-*/bin/windows-x86_64/codex.exe"))
    for part in os.environ.get("PATH", "").split(os.pathsep):
        if part and (Path(part) / "codex.exe").is_file():
            candidates.append(Path(part) / "codex.exe")
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        return None
    return max(existing, key=lambda path: path.stat().st_mtime)


class _CodexLink:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._queue: queue.Queue = queue.Queue()
        self._next = 0
        self._ready = False

    def usage(self) -> dict | None:
        with self._lock:
            exe = _find_codex()
            if exe is None:
                return None
            self._ensure(exe)
            account = self._request("account/read", {"refreshToken": False}, 4)
            info = account.get("account") if isinstance(account, dict) else None
            if not isinstance(info, dict):
                raise UsageError("Codex에 로그인되어 있지 않습니다")
            limits = self._request("account/rateLimits/read", {}, 4)
            rate = None
            if isinstance(limits, dict):
                rate = limits.get("rateLimits")
                if not isinstance(rate, dict):
                    by_id = limits.get("rateLimitsByLimitId")
                    if isinstance(by_id, dict) and by_id:
                        rate = next(iter(by_id.values()))
            if not isinstance(rate, dict):
                raise UsageError("Codex 한도를 받지 못했습니다")
            plan = str(info.get("planType") or info.get("type") or "")
            return codex_from_limits(plan, rate)

    def _ensure(self, exe: Path) -> None:
        if self._proc is not None and self._proc.poll() is None and self._ready:
            return
        self._close()
        self._queue = queue.Queue()
        self._proc = subprocess.Popen(
            [str(exe), "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        box = self._queue
        stream = self._proc.stdout
        threading.Thread(target=self._read, args=(stream, box), daemon=True).start()
        self._request(
            "initialize",
            {
                "clientInfo": {"name": "desk", "title": "Desk", "version": "1.0.0"},
                "capabilities": {"experimentalApi": True},
            },
            8,
        )
        self._notify("initialized", {})
        self._ready = True

    def _read(self, stream, box: queue.Queue) -> None:
        if stream is None:
            box.put(None)
            return
        for line in stream:
            box.put(line)
        box.put(None)

    def _notify(self, method: str, params: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise UsageError("Codex에 연결하지 못했습니다")
        proc.stdin.write((json.dumps({"method": method, "params": params}) + "\n").encode("utf-8"))
        proc.stdin.flush()

    def _request(self, method: str, params: dict, timeout: float):
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise UsageError("Codex에 연결하지 못했습니다")
        self._next += 1
        ident = self._next
        proc.stdin.write((json.dumps({"method": method, "id": ident, "params": params}) + "\n").encode("utf-8"))
        proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                line = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                self._ready = False
                raise UsageError("Codex가 응답을 끊었습니다")
            try:
                message = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict) or message.get("id") != ident:
                continue
            if message.get("error"):
                self._close()
                raise UsageError("Codex 한도를 받지 못했습니다")
            return message.get("result")
        self._close()
        raise UsageError("Codex 응답이 없습니다")

    def _close(self) -> None:
        self._ready = False
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.kill()
        except OSError:
            pass


_codex = _CodexLink()


def _codex_item(orca: dict | None) -> dict | None:
    try:
        live = _codex.usage()
    except UsageError as exc:
        live = _item("codex", "Codex", "error", str(exc))
    except Exception:
        logger.exception("codex usage")
        live = _item("codex", "Codex", "error", "Codex 한도를 받지 못했습니다")
    if live and live["status"] == "ready":
        return live
    orca_item = _from_orca("codex", "Codex", "Orca", orca)
    if orca_item:
        return orca_item
    if live and _find_codex() is not None:
        return live
    return None


def apply_layout(live: list[dict], settings: dict, meters: list[dict] | None = None) -> list[dict]:
    hidden = set(settings.get("usageHidden") or [])
    pool = {item["id"]: item for item in live}
    for entry in settings.get("usageCustom") or []:
        pool[entry["id"]] = _custom_item(entry)
    for item in meters or []:
        pool[item["id"]] = item
    ordered = []
    seen = set()
    for item_id in settings.get("usageOrder") or []:
        item = pool.get(item_id)
        if item is not None and item_id not in hidden and item_id not in seen:
            ordered.append(item)
            seen.add(item_id)
    for item_id, item in pool.items():
        if item_id not in seen and item_id not in hidden:
            ordered.append(item)
    return ordered


def usage_catalog(live: list[dict], settings: dict) -> dict:
    pool = {item["id"]: item["name"] for item in live}
    custom = {entry["id"]: entry for entry in settings.get("usageCustom") or []}
    pool.update({item_id: entry["name"] for item_id, entry in custom.items()})
    pool.update(METER_NAMES)
    live_by_id = {item["id"]: item for item in live}
    shown_meters = set(enabled_meter_ids(settings))
    ordered = []
    seen = set()
    for item_id in list(settings.get("usageOrder") or []) + list(pool):
        if item_id in pool and item_id not in seen:
            ordered.append(item_id)
            seen.add(item_id)
    hidden = set(settings.get("usageHidden") or [])
    accounts = []
    for item_id in ordered:
        entry = custom.get(item_id, {})
        is_meter = item_id in METER_NAMES
        source = live_by_id.get(item_id)
        if source and source.get("bars"):
            preview = source["bars"]
        elif item_id in custom:
            preview = _custom_item(entry).get("bars") or []
        else:
            preview = []
        accounts.append(
            {
                "id": item_id,
                "name": pool[item_id],
                "enabled": item_id in shown_meters if is_meter else item_id not in hidden,
                "custom": item_id in custom,
                "meter": is_meter,
                "bars": preview,
                "session": entry.get("session"),
                "week": entry.get("week"),
                "sessionLeft": entry.get("sessionLeft") or "",
                "weekLeft": entry.get("weekLeft") or "",
            }
        )
    return {"ok": True, "align": settings.get("usageAlign") or "start", "accounts": accounts}


def _custom_item(entry: dict) -> dict:
    bars = []
    if entry.get("session") is not None:
        bars.append({"label": "5H", "used": int(entry["session"]), "left": entry.get("sessionLeft") or "--"})
    if entry.get("week") is not None:
        bars.append({"label": "WK", "used": int(entry["week"]), "left": entry.get("weekLeft") or "--"})
    return _item(entry["id"], entry["name"], "ready", "직접 입력", bars)


def collect() -> list[dict]:
    orca = _orca_accounts()
    items = []
    for read in (_agy_item, lambda: _claude_item(orca), lambda: _codex_item(orca)):
        try:
            item = read()
        except Exception:
            logger.exception("usage source failed")
            continue
        if item is not None:
            items.append(item)
    return items


class UsageMonitor:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._live: list[dict] = []
        self._meters: list[dict] = []
        self._settings: dict | None = None
        self._sampler = MachineMeters()
        self._stop = threading.Event()
        self._on_update = None

    def start(self, on_update) -> None:
        self._on_update = on_update
        threading.Thread(target=self._ai_loop, name="desk-usage", daemon=True).start()
        threading.Thread(target=self._meter_loop, name="desk-meters", daemon=True).start()

    def remember(self, settings: dict) -> None:
        with self._lock:
            self._settings = settings

    def resample(self) -> None:
        meters = self._sampler.sample(enabled_meter_ids(self._cached_settings()))
        with self._lock:
            self._meters = meters

    def snapshot(self) -> dict:
        settings = self._cached_settings()
        with self._lock:
            items = apply_layout(self._live, settings, self._meters)
        return {"ok": True, "align": settings.get("usageAlign") or "start", "items": [dict(item) for item in items]}

    def catalog(self) -> dict:
        settings = self._cached_settings()
        with self._lock:
            live = list(self._live)
        return usage_catalog(live, settings)

    def _cached_settings(self) -> dict:
        with self._lock:
            if self._settings is None:
                self._settings = load_settings()
            return self._settings

    def _publish(self) -> None:
        if self._on_update is None:
            return
        try:
            self._on_update(self.snapshot())
        except Exception:
            logger.exception("usage publish")

    def _ai_loop(self) -> None:
        while not self._stop.is_set():
            items = collect()
            with self._lock:
                self._live = items
            self._publish()
            self._stop.wait(60)

    def _meter_loop(self) -> None:
        while not self._stop.is_set():
            meters = self._sampler.sample(enabled_meter_ids(self._cached_settings()))
            with self._lock:
                self._meters = meters
            self._publish()
            self._stop.wait(2)
