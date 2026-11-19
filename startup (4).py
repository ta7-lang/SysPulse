"""Автозагрузка: реестр Run, папки «Автозагрузка», задания планировщика.

Отключение работает так же, как в Диспетчере задач (ключи StartupApproved),
поэтому полностью обратимо. Удаление делается с резервной копией.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import struct
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .winutil import (
    IS_WINDOWS,
    as_list,
    backups_dir,
    expand,
    powershell,
    ps_json,
    reg_delete_value,
    reg_get,
    reg_get_typed,
    reg_set,
    reg_values,
)

log = logging.getLogger(__name__)

APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"

# (улей, ключ Run, подпись, ключ StartupApproved или None)
REG_LOCATIONS = [
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run", "Реестр: HKCU Run", APPROVED + r"\Run"),
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", "Реестр: HKLM Run", APPROVED + r"\Run"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run", "Реестр: HKLM Run (32-бит)",
     APPROVED + r"\Run32"),
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\RunOnce", "Реестр: HKCU RunOnce", None),
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce", "Реестр: HKLM RunOnce", None),
]


@dataclass
class StartupItem:
    name: str
    command: str
    location: str
    kind: str  # reg | folder | task
    hive: str = ""
    key: str = ""
    approved_key: str | None = None
    enabled: bool = True
    path: str = ""
    exists: bool = True
    signer: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def uid(self) -> str:
        return f"{self.kind}|{self.hive}|{self.key}|{self.name}"


# ----------------------------------------------------------------- разбор командной строки
def extract_path(command: str) -> str:
    """Извлекает путь к исполняемому файлу из командной строки автозапуска."""
    cmd = expand(command.strip())
    if not cmd:
        return ""
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        return cmd[1:end] if end > 0 else cmd[1:]
    low = cmd.lower()
    for ext in (".exe", ".bat", ".cmd", ".com", ".lnk", ".vbs", ".js", ".ps1", ".dll"):
        idx = low.find(ext + " ")
        if idx < 0 and low.endswith(ext):
            idx = len(low) - len(ext)
        if idx >= 0:
            candidate = cmd[: idx + len(ext)]
            return candidate
    try:
        return shlex.split(cmd, posix=False)[0]
    except ValueError:
        return cmd.split(" ")[0]


def resolve_exists(path: str) -> bool:
    if not path:
        return False
    if os.path.isabs(path):
        return os.path.exists(path)
    # относительное имя — ищем в PATH и System32
    from shutil import which
    return which(path) is not None or os.path.exists(os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                                                                   "System32", path))


# ----------------------------------------------------------------- StartupApproved
def approved_enabled(data: Any) -> bool:
    """Первый байт: 0x02/0x06 — включено, 0x03/0x07 (нечётный) — отключено."""
    if isinstance(data, (bytes, bytearray)) and data:
        return not (data[0] & 0x01)
    return True


def approved_value(enabled: bool) -> bytes:
    if enabled:
        return b"\x02" + b"\x00" * 11
    # FILETIME (100-нс интервалы с 1601-01-01)
    filetime = int((time.time() + 11644473600) * 10_000_000)
    return b"\x03\x00\x00\x00" + struct.pack("<Q", filetime)


# ----------------------------------------------------------------- перечисление
def _startup_folders() -> list[tuple[str, str, str, str]]:
    appdata = os.environ.get("APPDATA", "")
    progdata = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    return [
        ("HKCU", os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Startup"),
         "Папка автозагрузки (пользователь)", APPROVED + r"\StartupFolder"),
        ("HKLM", os.path.join(progdata, r"Microsoft\Windows\Start Menu\Programs\StartUp"),
         "Папка автозагрузки (все)", APPROVED + r"\StartupFolder"),
    ]


def _resolve_shortcuts(paths: list[str]) -> dict[str, str]:
    if not paths or not IS_WINDOWS:
        return {}
    arr = ",".join("'" + p.replace("'", "''") + "'" for p in paths)
    data = ps_json(
        "$sh = New-Object -ComObject WScript.Shell; "
        f"@({arr}) | ForEach-Object {{ @{{ p = $_; t = $sh.CreateShortcut($_).TargetPath; "
        "a = $sh.CreateShortcut($_).Arguments } }", timeout=30)
    return {d["p"]: (d.get("t") or "") + ((" " + d["a"]) if d.get("a") else "") for d in as_list(data)
            if isinstance(d, dict) and d.get("p")}


def list_items(include_tasks: bool = True) -> list[StartupItem]:
    items: list[StartupItem] = []
    if not IS_WINDOWS:
        return items
    for hive, key, label, approved in REG_LOCATIONS:
        for name, value, _vtype in reg_values(hive, key):
            if not name:
                continue
            cmd = str(value)
            path = extract_path(cmd)
            enabled = True
            if approved:
                enabled = approved_enabled(reg_get(hive, approved, name))
            items.append(StartupItem(name=name, command=cmd, location=label, kind="reg", hive=hive, key=key,
                                     approved_key=approved, enabled=enabled, path=path,
                                     exists=resolve_exists(path)))
    lnks: list[str] = []
    folder_items: list[StartupItem] = []
    for hive, folder, label, approved in _startup_folders():
        if not os.path.isdir(folder):
            continue
        for entry in os.scandir(folder):
            if not entry.is_file() or entry.name.lower() == "desktop.ini":
                continue
            item = StartupItem(name=entry.name, command=entry.path, location=label, kind="folder", hive=hive,
                               key=folder, approved_key=approved, path=entry.path,
                               enabled=approved_enabled(reg_get(hive, approved, entry.name)))
            if entry.name.lower().endswith(".lnk"):
                lnks.append(entry.path)
            folder_items.append(item)
    targets = _resolve_shortcuts(lnks)
    for item in folder_items:
        if item.command in targets and targets[item.command]:
            item.command = targets[item.command]
            item.path = extract_path(item.command)
            item.exists = resolve_exists(item.path)
    items.extend(folder_items)
    if include_tasks:
        items.extend(list_tasks())
    return items


_TASK_SCRIPT = r"""
Get-ScheduledTask | Where-Object {
  $_.TaskPath -notlike '\Microsoft\*' -and
  ($_.Triggers | Where-Object { $_.CimClass.CimClassName -in 'MSFT_TaskLogonTrigger','MSFT_TaskBootTrigger' })
} | ForEach-Object {
  $a = $_.Actions | Select-Object -First 1
  @{ name = $_.TaskName; path = $_.TaskPath; state = [string]$_.State;
     exe = [string]$a.Execute; args = [string]$a.Arguments; author = [string]$_.Author }
}
"""


def list_tasks() -> list[StartupItem]:
    data = ps_json("@(" + _TASK_SCRIPT + ")", timeout=60)
    items = []
    for d in as_list(data):
        if not isinstance(d, dict):
            continue
        exe = expand(d.get("exe") or "").strip('"')
        cmd = (exe + " " + (d.get("args") or "")).strip()
        items.append(StartupItem(
            name=d.get("name", ""), command=cmd, location="Планировщик заданий" + (d.get("path") or "\\"),
            kind="task", key=d.get("path") or "\\", enabled=(d.get("state") or "").lower() != "disabled",
            path=exe, exists=resolve_exists(exe) if exe else True, extra={"author": d.get("author", "")},
        ))
    return items


# ----------------------------------------------------------------- действия
def set_enabled(item: StartupItem, enabled: bool) -> str:
    """Возвращает пустую строку при успехе или текст ошибки."""
    try:
        if item.kind == "task":
            full = (item.key.rstrip("\\") + "\\" + item.name)
            verb = "Enable-ScheduledTask" if enabled else "Disable-ScheduledTask"
            safe_path = item.key.replace("'", "''")
            safe_name = item.name.replace("'", "''")
            code, out = powershell(f"{verb} -TaskPath '{safe_path}' -TaskName '{safe_name}' | Out-Null; "
                                   "if($?){'OK'}else{'FAIL'}", timeout=60)
            return "" if "OK" in out else f"Не удалось изменить задание {full}: {out or code}"
        if not item.approved_key:
            return "Этот тип записи (RunOnce) нельзя отключить — только удалить."
        reg_set(item.hive, item.approved_key, item.name, approved_value(enabled))
        item.enabled = enabled
        return ""
    except PermissionError:
        return "Нужны права администратора."
    except OSError as exc:
        return str(exc)


def delete_item(item: StartupItem) -> str:
    """Удаляет запись с сохранением резервной копии в JSON."""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = backups_dir() / f"startup_{stamp}.json"
    record: dict[str, Any] = asdict(item)
    try:
        if item.kind == "reg":
            typed = reg_get_typed(item.hive, item.key, item.name)
            if typed is not None:
                value, vtype = typed
                if isinstance(value, (bytes, bytearray)):
                    record["reg_value_hex"] = bytes(value).hex()
                else:
                    record["reg_value"] = value
                record["reg_type"] = vtype
            backup.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            if not reg_delete_value(item.hive, item.key, item.name):
                return "Не удалось удалить значение реестра (нужны права администратора?)."
            if item.approved_key:
                reg_delete_value(item.hive, item.approved_key, item.name)
            return ""
        if item.kind == "folder":
            src = Path(item.key) / item.name
            dst_dir = backups_dir() / f"startup_files_{stamp}"
            dst_dir.mkdir(exist_ok=True)
            record["backup_file"] = str(dst_dir / item.name)
            backup.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            _move(src, dst_dir / item.name)
            return ""
        if item.kind == "task":
            backup.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            code, out = powershell(
                f"Export-ScheduledTask -TaskPath '{item.key.replace(chr(39), chr(39) * 2)}' "
                f"-TaskName '{item.name.replace(chr(39), chr(39) * 2)}' | Out-File -Encoding utf8 "
                f"'{str(backup.with_suffix('.xml')).replace(chr(39), chr(39) * 2)}'; "
                f"Unregister-ScheduledTask -TaskPath '{item.key.replace(chr(39), chr(39) * 2)}' "
                f"-TaskName '{item.name.replace(chr(39), chr(39) * 2)}' -Confirm:$false; if($?){{'OK'}}",
                timeout=60)
            return "" if "OK" in out else f"Не удалось удалить задание: {out or code}"
    except PermissionError:
        return "Нужны права администратора."
    except OSError as exc:
        return str(exc)
    return "Неизвестный тип записи"


def _move(src: Path, dst: Path) -> None:
    import shutil
    shutil.move(str(src), str(dst))


def restore_backup(file: Path) -> str:
    """Восстанавливает запись автозагрузки из JSON-копии, созданной delete_item."""
    try:
        record = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"Не удалось прочитать копию: {exc}"
    try:
        if record.get("kind") == "reg" and ("reg_value" in record or "reg_value_hex" in record):
            value = bytes.fromhex(record["reg_value_hex"]) if "reg_value_hex" in record else record["reg_value"]
            reg_set(record["hive"], record["key"], record["name"], value, record.get("reg_type"))
            return ""
        if record.get("kind") == "folder" and record.get("backup_file"):
            _move(Path(record["backup_file"]), Path(record["key"]) / record["name"])
            return ""
        if record.get("kind") == "task":
            xml = file.with_suffix(".xml")
            if not xml.exists():
                return "XML-копия задания не найдена."
            code, out = powershell(
                f"Register-ScheduledTask -Xml (Get-Content -Raw '{str(xml).replace(chr(39), chr(39) * 2)}') "
                f"-TaskName '{record['name'].replace(chr(39), chr(39) * 2)}' "
                f"-TaskPath '{record['key'].replace(chr(39), chr(39) * 2)}' | Out-Null; if($?){{'OK'}}", timeout=60)
            return "" if "OK" in out else f"Ошибка: {out or code}"
    except (OSError, KeyError) as exc:
        return str(exc)
    return "Копия не содержит данных для восстановления."
