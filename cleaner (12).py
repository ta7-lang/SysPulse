"""Глубокая очистка: временные файлы, кэши системы, браузеров и приложений, дампы, корзина."""
from __future__ import annotations

import ctypes
import fnmatch
import glob
import heapq
import logging
import os
import stat
import time
from collections.abc import Callable, Iterator
from ctypes import wintypes
from dataclasses import dataclass, field

from .winutil import IS_WINDOWS, expand, powershell, run

log = logging.getLogger(__name__)

ProgressFn = Callable[[str], None]


@dataclass
class Target:
    root: str                 # может содержать * для профилей
    patterns: tuple[str, ...] = ("*",)
    recursive: bool = True
    min_age_hours: float = 0  # не трогать свежие файлы (могут использоваться)


@dataclass
class Category:
    id: str
    group: str
    name: str
    description: str
    targets: list[Target] = field(default_factory=list)
    default: bool = True
    admin: bool = False
    process_names: tuple[str, ...] = ()  # если процесс запущен — часть файлов будет занята
    special: str = ""                    # recycle | dns | dism | delivery


def _chromium(group: str, cid: str, name: str, base: str, procs: tuple[str, ...]) -> Category:
    prof = os.path.join(base, "*")
    subdirs = ("Cache", "Code Cache", "GPUCache", "Service Worker\\CacheStorage", "Service Worker\\ScriptCache",
               "DawnCache", "DawnGraphiteCache", "DawnWebGPUCache", "GrShaderCache")
    targets = [Target(os.path.join(prof, s)) for s in subdirs]
    targets += [Target(os.path.join(base, s)) for s in ("ShaderCache", "GrShaderCache", "component_crx_cache")]
    return Category(cid, group, name, "Кэш страниц, скриптов и шейдеров. История, пароли и вкладки не затрагиваются.",
                    targets, process_names=procs)


def build_categories() -> list[Category]:
    la = os.environ.get("LOCALAPPDATA", expand("~\\AppData\\Local"))
    ra = os.environ.get("APPDATA", expand("~\\AppData\\Roaming"))
    win = os.environ.get("WINDIR", r"C:\Windows")
    pd = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    tmp = os.environ.get("TEMP", os.path.join(la, "Temp"))
    S, B, A, P = "Система", "Браузеры", "Приложения", "Конфиденциальность"
    cats = [
        Category("user_temp", S, "Временные файлы пользователя", "Папка %TEMP%. Файлы моложе 2 часов пропускаются.",
                 [Target(tmp, min_age_hours=2)]),
        Category("win_temp", S, "Временные файлы Windows", "C:\\Windows\\Temp.",
                 [Target(os.path.join(win, "Temp"), min_age_hours=2)], admin=True),
        Category("thumbs", S, "Кэш эскизов и значков", "thumbcache_*.db / iconcache_*.db. Пересоздаются автоматически.",
                 [Target(os.path.join(la, r"Microsoft\Windows\Explorer"), ("thumbcache_*.db", "iconcache_*.db"), False)]),
        Category("shader", S, "Кэш шейдеров DirectX и видеодрайверов",
                 "D3DSCache, NVIDIA DXCache/GLCache, AMD DxCache. Пересоздаётся при запуске игр.",
                 [Target(os.path.join(la, "D3DSCache")), Target(os.path.join(la, r"NVIDIA\DXCache")),
                  Target(os.path.join(la, r"NVIDIA\GLCache")), Target(os.path.join(la, r"AMD\DxCache")),
                  Target(os.path.join(la, r"AMD\DxcCache")), Target(os.path.join(la, r"AMD\GLCache")),
                  Target(os.path.join(la, r"Intel\ShaderCache"))]),
        Category("wu_cache", S, "Кэш загрузок Windows Update",
                 "SoftwareDistribution\\Download — уже установленные пакеты обновлений.",
                 [Target(os.path.join(win, r"SoftwareDistribution\Download"), min_age_hours=24)], admin=True),
        Category("delivery", S, "Кэш оптимизации доставки", "Файлы, раздаваемые другим ПК при обновлении.",
                 [], admin=True, special="delivery"),
        Category("crash", S, "Дампы памяти и отчёты об ошибках",
                 "Minidump, MEMORY.DMP, CrashDumps, отчёты WER.",
                 [Target(os.path.join(win, "Minidump")), Target(win, ("MEMORY.DMP",), False),
                  Target(os.path.join(la, "CrashDumps")), Target(os.path.join(pd, r"Microsoft\Windows\WER\ReportArchive")),
                  Target(os.path.join(pd, r"Microsoft\Windows\WER\ReportQueue")),
                  Target(os.path.join(la, r"Microsoft\Windows\WER"))], admin=True),
        Category("logs", S, "Журналы установки и обслуживания", "*.log в Windows\\Logs, Panther, старые журналы CBS/DISM.",
                 [Target(os.path.join(win, "Logs"), ("*.log", "*.etl", "*.cab"), True, 24),
                  Target(os.path.join(win, "Panther"), ("*.log",), True, 24)], admin=True),
        Category("prefetch", S, "Prefetch", "Данные ускорения запуска программ. Очистка временно замедлит первый запуск.",
                 [Target(os.path.join(win, "Prefetch"), ("*.pf",), False)], default=False, admin=True),
        Category("recycle", S, "Корзина", "Окончательное удаление файлов из корзины на всех дисках.", [],
                 special="recycle"),
        Category("dns", S, "Кэш DNS", "Сброс кэша DNS-резолвера (ipconfig /flushdns).", [], special="dns"),
        Category("dism", S, "Хранилище компонентов WinSxS (DISM)",
                 "Удаление заменённых версий компонентов. Может занять 5–15 минут.", [], default=False, admin=True,
                 special="dism"),
        _chromium(B, "chrome", "Google Chrome", os.path.join(la, r"Google\Chrome\User Data"), ("chrome.exe",)),
        _chromium(B, "edge", "Microsoft Edge", os.path.join(la, r"Microsoft\Edge\User Data"), ("msedge.exe",)),
        _chromium(B, "yandex", "Яндекс Браузер", os.path.join(la, r"Yandex\YandexBrowser\User Data"), ("browser.exe",)),
        _chromium(B, "brave", "Brave", os.path.join(la, r"BraveSoftware\Brave-Browser\User Data"), ("brave.exe",)),
        _chromium(B, "vivaldi", "Vivaldi", os.path.join(la, r"Vivaldi\User Data"), ("vivaldi.exe",)),
        Category("opera", B, "Opera / Opera GX", "Кэш страниц и шейдеров.",
                 [Target(os.path.join(la, r"Opera Software\*\Cache")),
                  Target(os.path.join(ra, r"Opera Software\*\Code Cache")),
                  Target(os.path.join(ra, r"Opera Software\*\GPUCache"))], process_names=("opera.exe",)),
        Category("firefox", B, "Mozilla Firefox", "Кэш cache2 и миниатюры. Закладки и пароли не затрагиваются.",
                 [Target(os.path.join(la, r"Mozilla\Firefox\Profiles\*\cache2")),
                  Target(os.path.join(la, r"Mozilla\Firefox\Profiles\*\thumbnails")),
                  Target(os.path.join(la, r"Mozilla\Firefox\Profiles\*\startupCache"))],
                 process_names=("firefox.exe",)),
        Category("discord", A, "Discord", "Кэш изображений и кода.",
                 [Target(os.path.join(ra, r"discord\Cache")), Target(os.path.join(ra, r"discord\Code Cache")),
                  Target(os.path.join(ra, r"discord\GPUCache"))], process_names=("discord.exe",)),
        Category("steam", A, "Steam", "Кэш встроенного браузера и шейдеров интерфейса.",
                 [Target(os.path.join(la, r"Steam\htmlcache"))], process_names=("steam.exe", "steamwebhelper.exe")),
        Category("teams", A, "Microsoft Teams", "Кэш классического и нового Teams.",
                 [Target(os.path.join(ra, r"Microsoft\Teams\Cache")), Target(os.path.join(ra, r"Microsoft\Teams\Code Cache")),
                  Target(os.path.join(ra, r"Microsoft\Teams\GPUCache")),
                  Target(os.path.join(la, r"Packages\MSTeams_8wekyb3d8bbwe\LocalCache\Microsoft\MSTeams\EBWebView\*\Cache"))],
                 process_names=("teams.exe", "ms-teams.exe")),
        Category("spotify", A, "Spotify", "Кэш треков (скачанные для офлайна не удаляются).",
                 [Target(os.path.join(la, r"Spotify\Data")), Target(os.path.join(la, r"Spotify\Browser\Cache"))],
                 process_names=("spotify.exe",)),
        Category("vscode", A, "Visual Studio Code", "Кэш интерфейса и расширений.",
                 [Target(os.path.join(ra, r"Code\Cache")), Target(os.path.join(ra, r"Code\CachedData")),
                  Target(os.path.join(ra, r"Code\Code Cache")), Target(os.path.join(ra, r"Code\GPUCache"))],
                 default=False, process_names=("code.exe",)),
        Category("telegram", A, "Telegram Desktop", "Временные файлы emoji/медиа-кэша.",
                 [Target(os.path.join(ra, r"Telegram Desktop\tdata\emoji")),
                  Target(os.path.join(ra, r"Telegram Desktop\tdata\user_data\cache"))],
                 default=False, process_names=("telegram.exe",)),
        Category("recent", P, "Недавние документы", "Список последних открытых файлов (ярлыки в Recent).",
                 [Target(os.path.join(ra, r"Microsoft\Windows\Recent"), ("*.lnk",), False)], default=False),
        Category("jumplists", P, "Списки переходов", "Недавние файлы в контекстном меню панели задач.",
                 [Target(os.path.join(ra, r"Microsoft\Windows\Recent\AutomaticDestinations")),
                  Target(os.path.join(ra, r"Microsoft\Windows\Recent\CustomDestinations"))], default=False),
    ]
    return cats


# ----------------------------------------------------------------- обход файлов
def _is_link(entry: os.DirEntry[str]) -> bool:
    try:
        if entry.is_symlink():
            return True
        isjunction = getattr(entry, "is_junction", None)
        return bool(isjunction and isjunction())
    except OSError:
        return True


def _iter_files(target: Target) -> Iterator[tuple[str, int, float]]:
    roots = glob.glob(target.root) if any(c in target.root for c in "*?[") else [target.root]
    now = time.time()
    min_age = target.min_age_hours * 3600
    for root in roots:
        if not os.path.isdir(root):
            continue
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        if _is_link(entry):
                            continue
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if target.recursive:
                                    stack.append(entry.path)
                                continue
                            if not any(fnmatch.fnmatch(entry.name.lower(), p.lower()) for p in target.patterns):
                                continue
                            st = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if min_age and now - st.st_mtime < min_age:
                            continue
                        yield entry.path, st.st_size, st.st_mtime
            except OSError:
                continue


def _remove_empty_dirs(root: str) -> None:
    for r in (glob.glob(root) if any(c in root for c in "*?[") else [root]):
        for dirpath, _dirs, _files in os.walk(r, topdown=False):
            if os.path.normcase(dirpath) == os.path.normcase(r):
                continue
            try:
                os.rmdir(dirpath)
            except OSError:
                pass


# ----------------------------------------------------------------- корзина
_PACK = 1 if ctypes.sizeof(ctypes.c_void_p) == 4 else 8  # shellapi.h: pshpack1 только для Win32


class _SHQUERYRBINFO(ctypes.Structure):
    _pack_ = _PACK
    _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]


def recycle_bin_size() -> tuple[int, int]:
    if not IS_WINDOWS:
        return 0, 0
    info = _SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(info)
    if ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) == 0:
        return int(info.i64Size), int(info.i64NumItems)
    return 0, 0


def empty_recycle_bin() -> bool:
    if not IS_WINDOWS:
        return False
    # SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
    res = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4)
    return res in (0, -2147418113)  # S_OK или E_UNEXPECTED (корзина уже пуста)


# ----------------------------------------------------------------- сканирование / очистка
def running_processes(names: tuple[str, ...]) -> list[str]:
    if not names:
        return []
    import psutil
    low = {n.lower() for n in names}
    found = set()
    for p in psutil.process_iter(["name"]):
        n = (p.info.get("name") or "").lower()
        if n in low:
            found.add(p.info["name"])
    return sorted(found)


def scan_category(cat: Category) -> tuple[int, int]:
    """(байт, файлов)"""
    if cat.special == "recycle":
        return recycle_bin_size()
    if cat.special == "delivery":
        out = powershell("(Get-DeliveryOptimizationStatus | Measure-Object -Property FileSize -Sum).Sum", 60)[1]
        try:
            return int(float(out.strip() or 0)), 0
        except ValueError:
            return 0, 0
    if cat.special in ("dns", "dism"):
        return 0, 0
    total = count = 0
    for t in cat.targets:
        for _path, size, _mtime in _iter_files(t):
            total += size
            count += 1
    return total, count


def clean_category(cat: Category, progress: ProgressFn | None = None) -> tuple[int, int, int]:
    """Удаляет файлы. Возвращает (освобождено байт, удалено файлов, пропущено занятых)."""
    if cat.special == "recycle":
        size, n = recycle_bin_size()
        return (size, n, 0) if empty_recycle_bin() else (0, 0, n)
    if cat.special == "dns":
        run(["ipconfig", "/flushdns"], timeout=30)
        return 0, 0, 0
    if cat.special == "delivery":
        before = scan_category(cat)[0]
        powershell("Delete-DeliveryOptimizationCache -Force", 300)
        return max(before - scan_category(cat)[0], 0), 0, 0
    if cat.special == "dism":
        if progress:
            progress("DISM: очистка хранилища компонентов (может занять несколько минут)…")
        code, out = run(["dism.exe", "/Online", "/Cleanup-Image", "/StartComponentCleanup"], timeout=3600)
        if code != 0 and progress:
            progress("DISM завершился с ошибкой: " + out[-300:])
        return 0, 0, 0
    freed = deleted = skipped = 0
    for t in cat.targets:
        for path, size, _mtime in list(_iter_files(t)):
            try:
                os.remove(path)
            except PermissionError:
                try:
                    os.chmod(path, stat.S_IWRITE)
                    os.remove(path)
                except OSError:
                    skipped += 1
                    continue
            except OSError:
                skipped += 1
                continue
            freed += size
            deleted += 1
            if progress and deleted % 500 == 0:
                progress(f"{cat.name}: удалено {deleted} файлов…")
        if t.recursive:
            _remove_empty_dirs(t.root)
    return freed, deleted, skipped


# ----------------------------------------------------------------- большие файлы
def find_large_files(root: str, min_mb: int = 200, limit: int = 200,
                     progress: ProgressFn | None = None, should_stop: Callable[[], bool] | None = None
                     ) -> list[tuple[int, str, float]]:
    """Топ самых больших файлов: (размер, путь, mtime)."""
    heap: list[tuple[int, str, float]] = []
    min_size = min_mb * 1024 * 1024
    skip_dirs = {"$recycle.bin", "system volume information", "windows", "$winreagent", "winsxs"}
    scanned = 0
    stack = [root]
    while stack:
        if should_stop and should_stop():
            break
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    if _is_link(entry):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name.lower() not in skip_dirs:
                                stack.append(entry.path)
                            continue
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    scanned += 1
                    if progress and scanned % 20000 == 0:
                        progress(f"Просмотрено файлов: {scanned:,}".replace(",", " "))
                    if st.st_size >= min_size:
                        item = (st.st_size, entry.path, st.st_mtime)
                        if len(heap) < limit:
                            heapq.heappush(heap, item)
                        else:
                            heapq.heappushpop(heap, item)
        except OSError:
            continue
    return sorted(heap, reverse=True)


def delete_to_recycle_bin(path: str) -> bool:
    """Перемещает файл в корзину (SHFileOperationW + FOF_ALLOWUNDO)."""
    if not IS_WINDOWS:
        try:
            os.remove(path)
            return True
        except OSError:
            return False

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _pack_ = _PACK
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", ctypes.c_void_p),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    op = SHFILEOPSTRUCTW()
    op.wFunc = 3  # FO_DELETE
    buf = ctypes.create_unicode_buffer(path, len(path) + 2)  # список путей завершается двойным нулём
    op.pFrom = ctypes.addressof(buf)
    op.fFlags = 0x40 | 0x10 | 0x400 | 0x4  # ALLOWUNDO | NOCONFIRMATION | NOERRORUI | SILENT
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not os.path.exists(path)
