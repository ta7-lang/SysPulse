"""Минимальная обёртка над Windows PDH (счётчики производительности) через ctypes.

Используется для данных, которых нет в psutil: загрузка GPU, видеопамять,
активность дисков и реальная частота процессора.
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

from .winutil import IS_WINDOWS

log = logging.getLogger(__name__)

PDH_FMT_DOUBLE = 0x00000200
PDH_FMT_NOCAP100 = 0x00008000
PDH_MORE_DATA = 0x800007D2
PDH_CSTATUS_VALID_DATA = 0x0
PDH_CSTATUS_NEW_DATA = 0x1


class _FmtValueUnion(ctypes.Union):
    _fields_ = [
        ("longValue", ctypes.c_long),
        ("doubleValue", ctypes.c_double),
        ("largeValue", ctypes.c_longlong),
        ("AnsiStringValue", ctypes.c_char_p),
        ("WideStringValue", ctypes.c_wchar_p),
    ]


class PDH_FMT_COUNTERVALUE(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("u", _FmtValueUnion)]


class PDH_FMT_COUNTERVALUE_ITEM_W(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", PDH_FMT_COUNTERVALUE)]


def _ok(status: int) -> bool:
    return status in (PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA)


class PdhQuery:
    """Набор счётчиков PDH. Все методы безопасны: при ошибке возвращают None/пусто."""

    def __init__(self) -> None:
        self._query = ctypes.c_void_p()
        self._counters: dict[str, ctypes.c_void_p] = {}
        self._pdh = None
        self.available = False
        if not IS_WINDOWS:
            return
        try:
            self._pdh = ctypes.WinDLL("pdh.dll")
            self._pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)]
            self._pdh.PdhOpenQueryW.restype = wintypes.DWORD
            self._pdh.PdhAddEnglishCounterW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_size_t,
                                                        ctypes.POINTER(ctypes.c_void_p)]
            self._pdh.PdhAddEnglishCounterW.restype = wintypes.DWORD
            self._pdh.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
            self._pdh.PdhCollectQueryData.restype = wintypes.DWORD
            self._pdh.PdhGetFormattedCounterValue.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                                              ctypes.POINTER(wintypes.DWORD),
                                                              ctypes.POINTER(PDH_FMT_COUNTERVALUE)]
            self._pdh.PdhGetFormattedCounterValue.restype = wintypes.DWORD
            self._pdh.PdhGetFormattedCounterArrayW.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                                               ctypes.POINTER(wintypes.DWORD),
                                                               ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
            self._pdh.PdhGetFormattedCounterArrayW.restype = wintypes.DWORD
            self._pdh.PdhCloseQuery.argtypes = [ctypes.c_void_p]
            self._pdh.PdhCloseQuery.restype = wintypes.DWORD
            status = self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query))
            self.available = status == 0
        except (OSError, AttributeError):
            log.exception("PDH недоступен")
            self.available = False

    def add(self, key: str, path: str) -> bool:
        if not self.available or self._pdh is None:
            return False
        handle = ctypes.c_void_p()
        status = self._pdh.PdhAddEnglishCounterW(self._query, path, 0, ctypes.byref(handle))
        if status != 0:
            log.debug("PDH: счётчик %s недоступен (0x%08X)", path, status)
            return False
        self._counters[key] = handle
        return True

    def has(self, key: str) -> bool:
        return key in self._counters

    def collect(self) -> bool:
        if not self.available or self._pdh is None:
            return False
        return self._pdh.PdhCollectQueryData(self._query) == 0

    def value(self, key: str) -> float | None:
        handle = self._counters.get(key)
        if handle is None or self._pdh is None:
            return None
        val = PDH_FMT_COUNTERVALUE()
        status = self._pdh.PdhGetFormattedCounterValue(handle, PDH_FMT_DOUBLE | PDH_FMT_NOCAP100, None,
                                                       ctypes.byref(val))
        if status != 0 or not _ok(val.CStatus):
            return None
        return float(val.u.doubleValue)

    def array(self, key: str) -> dict[str, float]:
        handle = self._counters.get(key)
        if handle is None or self._pdh is None:
            return {}
        size = wintypes.DWORD(0)
        count = wintypes.DWORD(0)
        fmt = PDH_FMT_DOUBLE | PDH_FMT_NOCAP100
        status = self._pdh.PdhGetFormattedCounterArrayW(handle, fmt, ctypes.byref(size), ctypes.byref(count), None)
        if status != PDH_MORE_DATA or size.value == 0:
            return {}
        buf = ctypes.create_string_buffer(size.value)
        status = self._pdh.PdhGetFormattedCounterArrayW(handle, fmt, ctypes.byref(size), ctypes.byref(count), buf)
        if status != 0:
            return {}
        items = ctypes.cast(buf, ctypes.POINTER(PDH_FMT_COUNTERVALUE_ITEM_W))
        result: dict[str, float] = {}
        for i in range(count.value):
            item = items[i]
            if _ok(item.FmtValue.CStatus) and item.szName:
                result[item.szName] = result.get(item.szName, 0.0) + float(item.FmtValue.u.doubleValue)
        return result

    def close(self) -> None:
        if self.available and self._pdh is not None:
            try:
                self._pdh.PdhCloseQuery(self._query)
            except OSError:
                pass
        self.available = False
        self._counters.clear()
