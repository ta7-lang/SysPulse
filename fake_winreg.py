"""Имитация модуля winreg в памяти — позволяет тестировать код реестра на любой ОС."""
from __future__ import annotations

from typing import Any

HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER, HKEY_CLASSES_ROOT, HKEY_USERS = 1, 2, 3, 4
KEY_READ, KEY_WRITE, KEY_SET_VALUE, KEY_ALL_ACCESS, KEY_WOW64_64KEY = 0x20019, 0x20006, 0x2, 0xF003F, 0x100
REG_SZ, REG_EXPAND_SZ, REG_BINARY, REG_DWORD, REG_MULTI_SZ, REG_QWORD = 1, 2, 3, 4, 7, 11


class _Key:
    def __init__(self) -> None:
        self.values: dict[str, tuple[Any, int]] = {}
        self.subkeys: dict[str, _Key] = {}


class _Handle:
    def __init__(self, key: _Key) -> None:
        self.key = key

    def __enter__(self) -> _Handle:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


ROOTS: dict[int, _Key] = {}


def reset() -> None:
    ROOTS.clear()
    for h in (HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER, HKEY_CLASSES_ROOT, HKEY_USERS):
        ROOTS[h] = _Key()


reset()


def _walk(root: Any, path: str, create: bool) -> _Key:
    node = root.key if isinstance(root, _Handle) else ROOTS[root]
    for part in [p for p in path.split("\\") if p]:
        low = {k.lower(): k for k in node.subkeys}
        if part.lower() in low:
            node = node.subkeys[low[part.lower()]]
        elif create:
            node.subkeys[part] = _Key()
            node = node.subkeys[part]
        else:
            raise FileNotFoundError(2, "Не удаётся найти указанный файл", path)
    return node


def OpenKey(root: Any, path: str, reserved: int = 0, access: int = KEY_READ) -> _Handle:  # noqa: N802
    return _Handle(_walk(root, path, False))


def CreateKeyEx(root: Any, path: str, reserved: int = 0, access: int = KEY_WRITE) -> _Handle:  # noqa: N802
    return _Handle(_walk(root, path, True))


def QueryValueEx(h: _Handle, name: str) -> tuple[Any, int]:  # noqa: N802
    for k, v in h.key.values.items():
        if k.lower() == name.lower():
            return v
    raise FileNotFoundError(2, "value not found", name)


def SetValueEx(h: _Handle, name: str, reserved: int, vtype: int, value: Any) -> None:  # noqa: N802
    if vtype == REG_DWORD and not (0 <= int(value) <= 0xFFFFFFFF):
        raise OverflowError("DWORD out of range")
    h.key.values[name] = (value, vtype)


def EnumValue(h: _Handle, i: int) -> tuple[str, Any, int]:  # noqa: N802
    items = list(h.key.values.items())
    if i >= len(items):
        raise OSError(259, "No more data")
    name, (val, t) = items[i]
    return name, val, t


def EnumKey(h: _Handle, i: int) -> str:  # noqa: N802
    keys = list(h.key.subkeys)
    if i >= len(keys):
        raise OSError(259, "No more data")
    return keys[i]


def DeleteValue(h: _Handle, name: str) -> None:  # noqa: N802
    for k in list(h.key.values):
        if k.lower() == name.lower():
            del h.key.values[k]
            return
    raise FileNotFoundError(2, "value not found", name)


def DeleteKeyEx(root: Any, path: str, access: int = KEY_WOW64_64KEY, reserved: int = 0) -> None:  # noqa: N802
    parent_path, _, leaf = path.rpartition("\\")
    parent = _walk(root, parent_path, False)
    target = _walk(root, path, False)
    if target.subkeys:
        raise PermissionError(5, "Access denied: key has subkeys")
    for k in list(parent.subkeys):
        if k.lower() == leaf.lower():
            del parent.subkeys[k]
