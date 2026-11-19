"""Компоненты ПК и их состояние."""
from __future__ import annotations

from typing import Any

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from ...core.hardware import DEVICE_ERRORS, battery_wear, collect_hardware
from ...core.winutil import human_bytes, run_elevated_console
from ..common import Page, run_bg


class HardwarePage(Page):
    title = "Компоненты"
    subtitle = "Процессор, память, видеокарта, накопители (S.M.A.R.T.), сеть, устройства с ошибками"

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.tool("Диспетчер устройств", lambda: ctx.run_action("devmgmt"))
        self.tool("Проверка памяти (mdsched)", lambda: run_elevated_console("mdsched.exe", keep_open=False),
                  tip="Средство проверки памяти Windows — потребуется перезагрузка")
        self.tool("Обновить", self.refresh, primary=True)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Параметр", "Значение"])
        self.tree.setColumnWidth(0, 300)
        self.tree.setAlternatingRowColors(True)
        self.root.addWidget(self.tree, 1)
        self.finish_layout()

    def refresh(self) -> None:
        self.set_status("Сбор сведений об оборудовании…")
        run_bg(collect_hardware, on_done=self._show, on_error=lambda e: self.set_status(f"Ошибка: {e}"))

    def _section(self, title: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([title.upper(), ""])
        f = item.font(0)
        f.setBold(True)
        item.setFont(0, f)
        item.setForeground(0, QColor(self.ctx.settings.palette()["accent"]))
        self.tree.addTopLevelItem(item)
        item.setExpanded(True)
        return item

    def _row(self, parent: QTreeWidgetItem, key: str, value: Any, color: str | None = None) -> QTreeWidgetItem:
        it = QTreeWidgetItem([key, "" if value in (None, "") else str(value)])
        if color:
            it.setForeground(1, QColor(color))
        parent.addChild(it)
        return it

    def _show(self, info: dict[str, Any]) -> None:
        self.ctx.hw_info = info
        pal = self.ctx.settings.palette()
        good, warn, bad = pal["good"], pal["warn"], pal["bad"]
        self.tree.clear()
        os_ = info.get("os", {})
        sec = self._section("Система")
        self._row(sec, "Операционная система", os_.get("caption"))
        self._row(sec, "Версия / сборка", f"{os_.get('version', '')} (сборка {os_.get('build', '—')})")
        self._row(sec, "Разрядность", os_.get("arch"))
        self._row(sec, "Компьютер / пользователь", f"{os_.get('host', '')} / {os_.get('user', '')}")
        self._row(sec, "Установлена", os_.get("install"))
        self._row(sec, "Последняя загрузка", os_.get("boot"))
        board, bios = info.get("board") or {}, info.get("bios") or {}
        if board:
            self._row(sec, "Материнская плата", f"{board.get('vendor', '')} {board.get('product', '')}")
        if bios:
            self._row(sec, "BIOS/UEFI", f"{bios.get('vendor', '')} {bios.get('version', '')} от {bios.get('date', '')}")

        for cpu in info.get("cpu", []):
            s = self._section("Процессор")
            self._row(s, "Модель", cpu.get("name"))
            self._row(s, "Ядра / потоки", f"{cpu.get('cores')} / {cpu.get('threads')}")
            self._row(s, "Базовая частота", f"{cpu.get('mhz')} МГц" if cpu.get("mhz") else None)
            if cpu.get("l2"):
                self._row(s, "Кэш L2 / L3", f"{cpu.get('l2')} КБ / {cpu.get('l3')} КБ")
            self._row(s, "Сокет", cpu.get("socket"))
            if cpu.get("virt") is not None:
                self._row(s, "Виртуализация в BIOS", "Включена" if cpu.get("virt") else "Выключена")

        ram = info.get("ram", [])
        if ram:
            s = self._section("Оперативная память")
            total = sum(float(m.get("size") or 0) for m in ram if isinstance(m, dict))
            self._row(s, "Всего", f"{human_bytes(total)} в {len(ram)} модулях")
            for m in ram:
                if not isinstance(m, dict):
                    continue
                speed = m.get("speed") or m.get("maxspeed")
                txt = f"{human_bytes(m.get('size'))}, {speed} МТ/с, {m.get('vendor', '')} {m.get('part', '')}"
                it = self._row(s, m.get("slot") or "Модуль", txt)
                if m.get("maxspeed") and m.get("speed") and m["speed"] < m["maxspeed"]:
                    it.setForeground(1, QColor(warn))
                    it.setToolTip(1, f"Работает ниже номинала ({m['maxspeed']}). Включите XMP/EXPO в BIOS.")

        for g in info.get("gpu", []):
            if not isinstance(g, dict):
                continue
            s = self._section("Видеоадаптер")
            self._row(s, "Модель", g.get("name"))
            self._row(s, "Драйвер", f"{g.get('driver', '')} от {g.get('driverdate', '')}")
            self._row(s, "Режим", g.get("res"))
            err = int(g.get("err") or 0)
            self._row(s, "Состояние", "Работает нормально" if err == 0 else DEVICE_ERRORS.get(err, f"Ошибка {err}"),
                      good if err == 0 else bad)

        disks = info.get("disks", [])
        if disks:
            s = self._section("Накопители (S.M.A.R.T.)")
            for d in disks:
                if not isinstance(d, dict):
                    continue
                health = str(d.get("health") or "Неизвестно")
                ok = health.lower() == "healthy"
                node = self._row(s, d.get("name") or "Диск",
                                 f"{human_bytes(d.get('size'))} · {d.get('media', '')} · {d.get('bus', '')}")
                self._row(node, "Состояние", "Исправен" if ok else health, good if ok else bad)
                if d.get("temp"):
                    self._row(node, "Температура", f"{d['temp']} °C (макс. {d.get('tempmax') or '—'})",
                              warn if d["temp"] >= 60 else None)
                if d.get("wear") is not None:
                    self._row(node, "Износ", f"{d['wear']}%", bad if d["wear"] >= 80 else warn if d["wear"] >= 50 else None)
                if d.get("hours"):
                    self._row(node, "Наработка", f"{d['hours']} ч")
                if d.get("rerr") or d.get("werr"):
                    self._row(node, "Ошибки чтения/записи", f"{d.get('rerr') or 0} / {d.get('werr') or 0}", bad)
                node.setExpanded(True)
            if info.get("smartfail"):
                self._row(s, "ПРЕДУПРЕЖДЕНИЕ S.M.A.R.T.", "Прогнозируется отказ: " + ", ".join(info["smartfail"]), bad)

        parts = info.get("partitions", [])
        if parts:
            s = self._section("Разделы")
            for p in parts:
                free = 100 - p["percent"]
                self._row(s, f"{p['mount']} ({p['fs']})",
                          f"свободно {human_bytes(p['free'])} из {human_bytes(p['total'])} ({free:.0f}%)",
                          bad if free < 5 else warn if free < 12 else None)

        nets = info.get("net", [])
        if nets:
            s = self._section("Сетевые адаптеры (активные)")
            for n in nets:
                if isinstance(n, dict):
                    self._row(s, str(n.get("name") or ""), f"{n.get('desc', '')} · {n.get('speed', '')} · {n.get('mac', '')}")

        wear = battery_wear(info)
        if wear is not None:
            s = self._section("Аккумулятор")
            b = info.get("battery") or {}
            self._row(s, "Проектная / полная ёмкость", f"{b.get('design'):.0f} / {b.get('full'):.0f} мВт·ч")
            self._row(s, "Износ", f"{wear:.0f}%", bad if wear >= 40 else warn if wear >= 20 else good)

        bad_devs = [d for d in info.get("badDevices", []) if isinstance(d, dict)]
        s = self._section("Устройства с ошибками")
        if not bad_devs:
            self._row(s, "Все устройства", "Работают нормально", good)
        for d in bad_devs:
            code = int(d.get("code") or 0)
            self._row(s, d.get("name") or "Неизвестное устройство",
                      f"{DEVICE_ERRORS.get(code, 'Ошибка')} (код {code})", warn if code == 22 else bad)
        self.tree.resizeColumnToContents(0)
        self.set_status(f"Найдено устройств с ошибками: {len(bad_devs)}")
