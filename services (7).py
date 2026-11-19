"""Службы Windows: список, запуск/остановка, тип запуска, рекомендации."""
from __future__ import annotations

import logging
from typing import Any

import psutil

from .winutil import IS_WINDOWS, run

log = logging.getLogger(__name__)

START_TYPES = {
    "automatic": "Автоматически",
    "manual": "Вручную",
    "disabled": "Отключена",
}
STATUS_RU = {
    "running": "Работает",
    "stopped": "Остановлена",
    "paused": "Приостановлена",
    "start_pending": "Запускается",
    "stop_pending": "Останавливается",
    "continue_pending": "Возобновляется",
    "pause_pending": "Приостанавливается",
}

# Службы, которые часто можно отключить без вреда. (рекомендация, пояснение, риск)
RECOMMENDATIONS: dict[str, tuple[str, str, str]] = {
    "DiagTrack": ("disabled", "Телеметрия Microsoft (Connected User Experiences). Отправляет диагностику.", "низкий"),
    "dmwappushservice": ("disabled", "Маршрутизация push-сообщений WAP, используется телеметрией.", "низкий"),
    "WerSvc": ("manual", "Отчёты об ошибках Windows. Достаточно ручного запуска.", "низкий"),
    "Fax": ("disabled", "Факс. Нужен только при наличии факс-модема.", "низкий"),
    "XblAuthManager": ("manual", "Xbox Live: авторизация. Нужна для игр Xbox/Game Pass.", "средний"),
    "XblGameSave": ("manual", "Xbox Live: сохранения в облаке.", "средний"),
    "XboxNetApiSvc": ("manual", "Xbox Live: сетевая служба.", "средний"),
    "XboxGipSvc": ("manual", "Аксессуары Xbox.", "средний"),
    "MapsBroker": ("disabled", "Диспетчер скачанных карт.", "низкий"),
    "lfsvc": ("manual", "Служба геолокации.", "низкий"),
    "RetailDemo": ("disabled", "Демонстрационный режим для магазинов.", "низкий"),
    "RemoteRegistry": ("disabled", "Удалённый реестр — потенциальный риск безопасности.", "низкий"),
    "SysMain": ("manual", "SuperFetch. На SSD польза мала, на HDD — лучше оставить.", "средний"),
    "WSearch": ("manual", "Индексирование поиска. Отключение замедлит поиск в Пуске/Проводнике.", "средний"),
    "PrintNotify": ("manual", "Уведомления принтера. Не нужна без принтера.", "низкий"),
    "Spooler": ("manual", "Диспетчер печати. Без принтера можно не запускать автоматически.", "средний"),
    "TabletInputService": ("manual", "Сенсорная клавиатура и рукописный ввод.", "средний"),
    "WbioSrvc": ("manual", "Биометрия. Нужна для Windows Hello по отпечатку/лицу.", "средний"),
    "PhoneSvc": ("manual", "Телефонная связь.", "низкий"),
    "wisvc": ("disabled", "Программа предварительной оценки Windows (Insider).", "низкий"),
    "TrkWks": ("manual", "Клиент отслеживания изменившихся связей (NTFS-ярлыки по сети).", "низкий"),
    "SEMgrSvc": ("manual", "Платежи и NFC.", "низкий"),
    "WMPNetworkSvc": ("disabled", "Общий доступ к библиотекам Windows Media Player.", "низкий"),
}

PROTECTED = {"RpcSs", "RpcEptMapper", "DcomLaunch", "LSM", "Winmgmt", "EventLog", "PlugPlay", "Power",
             "ProfSvc", "Schedule", "SamSs", "BFE", "MpsSvc", "WinDefend", "CryptSvc", "Dhcp", "Dnscache",
             "nsi", "AudioSrv", "AudioEndpointBuilder", "Themes", "UserManager", "CoreMessagingRegistrar",
             "BrokerInfrastructure", "SystemEventsBroker", "TimeBrokerSvc", "StateRepository", "gpsvc",
             "wscsvc", "SecurityHealthService", "WdNisSvc", "mpssvc", "EventSystem", "LanmanWorkstation"}


def list_services() -> list[dict[str, Any]]:
    if not IS_WINDOWS:
        return []
    rows: list[dict[str, Any]] = []
    for svc in psutil.win_service_iter():  # type: ignore[attr-defined]
        try:
            d = svc.as_dict()
        except (psutil.Error, OSError):
            continue
        name = d.get("name") or ""
        rec = RECOMMENDATIONS.get(name)
        rows.append({
            "name": name,
            "display": d.get("display_name") or name,
            "status": STATUS_RU.get(d.get("status") or "", d.get("status") or ""),
            "raw_status": d.get("status") or "",
            "start": START_TYPES.get(d.get("start_type") or "", d.get("start_type") or ""),
            "raw_start": d.get("start_type") or "",
            "pid": d.get("pid") or "",
            "user": d.get("username") or "",
            "binpath": d.get("binpath") or "",
            "description": d.get("description") or "",
            "recommend": START_TYPES.get(rec[0], "") if rec else "",
            "recommend_raw": rec[0] if rec else "",
            "note": rec[1] if rec else "",
            "risk": rec[2] if rec else "",
            "protected": name in PROTECTED,
        })
    return rows


def _sc(*args: str) -> str:
    code, out = run(["sc.exe", *args], timeout=60)
    if code == 0:
        return ""
    if code == 5 or "1062" in out or "5:" in out:
        return out or "Отказано в доступе (нужны права администратора)."
    return out or f"Ошибка sc.exe (код {code})"


def start(name: str) -> str:
    err = _sc("start", name)
    return "" if (not err or "1056" in err) else err  # 1056 — уже запущена


def stop(name: str) -> str:
    if name in PROTECTED:
        return "Служба защищена: её остановка может нарушить работу Windows."
    err = _sc("stop", name)
    return "" if (not err or "1062" in err) else err  # 1062 — не запущена


def set_start_type(name: str, mode: str) -> str:
    """mode: auto | delayed-auto | demand | disabled"""
    if name in PROTECTED and mode == "disabled":
        return "Служба защищена: отключать её нельзя."
    return _sc("config", name, "start=", mode)


def apply_recommendations(names: list[str]) -> list[str]:
    """Применяет рекомендованный тип запуска. Возвращает ошибки."""
    errors = []
    for n in names:
        rec = RECOMMENDATIONS.get(n)
        if not rec:
            continue
        mode = "demand" if rec[0] == "manual" else rec[0]
        err = set_start_type(n, mode)
        if err:
            errors.append(f"{n}: {err}")
        elif mode == "disabled":
            stop(n)
    return errors
