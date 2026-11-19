"""Процессы: список, классификация (приложение / фоновый / системный), эвристики, действия."""
from __future__ import annotations

import ctypes
import logging
import os
import time
from ctypes import wintypes
from typing import Any

import psutil

from .winutil import IS_WINDOWS

log = logging.getLogger(__name__)

WINDIR = os.environ.get("WINDIR", r"C:\Windows").lower()
SYSTEM32 = os.path.join(WINDIR, "system32")

# критически важные процессы — завершать нельзя (BSOD или выход из сеанса)
CRITICAL = {"system", "system idle process", "registry", "smss.exe", "csrss.exe", "wininit.exe",
            "services.exe", "lsass.exe", "winlogon.exe", "memory compression", "secure system",
            "lsaiso.exe", "fontdrvhost.exe", "dwm.exe"}

# имена системных файлов, которые вредоносы любят подделывать: имя -> допустимые каталоги
EXPECTED_DIRS = {
    "svchost.exe": [SYSTEM32, os.path.join(WINDIR, "syswow64")],
    "lsass.exe": [SYSTEM32],
    "csrss.exe": [SYSTEM32],
    "winlogon.exe": [SYSTEM32],
    "services.exe": [SYSTEM32],
    "smss.exe": [SYSTEM32],
    "wininit.exe": [SYSTEM32],
    "spoolsv.exe": [SYSTEM32],
    "taskhostw.exe": [SYSTEM32],
    "dwm.exe": [SYSTEM32],
    "conhost.exe": [SYSTEM32],
    "rundll32.exe": [SYSTEM32, os.path.join(WINDIR, "syswow64")],
    "explorer.exe": [WINDIR],
    "ctfmon.exe": [SYSTEM32, os.path.join(WINDIR, "syswow64")],
    "dllhost.exe": [SYSTEM32, os.path.join(WINDIR, "syswow64")],
}

SYSTEM_ACCOUNTS = ("SYSTEM", "СИСТЕМА", "LOCAL SERVICE", "NETWORK SERVICE", "ЛОКАЛЬНАЯ СЛУЖБА",
                   "СЕТЕВАЯ СЛУЖБА", "DWM-1", "UMFD-0", "UMFD-1")

SUSPICIOUS_DIR_MARKERS = ("\\appdata\\local\\temp\\", "\\windows\\temp\\", "\\$recycle.bin\\",
                          "\\users\\public\\", "\\programdata\\temp", "\\downloads\\")

PRIORITY_NAMES = {
    "idle": "Низкий",
    "below": "Ниже среднего",
    "normal": "Обычный",
    "above": "Выше среднего",
    "high": "Высокий",
    "realtime": "Реального времени",
}


def _priority_map() -> dict[str, int]:
    if IS_WINDOWS:
        return {
            "idle": psutil.IDLE_PRIORITY_CLASS,
            "below": psutil.BELOW_NORMAL_PRIORITY_CLASS,
            "normal": psutil.NORMAL_PRIORITY_CLASS,
            "above": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
            "high": psutil.HIGH_PRIORITY_CLASS,
            "realtime": psutil.REALTIME_PRIORITY_CLASS,
        }
    return {"idle": 19, "below": 10, "normal": 0, "above": -5, "high": -10, "realtime": -20}


def priority_key(value: int) -> str:
    for k, v in _priority_map().items():
        if v == value:
            return k
    return "normal"


# ----------------------------------------------------------------- окна
def window_pids() -> tuple[set[int], set[int]]:
    """(PID с видимыми окнами, PID с зависшими окнами)."""
    visible: set[int] = set()
    hung: set[int] = set()
    if not IS_WINDOWS:
        return visible, hung
    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsHungAppWindow.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND

    def callback(hwnd: int, _lparam: int) -> bool:
        try:
            if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0 \
                    and not user32.GetWindow(hwnd, 4):  # GW_OWNER: только окна верхнего уровня
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                visible.add(pid.value)
                if user32.IsHungAppWindow(hwnd):
                    hung.add(pid.value)
        except Exception:
            pass
        return True

    user32.EnumWindows(enum_proc(callback), 0)
    return visible, hung


# ----------------------------------------------------------------- эвристики
def suspicion(name: str, exe: str) -> str:
    """Возвращает текст предупреждения или пустую строку."""
    if not exe:
        return ""
    low_name = name.lower()
    low_exe = exe.lower()
    folder = os.path.dirname(low_exe)
    if low_name in EXPECTED_DIRS and folder not in EXPECTED_DIRS[low_name]:
        return f"Системное имя «{name}» запущено не из системной папки"
    if any(m in low_exe for m in SUSPICIOUS_DIR_MARKERS):
        return "Запущен из временной/общей папки"
    if low_exe.count(".exe") > 1 or any(low_exe.endswith(ext) for ext in (".pdf.exe", ".doc.exe", ".jpg.exe")):
        return "Двойное расширение файла"
    return ""


class ProcessTracker:
    """Хранит объекты psutil.Process между вызовами для корректного расчёта загрузки CPU."""

    def __init__(self) -> None:
        self._cache: dict[int, psutil.Process] = {}
        self._ncpu = psutil.cpu_count() or 1
        self._self_pid = os.getpid()

    def snapshot(self) -> list[dict[str, Any]]:
        visible, hung = window_pids()
        rows: list[dict[str, Any]] = []
        seen: set[int] = set()
        attrs = ["pid", "name", "exe", "username", "memory_info", "num_threads", "create_time", "status",
                 "nice", "ppid"]
        for p in psutil.process_iter(attrs):
            pid = p.info["pid"]
            seen.add(pid)
            cached = self._cache.get(pid)
            if cached is None or cached.info.get("create_time") != p.info.get("create_time"):  # type: ignore[attr-defined]
                self._cache[pid] = p
                cached = p
                try:
                    p.cpu_percent(None)
                except (psutil.Error, OSError):
                    pass
                cpu = 0.0
            else:
                try:
                    cpu = cached.cpu_percent(None) / self._ncpu
                except (psutil.Error, OSError):
                    cpu = 0.0
            info = p.info
            name = info.get("name") or "?"
            exe = info.get("exe") or ""
            user = info.get("username") or ""
            mem = info.get("memory_info")
            low_exe = exe.lower()
            if pid in (0, 4) or user.upper().endswith(SYSTEM_ACCOUNTS) \
                    or (low_exe.startswith(WINDIR) and pid not in visible):
                kind = "Системный"
            elif pid in visible:
                kind = "Приложение"
            else:
                kind = "Фоновый"
            rows.append({
                "pid": pid,
                "name": name,
                "exe": exe,
                "user": user.split("\\")[-1],
                "cpu": round(cpu, 1),
                "mem": mem.rss if mem else 0,
                "threads": info.get("num_threads") or 0,
                "kind": kind,
                "status": "Не отвечает" if pid in hung else _status_ru(info.get("status")),
                "priority": PRIORITY_NAMES.get(priority_key(info.get("nice") or 0), "") if info.get("nice") is not None else "",
                "started": time.strftime("%d.%m %H:%M", time.localtime(info["create_time"])) if info.get("create_time") else "",
                "warn": suspicion(name, exe),
                "critical": name.lower() in CRITICAL or pid in (0, 4, self._self_pid),
            })
        for pid in list(self._cache):
            if pid not in seen:
                del self._cache[pid]
        return rows


def _status_ru(status: str | None) -> str:
    return {
        psutil.STATUS_RUNNING: "Работает",
        psutil.STATUS_STOPPED: "Приостановлен",
        psutil.STATUS_SLEEPING: "Работает",
    }.get(status or "", status or "")


# ----------------------------------------------------------------- действия
def kill(pid: int, tree: bool = False) -> str:
    try:
        proc = psutil.Process(pid)
        if proc.name().lower() in CRITICAL:
            return "Это критический системный процесс — завершение заблокировано."
        targets = proc.children(recursive=True) + [proc] if tree else [proc]
        for t in targets:
            try:
                t.kill()
            except psutil.Error:
                pass
        psutil.wait_procs(targets, timeout=3)
        return ""
    except psutil.AccessDenied:
        return "Отказано в доступе (нужны права администратора)."
    except psutil.NoSuchProcess:
        return ""
    except psutil.Error as exc:
        return str(exc)


def suspend(pid: int, resume: bool = False) -> str:
    try:
        proc = psutil.Process(pid)
        if proc.name().lower() in CRITICAL:
            return "Критический системный процесс — операция заблокирована."
        if resume:
            proc.resume()
        else:
            proc.suspend()
        return ""
    except psutil.AccessDenied:
        return "Отказано в доступе (нужны права администратора)."
    except psutil.Error as exc:
        return str(exc)


def set_priority(pid: int, key: str) -> str:
    try:
        psutil.Process(pid).nice(_priority_map()[key])
        return ""
    except psutil.AccessDenied:
        return "Отказано в доступе (нужны права администратора)."
    except (psutil.Error, KeyError, OSError) as exc:
        return str(exc)


def apply_rules(rules: list[dict[str, Any]]) -> list[str]:
    """Правила: {'name': 'app.exe', 'action': 'block'|'priority', 'priority': 'below'}.
    Возвращает список выполненных действий для журнала."""
    if not rules:
        return []
    by_name = {r["name"].lower(): r for r in rules if r.get("name")}
    done: list[str] = []
    pmap = _priority_map()
    for p in psutil.process_iter(["pid", "name", "nice"]):
        rule = by_name.get((p.info.get("name") or "").lower())
        if not rule:
            continue
        try:
            if rule.get("action") == "block":
                if p.info["name"].lower() in CRITICAL:
                    continue
                p.kill()
                done.append(f"Заблокирован запуск {p.info['name']} (PID {p.info['pid']})")
            elif rule.get("action") == "priority":
                target = pmap.get(rule.get("priority", "below"))
                if target is not None and p.info.get("nice") != target:
                    p.nice(target)
                    done.append(f"Приоритет {p.info['name']} → {PRIORITY_NAMES.get(rule.get('priority', ''), '')}")
        except (psutil.Error, OSError):
            continue
    return done


# ----------------------------------------------------------------- память
def trim_working_sets() -> tuple[int, int]:
    """Сбрасывает рабочие наборы процессов (EmptyWorkingSet). Возвращает (успешно, всего)."""
    if not IS_WINDOWS:
        return 0, 0
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.K32EmptyWorkingSet.argtypes = [wintypes.HANDLE]
    k32.K32EmptyWorkingSet.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    access = 0x0100 | 0x0400  # PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION
    ok = total = 0
    for pid in psutil.pids():
        if pid in (0, 4):
            continue
        total += 1
        h = k32.OpenProcess(access, False, pid)
        if not h:
            continue
        try:
            if k32.K32EmptyWorkingSet(h):
                ok += 1
        finally:
            k32.CloseHandle(h)
    return ok, total
