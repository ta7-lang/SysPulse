"""Установленные программы (ключи Uninstall) и их удаление."""
from __future__ import annotations

import os
import re
from typing import Any

from .winutil import IS_WINDOWS, popen_detached, reg_get, reg_subkeys

UNINSTALL_KEYS = [
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
]


def _fmt_date(raw: Any) -> str:
    s = str(raw or "")
    if re.fullmatch(r"\d{8}", s):
        return f"{s[6:8]}.{s[4:6]}.{s[0:4]}"
    return s


def list_programs() -> list[dict[str, Any]]:
    if not IS_WINDOWS:
        return []
    seen: set[tuple[str, str]] = set()
    rows: list[dict[str, Any]] = []
    for hive, base in UNINSTALL_KEYS:
        for sub in reg_subkeys(hive, base):
            key = f"{base}\\{sub}"
            name = reg_get(hive, key, "DisplayName")
            if not name:
                continue
            if reg_get(hive, key, "SystemComponent") == 1 or reg_get(hive, key, "ParentKeyName"):
                continue
            version = str(reg_get(hive, key, "DisplayVersion", "") or "")
            ident = (str(name).lower(), version)
            if ident in seen:
                continue
            seen.add(ident)
            size_kb = reg_get(hive, key, "EstimatedSize")
            location = str(reg_get(hive, key, "InstallLocation", "") or "").strip('"')
            rows.append({
                "name": str(name),
                "publisher": str(reg_get(hive, key, "Publisher", "") or ""),
                "version": version,
                "date": _fmt_date(reg_get(hive, key, "InstallDate")),
                "size": int(size_kb) * 1024 if isinstance(size_kb, int) else 0,
                "location": location,
                "uninstall": str(reg_get(hive, key, "UninstallString", "") or ""),
                "quiet": str(reg_get(hive, key, "QuietUninstallString", "") or ""),
                "hive": hive,
                "key": key,
                "broken": bool(location) and not os.path.isdir(location),
                "scope": "Пользователь" if hive == "HKCU" else ("32-бит" if "WOW6432Node" in base else "Все"),
            })
    rows.sort(key=lambda r: r["name"].lower())
    return rows


def uninstall(program: dict[str, Any]) -> str:
    cmd = program.get("uninstall") or ""
    if not cmd:
        return "У программы нет команды удаления."
    m = re.match(r"(?i)\s*\"?msiexec(\.exe)?\"?\s+/[ix]\s*(\{[0-9A-F-]+\})", cmd)
    if m:
        cmd = f"msiexec.exe /x {m.group(2)}"
    # ShellExecute корректно показывает запрос UAC, если деинсталлятору нужны права
    from .startup import extract_path
    cmd = os.path.expandvars(cmd.strip())
    exe = extract_path(cmd)
    rest = cmd
    rest = rest[len(exe) + 2:] if rest.startswith('"') else rest[len(exe):]
    try:
        import ctypes
        rc = ctypes.windll.shell32.ShellExecuteW(None, "open", os.path.expandvars(exe), rest.strip(), None, 1)
        if int(rc) > 32:
            return ""
    except (OSError, AttributeError):
        pass
    if not popen_detached(cmd):
        return "Не удалось запустить деинсталлятор."
    return ""
