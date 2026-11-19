"""Общие системные утилиты: права администратора, запуск команд, PowerShell, реестр."""
from __future__ import annotations

import base64
import ctypes
import json
import logging
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000

if sys.platform == "win32":
    import winreg
else:  # pragma: no cover - на других ОС модуль недоступен
    winreg = None


# ----------------------------------------------------------------- права
def is_admin() -> bool:
    if IS_WINDOWS:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)


def relaunch_as_admin() -> bool:
    """Перезапускает программу с запросом UAC. Возвращает True, если запуск принят."""
    if not IS_WINDOWS:
        return False
    if getattr(sys, "frozen", False):
        exe = sys.executable
        params = subprocess.list2cmdline(sys.argv[1:])
    else:
        exe = sys.executable
        script = os.path.abspath(sys.argv[0])
        params = subprocess.list2cmdline([script, *sys.argv[1:]])
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    except Exception:
        log.exception("ShellExecuteW runas failed")
        return False
    return int(rc) > 32


def run_elevated_console(command: str, keep_open: bool = True) -> bool:
    """Открывает видимую консоль администратора (для SFC, DISM, CHKDSK и т.п.)."""
    if not IS_WINDOWS:
        return False
    flag = "/k" if keep_open else "/c"
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", f'{flag} {command}', None, 1)
        return int(rc) > 32
    except Exception:
        log.exception("run_elevated_console failed")
        return False


# ----------------------------------------------------------------- команды
def _oem_encoding() -> str:
    if IS_WINDOWS:
        try:
            return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
        except Exception:
            pass
    return "utf-8"


def decode_output(data: bytes | None) -> str:
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(_oem_encoding(), errors="replace")


def run(cmd: list[str] | str, timeout: float = 120) -> tuple[int, str]:
    """Запускает процесс без окна, возвращает (код, вывод stdout+stderr)."""
    kwargs: dict[str, Any] = {"capture_output": True, "timeout": timeout}
    if IS_WINDOWS:
        kwargs["creationflags"] = CREATE_NO_WINDOW
    try:
        proc = subprocess.run(cmd, check=False, **kwargs)
    except FileNotFoundError as exc:
        return 127, str(exc)
    except subprocess.TimeoutExpired:
        return 124, "Превышено время ожидания"
    except OSError as exc:
        return 1, str(exc)
    out = decode_output(proc.stdout)
    err = decode_output(proc.stderr)
    return proc.returncode, (out + ("\n" + err if err.strip() else "")).strip()


def popen_detached(cmd: list[str] | str) -> bool:
    try:
        kwargs: dict[str, Any] = {}
        if IS_WINDOWS:
            kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
        subprocess.Popen(cmd, **kwargs)
        return True
    except OSError:
        log.exception("popen_detached failed: %s", cmd)
        return False


_PS_PREFIX = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    "$ProgressPreference='SilentlyContinue';$ErrorActionPreference='SilentlyContinue';"
)


def powershell_cmd(script: str) -> list[str]:
    encoded = base64.b64encode((_PS_PREFIX + script).encode("utf-16-le")).decode("ascii")
    return ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]


def powershell(script: str, timeout: float = 120) -> tuple[int, str]:
    if not IS_WINDOWS:
        return 1, "PowerShell доступен только в Windows"
    return run(powershell_cmd(script), timeout=timeout)


def ps_json(script: str, timeout: float = 120, depth: int = 4) -> Any:
    """Выполняет скрипт PowerShell и разбирает JSON-вывод. None при ошибке."""
    # & { … } — блок скрипта допускает несколько операторов (в отличие от круглых скобок)
    code, out = powershell(f"& {{\n{script}\n}} | ConvertTo-Json -Depth {depth} -Compress", timeout)
    if not out:
        return None
    # отбрасываем возможный мусор перед JSON
    start = min((i for i in (out.find("{"), out.find("[")) if i >= 0), default=-1)
    if start < 0:
        return None
    try:
        return json.loads(out[start:])
    except json.JSONDecodeError:
        log.debug("ps_json: invalid JSON (code %s): %s", code, out[:300])
        return None


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


# ----------------------------------------------------------------- проводник
def open_in_explorer(path: str) -> None:
    if not path:
        return
    if IS_WINDOWS:
        if os.path.isfile(path):
            popen_detached(["explorer.exe", f"/select,{os.path.normpath(path)}"])
        elif os.path.isdir(path):
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
        else:
            parent = os.path.dirname(path)
            if os.path.isdir(parent):
                os.startfile(parent)  # type: ignore[attr-defined]  # noqa: S606
    else:
        popen_detached(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)])


def expand(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path))


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA") if IS_WINDOWS else os.path.expanduser("~/.config")
    p = Path(base or os.path.expanduser("~")) / "SysPulse"
    p.mkdir(parents=True, exist_ok=True)
    return p


def backups_dir() -> Path:
    p = app_data_dir() / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ----------------------------------------------------------------- реестр
HIVES: dict[str, int] = {}
if IS_WINDOWS:
    HIVES = {
        "HKLM": winreg.HKEY_LOCAL_MACHINE,
        "HKCU": winreg.HKEY_CURRENT_USER,
        "HKCR": winreg.HKEY_CLASSES_ROOT,
        "HKU": winreg.HKEY_USERS,
    }

HIVE_LONG = {
    "HKLM": "HKEY_LOCAL_MACHINE",
    "HKCU": "HKEY_CURRENT_USER",
    "HKCR": "HKEY_CLASSES_ROOT",
    "HKU": "HKEY_USERS",
}


def _view() -> int:
    return winreg.KEY_WOW64_64KEY if IS_WINDOWS else 0


def reg_get(hive: str, key: str, name: str, default: Any = None) -> Any:
    if not IS_WINDOWS:
        return default
    try:
        with winreg.OpenKey(HIVES[hive], key, 0, winreg.KEY_READ | _view()) as k:
            value, _type = winreg.QueryValueEx(k, name)
            return value
    except OSError:
        return default


def reg_get_typed(hive: str, key: str, name: str) -> tuple[Any, int] | None:
    if not IS_WINDOWS:
        return None
    try:
        with winreg.OpenKey(HIVES[hive], key, 0, winreg.KEY_READ | _view()) as k:
            value, vtype = winreg.QueryValueEx(k, name)
            return value, vtype
    except OSError:
        return None


def reg_set(hive: str, key: str, name: str, value: Any, vtype: int | None = None) -> None:
    """Записывает значение (создаёт ключ при необходимости). Бросает OSError."""
    if not IS_WINDOWS:
        raise OSError("Реестр доступен только в Windows")
    if vtype is None:
        if isinstance(value, int):
            vtype = winreg.REG_DWORD
        elif isinstance(value, (bytes, bytearray)):
            vtype = winreg.REG_BINARY
        else:
            vtype = winreg.REG_SZ
    with winreg.CreateKeyEx(HIVES[hive], key, 0, winreg.KEY_WRITE | _view()) as k:
        winreg.SetValueEx(k, name, 0, vtype, value)


def reg_delete_value(hive: str, key: str, name: str) -> bool:
    if not IS_WINDOWS:
        return False
    try:
        with winreg.OpenKey(HIVES[hive], key, 0, winreg.KEY_SET_VALUE | _view()) as k:
            winreg.DeleteValue(k, name)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def reg_delete_tree(hive: str, key: str) -> bool:
    """Рекурсивно удаляет ключ."""
    if not IS_WINDOWS:
        return False
    try:
        with winreg.OpenKey(HIVES[hive], key, 0, winreg.KEY_ALL_ACCESS | _view()) as k:
            while True:
                try:
                    sub = winreg.EnumKey(k, 0)
                except OSError:
                    break
                if not reg_delete_tree(hive, f"{key}\\{sub}"):
                    return False
        winreg.DeleteKeyEx(HIVES[hive], key, _view(), 0)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        log.exception("reg_delete_tree %s\\%s", hive, key)
        return False


def reg_values(hive: str, key: str) -> Iterator[tuple[str, Any, int]]:
    if not IS_WINDOWS:
        return
    try:
        with winreg.OpenKey(HIVES[hive], key, 0, winreg.KEY_READ | _view()) as k:
            i = 0
            while True:
                try:
                    yield winreg.EnumValue(k, i)
                except OSError:
                    break
                i += 1
    except OSError:
        return


def reg_subkeys(hive: str, key: str) -> list[str]:
    if not IS_WINDOWS:
        return []
    result: list[str] = []
    try:
        with winreg.OpenKey(HIVES[hive], key, 0, winreg.KEY_READ | _view()) as k:
            i = 0
            while True:
                try:
                    result.append(winreg.EnumKey(k, i))
                except OSError:
                    break
                i += 1
    except OSError:
        pass
    return result


def reg_export(hive: str, key: str, target: Path) -> bool:
    """Резервная копия ключа в .reg-файл через reg.exe."""
    code, _ = run(["reg", "export", f"{HIVE_LONG[hive]}\\{key}", str(target), "/y"], timeout=60)
    return code == 0


def reg_import(file: Path) -> tuple[bool, str]:
    code, out = run(["reg", "import", str(file)], timeout=60)
    return code == 0, out


# ----------------------------------------------------------------- форматирование
def human_bytes(n: float | int | None, digits: int = 1) -> str:
    if n is None:
        return "—"
    value = float(n)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if abs(value) < 1024 or unit == "ТБ":
            return f"{value:.0f} {unit}" if unit == "Б" else f"{value:.{digits}f} {unit}"
        value /= 1024
    return f"{value:.1f} ТБ"


def human_rate(bps: float | None) -> str:
    return "—" if bps is None else human_bytes(bps) + "/с"


def human_duration(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d:
        return f"{d} д {h} ч {m} мин"
    if h:
        return f"{h} ч {m} мин"
    return f"{m} мин"
