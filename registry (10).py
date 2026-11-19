"""Поиск устаревших записей реестра с резервным копированием перед исправлением.

Сканируются только категории с низким риском: ссылки на несуществующие файлы.
Перед любым удалением ключ экспортируется в .reg-файл (восстановление — двойной щелчок).
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .startup import extract_path, resolve_exists
from .winutil import (
    IS_WINDOWS,
    backups_dir,
    expand,
    reg_delete_tree,
    reg_delete_value,
    reg_export,
    reg_get,
    reg_subkeys,
    reg_values,
)

log = logging.getLogger(__name__)


@dataclass
class RegIssue:
    category: str
    hive: str
    key: str
    value: str | None  # None — удаляется весь ключ
    target: str        # путь, на который ссылается запись
    description: str

    @property
    def location(self) -> str:
        return f"{self.hive}\\{self.key}" + (f" → {self.value}" if self.value is not None else "")


def _missing(path: str) -> bool:
    path = expand(path.strip().strip('"'))
    if not path or not re.match(r"^[a-zA-Z]:\\", path):
        return False  # относительные/сетевые/особые пути не трогаем
    if path[0].upper() not in _local_drives():
        return False  # съёмный/отключённый диск — не считаем ошибкой
    return not os.path.exists(path)


_DRIVES: set[str] | None = None


def _local_drives() -> set[str]:
    global _DRIVES
    if _DRIVES is None:
        import psutil
        _DRIVES = {p.mountpoint[0].upper() for p in psutil.disk_partitions(all=False)
                   if p.mountpoint and "removable" not in p.opts and "cdrom" not in p.opts}
    return _DRIVES


def _scan_uninstall() -> list[RegIssue]:
    out = []
    for hive, base in (("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                       ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                       ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Uninstall")):
        for sub in reg_subkeys(hive, base):
            key = f"{base}\\{sub}"
            name = reg_get(hive, key, "DisplayName")
            cmd = str(reg_get(hive, key, "UninstallString", "") or "")
            if not name or not cmd or "msiexec" in cmd.lower():
                continue
            exe = extract_path(cmd)
            loc = str(reg_get(hive, key, "InstallLocation", "") or "")
            if _missing(exe) and (not loc or _missing(loc)):
                out.append(RegIssue("Устаревшие записи программ", hive, key, None, exe,
                                    f"«{name}»: деинсталлятор не найден"))
    return out


def _scan_app_paths() -> list[RegIssue]:
    out = []
    for hive in ("HKLM", "HKCU"):
        base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
        for sub in reg_subkeys(hive, base):
            key = f"{base}\\{sub}"
            target = str(reg_get(hive, key, "", "") or "")
            if target and _missing(target):
                out.append(RegIssue("Пути приложений (App Paths)", hive, key, None, target,
                                    f"{sub} указывает на отсутствующий файл"))
    return out


def _scan_shared_dlls() -> list[RegIssue]:
    out = []
    for key in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\SharedDLLs",
                r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\SharedDLLs"):
        for name, _v, _t in reg_values("HKLM", key):
            if _missing(name):
                out.append(RegIssue("Общие библиотеки (SharedDLLs)", "HKLM", key, name, name,
                                    "Ссылка на отсутствующую библиотеку"))
    return out


def _scan_run() -> list[RegIssue]:
    out = []
    for hive, key in (("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run"),
                      ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"),
                      ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run")):
        for name, value, _t in reg_values(hive, key):
            path = extract_path(str(value))
            if path and os.path.isabs(expand(path)) and not resolve_exists(expand(path)) and _missing(path):
                out.append(RegIssue("Автозагрузка", hive, key, name, path, f"«{name}»: файл программы удалён"))
    return out


def _scan_muicache() -> list[RegIssue]:
    out = []
    key = r"Software\Classes\Local Settings\Software\Microsoft\Windows\Shell\MuiCache"
    for name, _v, _t in reg_values("HKCU", key):
        path = re.sub(r"\.(FriendlyAppName|ApplicationCompany)$", "", name)
        if _missing(path):
            out.append(RegIssue("Кэш MUI (имена программ)", "HKCU", key, name, path, "Программа больше не существует"))
    return out


def _scan_installer_folders() -> list[RegIssue]:
    out = []
    key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Installer\Folders"
    for name, _v, _t in reg_values("HKLM", key):
        if _missing(name):
            out.append(RegIssue("Папки установщика", "HKLM", key, name, name, "Папка не существует"))
    return out


def _scan_fonts() -> list[RegIssue]:
    out = []
    fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    user_fonts = os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\Windows\Fonts")
    for hive in ("HKLM", "HKCU"):
        key = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
        for name, value, _t in reg_values(hive, key):
            fname = str(value)
            if not fname or any(c in fname for c in "|,"):
                continue
            path =fname if os.path.isabs(fname) else os.path.join(fonts_dir, fname)
            if not os.path.isabs(fname) and not os.path.exists(path):
                path = os.path.join(user_fonts, fname)
            if _missing(path):
                out.append(RegIssue("Шрифты", hive, key, name, path, f"Файл шрифта «{fname}» не найден"))
    return out


SCANNERS = [
    ("uninstall", "Устаревшие записи программ", _scan_uninstall),
    ("apppaths", "Пути приложений", _scan_app_paths),
    ("shared", "Общие библиотеки", _scan_shared_dlls),
    ("run", "Автозагрузка", _scan_run),
    ("mui", "Кэш MUI", _scan_muicache),
    ("installer", "Папки установщика", _scan_installer_folders),
    ("fonts", "Шрифты", _scan_fonts),
]


def scan(enabled: set[str] | None = None) -> list[RegIssue]:
    if not IS_WINDOWS:
        return []
    issues: list[RegIssue] = []
    for sid, _title, fn in SCANNERS:
        if enabled is not None and sid not in enabled:
            continue
        try:
            issues.extend(fn())
        except Exception:
            log.exception("Ошибка сканера реестра %s", sid)
    return issues


def fix(issues: list[RegIssue]) -> tuple[int, list[str], Path]:
    """Экспортирует затронутые ключи и удаляет записи. Возвращает (исправлено, ошибки, папка копий)."""
    folder = backups_dir() / f"registry_{time.strftime('%Y%m%d_%H%M%S')}"
    folder.mkdir(parents=True, exist_ok=True)
    exported: dict[tuple[str, str], bool] = {}
    fixed = 0
    errors: list[str] = []
    for idx, issue in enumerate(issues):
        ident = (issue.hive, issue.key)
        if ident not in exported:
            exported[ident] = reg_export(issue.hive, issue.key, folder / f"{idx:04d}.reg")
        if not exported[ident]:
            errors.append(f"{issue.location}: не удалось создать резервную копию — пропущено")
            continue
        ok = reg_delete_value(issue.hive, issue.key, issue.value) if issue.value is not None \
            else reg_delete_tree(issue.hive, issue.key)
        if ok:
            fixed += 1
        else:
            errors.append(f"{issue.location}: нет доступа (запустите от имени администратора)")
    return fixed, errors, folder
