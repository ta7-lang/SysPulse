"""Настройки и скины (JSON в %APPDATA%\\SysPulse)."""
from __future__ import annotations

import copy
import json
import logging
import os
import sys
from typing import Any

from .winutil import IS_WINDOWS, app_data_dir, reg_delete_value, reg_set

log = logging.getLogger(__name__)

SKINS: dict[str, dict[str, str]] = {
    "Afterburner Red": {
        "bg": "#0a0a0c", "panel": "#131316", "panel2": "#1b1b20", "border": "#2b2b33", "accent": "#e3172d",
        "accent2": "#ff5266", "text": "#e9e9ec", "muted": "#8b8b96", "graph_bg": "#060607", "grid": "#1d1d24",
        "good": "#38d27a", "warn": "#ffb020", "bad": "#ff3b3b",
    },
    "Cyber Blue": {
        "bg": "#070b10", "panel": "#0e141c", "panel2": "#141d28", "border": "#22303f", "accent": "#00b4ff",
        "accent2": "#5fd3ff", "text": "#e3f2ff", "muted": "#7f93a8", "graph_bg": "#04070a", "grid": "#122030",
        "good": "#2ee6a8", "warn": "#ffc247", "bad": "#ff4d6a",
    },
    "Toxic Green": {
        "bg": "#080b07", "panel": "#10150e", "panel2": "#171f14", "border": "#26331f", "accent": "#6cff2e",
        "accent2": "#b2ff8a", "text": "#e8f7e1", "muted": "#83947a", "graph_bg": "#050704", "grid": "#152012",
        "good": "#6cff2e", "warn": "#ffd23f", "bad": "#ff4f3f",
    },
    "Amber Dragon": {
        "bg": "#0c0905", "panel": "#16110a", "panel2": "#1f180e", "border": "#3a2a14", "accent": "#ff8c1a",
        "accent2": "#ffc069", "text": "#fff1de", "muted": "#a08a6c", "graph_bg": "#080603", "grid": "#21180c",
        "good": "#9ee34f", "warn": "#ffd23f", "bad": "#ff4b2b",
    },
    "Violet Night": {
        "bg": "#09070d", "panel": "#120e19", "panel2": "#1a1424", "border": "#2e2440", "accent": "#a259ff",
        "accent2": "#cfa6ff", "text": "#efe7ff", "muted": "#8f84a3", "graph_bg": "#050408", "grid": "#1a1428",
        "good": "#40e0a0", "warn": "#ffc04d", "bad": "#ff4f7b",
    },
    "Graphite Light": {
        "bg": "#e9eaee", "panel": "#f7f7f9", "panel2": "#ffffff", "border": "#c9cbd3", "accent": "#d0102a",
        "accent2": "#ff4d5e", "text": "#16161a", "muted": "#606570", "graph_bg": "#ffffff", "grid": "#e1e3e8",
        "good": "#13a35a", "warn": "#d98a00", "bad": "#d42020",
    },
}

GRAPHS = {
    "cpu": "Загрузка CPU, %",
    "cpu_temp": "Температура CPU, °C",
    "cpu_freq": "Частота CPU, МГц",
    "gpu": "Загрузка GPU, %",
    "gpu_temp": "Температура GPU, °C",
    "vram": "Видеопамять, МБ",
    "ram": "Оперативная память, %",
    "disk_read": "Чтение с дисков, МБ/с",
    "disk_write": "Запись на диски, МБ/с",
    "net_down": "Сеть: приём, Мбит/с",
    "net_up": "Сеть: отдача, Мбит/с",
}

DEFAULTS: dict[str, Any] = {
    "skin": "Afterburner Red",
    "custom_colors": {},          # переопределения цветов скина
    "graph_colors": {},           # цвет линии для каждого графика
    "font_scale": 1.0,
    "opacity": 1.0,
    "always_on_top": False,
    "interval_ms": 1000,
    "history_sec": 120,
    "graph_height": 64,
    "graphs": ["cpu", "cpu_temp", "cpu_freq", "gpu", "gpu_temp", "vram", "ram", "disk_read", "disk_write",
               "net_down", "net_up"],
    "minimize_to_tray": True,
    "start_minimized": False,
    "autostart": False,
    "temp_unit": "C",
    "alerts": {"enabled": True, "cpu_temp": 90, "gpu_temp": 85, "ram": 92, "disk_free": 5},
    "osd": {"enabled": False, "x": 30, "y": 30, "opacity": 0.85, "click_through": False, "font": 11,
            "items": ["cpu", "cpu_temp", "gpu", "gpu_temp", "ram", "net"]},
    "process_rules": [],
    "window": {"w": 1240, "h": 800, "x": None, "y": None},
}


class Settings:
    def __init__(self) -> None:
        self.path = app_data_dir() / "settings.json"
        self.data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data = _merge(copy.deepcopy(DEFAULTS), loaded)
        except FileNotFoundError:
            pass
        except (OSError, json.JSONDecodeError):
            log.exception("Повреждён файл настроек — используются значения по умолчанию")

    def save(self) -> None:
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            log.exception("Не удалось сохранить настройки")

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value

    def palette(self) -> dict[str, str]:
        base = dict(SKINS.get(self.data["skin"], SKINS["Afterburner Red"]))
        base.update({k: v for k, v in self.data.get("custom_colors", {}).items() if isinstance(v, str)})
        return base

    def graph_color(self, key: str) -> str:
        return self.data.get("graph_colors", {}).get(key) or self.palette()["accent"]

    def export_skin(self) -> dict[str, Any]:
        return {"name": self.data["skin"], "colors": self.palette(), "graph_colors": self.data.get("graph_colors", {})}

    def import_skin(self, skin: dict[str, Any]) -> None:
        colors = skin.get("colors") or {}
        if not isinstance(colors, dict):
            raise ValueError("Неверный формат скина")
        self.data["custom_colors"] = {k: v for k, v in colors.items() if k in SKINS["Afterburner Red"]}
        if isinstance(skin.get("graph_colors"), dict):
            self.data["graph_colors"] = skin["graph_colors"]


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    for k, v in over.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            # пустой словарь по умолчанию (custom_colors, graph_colors) принимает любые ключи
            base[k] = _merge(base[k], v) if base[k] else dict(v)
        elif k in base:
            base[k] = v
    return base


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def set_autostart(enabled: bool) -> str:
    if not IS_WINDOWS:
        return "Только для Windows"
    try:
        if enabled:
            if getattr(sys, "frozen", False):
                cmd = f'"{sys.executable}" --minimized'
            else:
                pyw = sys.executable.replace("python.exe", "pythonw.exe")
                cmd = f'"{pyw}" "{os.path.abspath(sys.argv[0])}" --minimized'
            reg_set("HKCU", RUN_KEY, "SysPulse", cmd)
        else:
            reg_delete_value("HKCU", RUN_KEY, "SysPulse")
        return ""
    except OSError as exc:
        return str(exc)
