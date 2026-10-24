"""CPU, memory, GPU, and disk readings for the bottom bar.

These are live readings from this PC. CPU needs two samples, so the first
pass reports that it is still measuring. GPU uses nvidia-smi when that
program is installed, and stays quiet about it until the meter is enabled.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
from ctypes import wintypes
from pathlib import Path

METER_IDS = ("meter-cpu", "meter-ram", "meter-gpu", "meter-disk")
METER_NAMES = {
    "meter-cpu": "CPU",
    "meter-ram": "RAM",
    "meter-gpu": "GPU",
    "meter-disk": "디스크",
}
DEFAULT_METERS = ("meter-cpu", "meter-ram")

_CREATE_NO_WINDOW = 0x08000000


class FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def enabled_meter_ids(settings: dict) -> list[str]:
    if settings.get("usageMetersConfigured"):
        chosen = []
        for item in settings.get("usageMeters") or []:
            if item in METER_NAMES and item not in chosen:
                chosen.append(item)
        return chosen
    return list(DEFAULT_METERS)


def cpu_used(previous: tuple[int, int] | None, current: tuple[int, int]) -> int | None:
    if previous is None:
        return None
    idle_delta = current[0] - previous[0]
    total_delta = current[1] - previous[1]
    if total_delta <= 0:
        return None
    return max(0, min(100, round(100 * (total_delta - idle_delta) / total_delta)))


def compact_pair(used: int, total: int) -> str:
    if total <= 0:
        return ""
    return f"{_compact(used)}/{_compact(total)}"


def _compact(value: int) -> str:
    gb = value / (1024**3)
    if gb >= 10:
        return f"{round(gb)}GB"
    return f"{gb:.1f}GB"


def _filetime(value: FILETIME) -> int:
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def _kernel32():
    library = ctypes.WinDLL("kernel32", use_last_error=True)
    library.GetSystemTimes.argtypes = [
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
    ]
    library.GetSystemTimes.restype = wintypes.BOOL
    library.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MEMORYSTATUSEX)]
    library.GlobalMemoryStatusEx.restype = wintypes.BOOL
    library.GetDiskFreeSpaceExW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.POINTER(ctypes.c_ulonglong),
    ]
    library.GetDiskFreeSpaceExW.restype = wintypes.BOOL
    return library


def _meter(meter_id: str, used: int | None, left: str, detail: str) -> dict:
    bars = []
    status = "ready"
    if used is None:
        status = "wait"
    else:
        bars.append({"label": "", "used": int(used), "left": left})
    return {
        "id": meter_id,
        "name": METER_NAMES[meter_id],
        "status": status,
        "detail": detail,
        "bars": bars,
        "lines": [],
    }


class MachineMeters:
    def __init__(self) -> None:
        self._kernel = None
        self._cpu_sample: tuple[int, int] | None = None
        self._nvidia: str | None | bool = None
        self._gpu: dict | None = None
        self._gpu_at = 0.0

    def sample(self, enabled: list[str], now: float | None = None) -> list[dict]:
        import time

        moment = time.monotonic() if now is None else now
        items = []
        for meter_id in enabled:
            if meter_id == "meter-cpu":
                items.append(self._cpu())
            elif meter_id == "meter-ram":
                items.append(self._ram())
            elif meter_id == "meter-disk":
                items.append(self._disk())
            elif meter_id == "meter-gpu":
                items.append(self._gpu_meter(moment))
        return [item for item in items if item is not None]

    def _cpu(self) -> dict:
        current = self._times()
        if current is None:
            return _meter("meter-cpu", None, "", "프로세서 사용량을 읽지 못했습니다")
        used = cpu_used(self._cpu_sample, current)
        self._cpu_sample = current
        if used is None:
            return _meter("meter-cpu", None, "", "측정 중")
        return _meter("meter-cpu", used, "", "프로세서 사용량")

    def _ram(self) -> dict:
        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not self._win().GlobalMemoryStatusEx(ctypes.byref(status)):
            return _meter("meter-ram", None, "", "메모리 사용량을 읽지 못했습니다")
        total = int(status.ullTotalPhys)
        used_bytes = total - int(status.ullAvailPhys)
        return _meter("meter-ram", int(status.dwMemoryLoad), compact_pair(used_bytes, total), "메모리 사용량")

    def _disk(self) -> dict:
        drive = os.environ.get("SystemDrive", "C:") + "\\"
        free = ctypes.c_ulonglong()
        total = ctypes.c_ulonglong()
        if not self._win().GetDiskFreeSpaceExW(drive, None, ctypes.byref(total), ctypes.byref(free)):
            return _meter("meter-disk", None, "", "디스크 사용량을 읽지 못했습니다")
        whole = int(total.value)
        if whole <= 0:
            return _meter("meter-disk", None, "", "디스크 사용량을 읽지 못했습니다")
        used_bytes = whole - int(free.value)
        percent = max(0, min(100, round(100 * used_bytes / whole)))
        return _meter("meter-disk", percent, compact_pair(used_bytes, whole), f"{drive[0]}: 사용량")

    def _gpu_meter(self, now: float) -> dict:
        if self._nvidia is None:
            self._nvidia = _find_nvidia_smi() or False
        if self._nvidia is False:
            return _meter("meter-gpu", None, "", "NVIDIA 사용량을 읽지 못했습니다")
        if self._gpu is None or now - self._gpu_at >= 4:
            self._gpu = _read_nvidia(str(self._nvidia))
            self._gpu_at = now
        if not self._gpu:
            return _meter("meter-gpu", None, "", "GPU 사용량을 읽지 못했습니다")
        return _meter("meter-gpu", self._gpu["used"], self._gpu["left"], "GPU 사용량")

    def _times(self) -> tuple[int, int] | None:
        idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
        if not self._win().GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        return _filetime(idle), _filetime(kernel) + _filetime(user)

    def _win(self):
        if self._kernel is None:
            self._kernel = _kernel32()
        return self._kernel


def _find_nvidia_smi() -> str | None:
    windir = os.environ.get("WINDIR", r"C:\Windows")
    candidates = [
        Path(windir) / "System32" / "nvidia-smi.exe",
        Path(r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("nvidia-smi")
    return found or None


def _read_nvidia(executable: str) -> dict | None:
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=1.2,
            creationflags=_CREATE_NO_WINDOW,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    best: tuple[int, int, int] | None = None
    for line in (result.stdout or "").splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            util = max(0, min(100, int(float(parts[0]))))
            used = int(float(parts[1])) * 1024 * 1024
            total = int(float(parts[2])) * 1024 * 1024
        except ValueError:
            continue
        if best is None or util > best[0]:
            best = (util, used, total)
    if best is None:
        return None
    return {"used": best[0], "left": compact_pair(best[1], best[2])}
