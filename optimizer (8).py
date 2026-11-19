"""Оптимизация: твики реестра (обратимые), схемы электропитания, освобождение памяти,
системные инструменты."""
from __future__ import annotations

import ctypes
import logging
import re
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

import psutil

from .winutil import IS_WINDOWS, powershell, reg_delete_value, reg_get, reg_set, run

log = logging.getLogger(__name__)


# ======================================================================= твики
@dataclass
class RegValue:
    hive: str
    key: str
    name: str
    on: Any            # значение во включённом состоянии
    off: Any = None    # None — удалить значение при отключении
    kind: str = "dword"  # dword | sz


@dataclass
class Tweak:
    id: str
    group: str
    title: str
    description: str
    values: list[RegValue]
    admin: bool = False
    reboot: bool = False
    apply_hook: Callable[[bool], None] | None = None

    def state(self) -> bool | None:
        """True — твик применён, False — нет, None — неизвестно (нет доступа)."""
        if not IS_WINDOWS:
            return None
        results = []
        for v in self.values:
            cur = reg_get(v.hive, v.key, v.name)
            results.append(cur is not None and str(cur) == str(v.on))
        return all(results) if results else None

    def apply(self, enable: bool) -> str:
        try:
            import winreg
            for v in self.values:
                target = v.on if enable else v.off
                if target is None:
                    reg_delete_value(v.hive, v.key, v.name)
                elif v.kind == "sz":
                    reg_set(v.hive, v.key, v.name, str(target), winreg.REG_SZ)
                else:
                    reg_set(v.hive, v.key, v.name, int(target) & 0xFFFFFFFF, winreg.REG_DWORD)
            if self.apply_hook:
                self.apply_hook(enable)
            return ""
        except PermissionError:
            return "Нужны права администратора."
        except (OSError, ImportError) as exc:
            return str(exc)


def _hibernate_hook(enable: bool) -> None:
    run(["powercfg", "/hibernate", "off" if enable else "on"], timeout=30)


CDM = r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
ADV = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
MM = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"

TWEAKS: list[Tweak] = [
    Tweak("visual_fx", "Интерфейс", "Визуальные эффекты: макс. производительность",
          "Отключает анимации и тени окон. Заметно ускоряет слабые ПК.",
          [RegValue("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects", "VisualFXSetting", 2, 0),
           RegValue("HKCU", r"Control Panel\Desktop\WindowMetrics", "MinAnimate", "0", "1", "sz"),
           RegValue("HKCU", ADV, "TaskbarAnimations", 0, 1)]),
    Tweak("transparency", "Интерфейс", "Отключить прозрачность", "Эффект «акрил» нагружает видеокарту.",
          [RegValue("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "EnableTransparency", 0, 1)]),
    Tweak("menu_delay", "Интерфейс", "Мгновенное открытие меню", "Задержка показа подменю 400 → 50 мс.",
          [RegValue("HKCU", r"Control Panel\Desktop", "MenuShowDelay", "50", "400", "sz")]),
    Tweak("startup_delay", "Загрузка", "Убрать задержку автозапуска",
          "Windows искусственно откладывает запуск программ автозагрузки; твик убирает паузу.",
          [RegValue("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize", "StartupDelayInMSec", 0)]),
    Tweak("bg_apps", "Фоновые процессы", "Запретить фоновую работу приложений Store",
          "Приложения из Microsoft Store не будут работать в фоне.",
          [RegValue("HKCU", r"Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications", "GlobalUserDisabled", 1, 0),
           RegValue("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Search", "BackgroundAppGlobalToggle", 0, 1)]),
    Tweak("ads", "Фоновые процессы", "Отключить рекламу, советы и автоустановку приложений",
          "Предложения в Пуске, советы Windows и тихая установка рекламируемых приложений.",
          [RegValue("HKCU", CDM, "SystemPaneSuggestionsEnabled", 0, 1),
           RegValue("HKCU", CDM, "SilentInstalledAppsEnabled", 0, 1),
           RegValue("HKCU", CDM, "SubscribedContent-338389Enabled", 0, 1),
           RegValue("HKCU", CDM, "SubscribedContent-338388Enabled", 0, 1),
           RegValue("HKCU", CDM, "SoftLandingEnabled", 0, 1)]),
    Tweak("telemetry", "Фоновые процессы", "Минимальная телеметрия",
          "Групповая политика AllowTelemetry = 0 (для Home/Pro фактически «обязательные данные»).",
          [RegValue("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\DataCollection", "AllowTelemetry", 0, None)], admin=True),
    Tweak("game_mode", "Игры", "Игровой режим Windows", "Приоритет ресурсов для активной игры.",
          [RegValue("HKCU", r"Software\Microsoft\GameBar", "AutoGameModeEnabled", 1, 0)]),
    Tweak("game_dvr", "Игры", "Отключить фоновую запись Game DVR", "Xbox Game Bar не будет записывать фон, освобождая GPU.",
          [RegValue("HKCU", r"System\GameConfigStore", "GameDVR_Enabled", 0, 1),
           RegValue("HKCU", r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled", 0, 1)]),
    Tweak("net_throttle", "Игры", "Отключить сетевое регулирование мультимедиа",
          "NetworkThrottlingIndex = FFFFFFFF, SystemResponsiveness = 10. Снижает задержки в онлайн-играх.",
          [RegValue("HKLM", MM, "NetworkThrottlingIndex", 0xFFFFFFFF, 10),
           RegValue("HKLM", MM, "SystemResponsiveness", 10, 20)], admin=True),
    Tweak("hibernate", "Диск", "Отключить гибернацию",
          "Удаляет hiberfil.sys (освобождает несколько ГБ). Быстрый запуск Windows тоже отключится.",
          [RegValue("HKLM", r"SYSTEM\CurrentControlSet\Control\Power", "HibernateEnabled", 0, 1)],
          admin=True, apply_hook=_hibernate_hook),
    Tweak("ntfs_lastaccess", "Диск", "Не обновлять время последнего доступа NTFS",
          "Сокращает лишние записи на диск.",
          [RegValue("HKLM", r"SYSTEM\CurrentControlSet\Control\FileSystem", "NtfsDisableLastAccessUpdate", 0x80000001,
                    0x80000002)], admin=True),
    Tweak("autoend", "Завершение работы", "Быстрое завершение зависших программ",
          "Автоматически закрывать зависшие приложения при выключении, тайм-аут 2 с.",
          [RegValue("HKCU", r"Control Panel\Desktop", "AutoEndTasks", "1", "0", "sz"),
           RegValue("HKCU", r"Control Panel\Desktop", "HungAppTimeout", "2000", "5000", "sz"),
           RegValue("HKCU", r"Control Panel\Desktop", "WaitToKillAppTimeout", "2000", "20000", "sz")]),
]


# ======================================================================= электропитание
ULTIMATE_GUID = "e9a42b02-d5df-448d-aa00-03f14749eb61"


def power_plans() -> list[dict[str, Any]]:
    if not IS_WINDOWS:
        return []
    _code, out = run(["powercfg", "/list"], timeout=30)
    plans = []
    for line in out.splitlines():
        m = re.search(r"([0-9a-fA-F-]{36})\s+\((.+?)\)\s*(\*)?", line)
        if m:
            plans.append({"guid": m.group(1), "name": m.group(2), "active": bool(m.group(3))})
    return plans


def set_power_plan(guid: str) -> str:
    code, out = run(["powercfg", "/setactive", guid], timeout=30)
    return "" if code == 0 else out


def add_ultimate_plan() -> str:
    code, out = run(["powercfg", "-duplicatescheme", ULTIMATE_GUID], timeout=30)
    return "" if code == 0 else out or "Схема недоступна в этой редакции Windows."


# ======================================================================= память
class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", _LUID), ("Attributes", wintypes.DWORD)]


class _TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Privileges", _LUID_AND_ATTRIBUTES * 1)]


def _enable_privilege(name: str) -> bool:
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(_LUID)]
    advapi.AdjustTokenPrivileges.argtypes = [wintypes.HANDLE, wintypes.BOOL, ctypes.POINTER(_TOKEN_PRIVILEGES),
                                             wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(k32.GetCurrentProcess(), 0x0020 | 0x0008, ctypes.byref(token)):
        return False
    try:
        luid = _LUID()
        if not advapi.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            return False
        tp = _TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid
        tp.Privileges[0].Attributes = 0x2  # SE_PRIVILEGE_ENABLED
        if not advapi.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None):
            return False
        return ctypes.get_last_error() == 0  # ERROR_NOT_ALL_ASSIGNED = 1300
    finally:
        k32.CloseHandle(token)


def purge_standby_list() -> str:
    """Очищает список ожидания (standby) — как RAMMap/ISLC. Нужны права администратора."""
    if not IS_WINDOWS:
        return "Только для Windows."
    if not _enable_privilege("SeProfileSingleProcessPrivilege"):
        return "Нужны права администратора."
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtSetSystemInformation.argtypes = [ctypes.c_int, ctypes.c_void_p, wintypes.ULONG]
    ntdll.NtSetSystemInformation.restype = ctypes.c_long
    for command in (3, 4):  # MemoryFlushModifiedList, MemoryPurgeStandbyList
        cmd = ctypes.c_int(command)
        status = ntdll.NtSetSystemInformation(80, ctypes.byref(cmd), ctypes.sizeof(cmd))  # SystemMemoryListInformation
        if status != 0 and command == 4:
            return f"NtSetSystemInformation вернул 0x{status & 0xFFFFFFFF:08X}"
    return ""


def free_memory() -> tuple[int, list[str]]:
    """Освобождает ОЗУ: сброс рабочих наборов + очистка standby. Возвращает (байт, заметки)."""
    from .processes import trim_working_sets
    before = psutil.virtual_memory().available
    notes = []
    ok, total = trim_working_sets()
    notes.append(f"Сжаты рабочие наборы {ok} из {total} процессов")
    err = purge_standby_list()
    notes.append("Очищен список ожидания (standby)" if not err else f"Standby: {err}")
    time.sleep(1.0)
    after = psutil.virtual_memory().available
    return max(after - before, 0), notes


# ======================================================================= инструменты
def flush_dns() -> str:
    code, out = run(["ipconfig", "/flushdns"], timeout=30)
    return "" if code == 0 else out


def restart_explorer() -> str:
    run(["taskkill", "/f", "/im", "explorer.exe"], timeout=30)
    time.sleep(1.0)
    from .winutil import popen_detached
    return "" if popen_detached(["explorer.exe"]) else "Не удалось запустить explorer.exe"


def rebuild_icon_cache() -> str:
    import glob
    import os
    run(["ie4uinit.exe", "-show"], timeout=30)
    run(["taskkill", "/f", "/im", "explorer.exe"], timeout=30)
    time.sleep(1.0)
    folder = os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\Windows\Explorer")
    for f in glob.glob(os.path.join(folder, "iconcache_*.db")) + glob.glob(os.path.join(folder, "thumbcache_*.db")):
        try:
            os.remove(f)
        except OSError:
            pass
    from .winutil import popen_detached
    popen_detached(["explorer.exe"])
    return ""


def optimize_drives() -> tuple[bool, str]:
    """TRIM для SSD и дефрагментация для HDD (Optimize-Volume выбирает сам)."""
    code, out = powershell(
        "Get-Volume | Where-Object { $_.DriveLetter -and $_.DriveType -eq 'Fixed' -and $_.FileSystem -eq 'NTFS' } | "
        "ForEach-Object { $d = $_.DriveLetter; Optimize-Volume -DriveLetter $d -Verbose 4>&1 | "
        "Select-Object -Last 1 | ForEach-Object { \"${d}: $_\" } }", timeout=7200)
    return code == 0, out or "Готово"


def create_restore_point(description: str = "SysPulse") -> tuple[bool, str]:
    safe = description.replace("'", "''")
    code, out = powershell(
        "Enable-ComputerRestore -Drive \"$env:SystemDrive\\\"; "
        f"Checkpoint-Computer -Description '{safe}' -RestorePointType MODIFY_SETTINGS -ErrorAction Stop; 'OK'",
        timeout=600)
    if "OK" in out:
        return True, "Точка восстановления создана."
    return False, out or f"Ошибка (код {code}). Windows разрешает одну точку в 24 часа; нужны права администратора."


def network_reset() -> str:
    errors = []
    for cmd in (["netsh", "winsock", "reset"], ["netsh", "int", "ip", "reset"], ["ipconfig", "/flushdns"]):
        code, out = run(cmd, timeout=60)
        if code != 0:
            errors.append(f"{' '.join(cmd)}: {out[-200:]}")
    return "\n".join(errors)
