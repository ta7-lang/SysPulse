"""Настройки и кастомизация оформления."""
from __future__ import annotations

import copy
import json
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.settings import DEFAULTS, GRAPHS, SKINS, set_autostart
from ..common import Page, confirm, error_box

COLOR_KEYS = [("accent", "Акцент"), ("accent2", "Акцент (светлый)"), ("bg", "Фон окна"), ("panel", "Панели"),
              ("panel2", "Кнопки"), ("border", "Рамки"), ("text", "Текст"), ("muted", "Второстепенный текст"),
              ("graph_bg", "Фон графиков"), ("grid", "Сетка графиков")]
OSD_ITEMS = {"cpu": "Загрузка CPU", "cpu_temp": "Температура CPU", "cpu_freq": "Частота CPU", "gpu": "Загрузка GPU",
             "gpu_temp": "Температура GPU", "vram": "Видеопамять", "ram": "ОЗУ", "net": "Сеть", "disk": "Диски",
             "time": "Часы"}


class SettingsPage(Page):
    title = "Настройки"
    subtitle = "Скины, цвета, графики, оверлей поверх игр, уведомления и поведение программы"

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.st = ctx.settings
        self._loading = True
        self.tool("Импорт скина…", self._import)
        self.tool("Экспорт скина…", self._export)
        self.tool("Сбросить всё", self._reset)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 6, 0)
        grid.setSpacing(12)

        # ---------------- оформление
        g1 = QGroupBox("ОФОРМЛЕНИЕ")
        f1 = QFormLayout(g1)
        self.skin = QComboBox()
        self.skin.addItems(list(SKINS))
        self.skin.currentTextChanged.connect(self._skin_changed)
        f1.addRow("Скин:", self.skin)
        self.font_scale = QSlider(Qt.Orientation.Horizontal)
        self.font_scale.setRange(80, 140)
        self.font_scale.valueChanged.connect(lambda v: self._set("font_scale", v / 100, restyle=True))
        f1.addRow("Масштаб шрифта:", self.font_scale)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(60, 100)
        self.opacity.valueChanged.connect(lambda v: self._set("opacity", v / 100, restyle=True))
        f1.addRow("Непрозрачность окна:", self.opacity)
        self.on_top = QCheckBox("Поверх всех окон")
        self.on_top.toggled.connect(lambda v: self._set("always_on_top", v, restyle=True))
        f1.addRow("", self.on_top)
        colors = QGridLayout()
        self.color_btns: dict[str, QPushButton] = {}
        for i, (key, title) in enumerate(COLOR_KEYS):
            b = QPushButton(title)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._pick_color(k))
            colors.addWidget(b, i // 2, i % 2)
            self.color_btns[key] = b
        f1.addRow("Цвета:", colors)
        reset_c = QPushButton("Вернуть цвета скина")
        reset_c.clicked.connect(self._reset_colors)
        f1.addRow("", reset_c)
        grid.addWidget(g1, 0, 0)

        # ---------------- графики
        g2 = QGroupBox("МОНИТОРИНГ И ГРАФИКИ")
        v2 = QVBoxLayout(g2)
        f2 = QFormLayout()
        self.interval = QSpinBox()
        self.interval.setRange(250, 10000)
        self.interval.setSingleStep(250)
        self.interval.setSuffix(" мс")
        self.interval.valueChanged.connect(lambda v: self._set("interval_ms", v, restart=True))
        f2.addRow("Период опроса:", self.interval)
        self.history = QSpinBox()
        self.history.setRange(30, 3600)
        self.history.setSuffix(" с")
        self.history.valueChanged.connect(lambda v: self._set("history_sec", v, graphs=True))
        f2.addRow("История графиков:", self.history)
        self.gheight = QSpinBox()
        self.gheight.setRange(40, 200)
        self.gheight.setSuffix(" пикс.")
        self.gheight.valueChanged.connect(lambda v: self._set("graph_height", v, graphs=True))
        f2.addRow("Высота графика:", self.gheight)
        self.temp_unit = QComboBox()
        self.temp_unit.addItems(["°C", "°F"])
        self.temp_unit.currentIndexChanged.connect(lambda i: self._set("temp_unit", "CF"[i], graphs=True))
        f2.addRow("Единицы температуры:", self.temp_unit)
        v2.addLayout(f2)
        hint = QLabel("Отметьте графики и перетащите для изменения порядка. Двойной щелчок — цвет линии.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        v2.addWidget(hint)
        self.graph_list = QListWidget()
        self.graph_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.graph_list.itemChanged.connect(lambda _i: self._graphs_changed())
        self.graph_list.model().rowsMoved.connect(lambda *_a: self._graphs_changed())
        self.graph_list.itemDoubleClicked.connect(self._graph_color)
        self.graph_list.setMinimumHeight(260)
        v2.addWidget(self.graph_list)
        grid.addWidget(g2, 0, 1, 2, 1)

        # ---------------- оверлей
        g3 = QGroupBox("ОВЕРЛЕЙ (OSD) ПОВЕРХ ОКОН И ИГР")
        f3 = QFormLayout(g3)
        self.osd_on = QCheckBox("Показывать оверлей")
        self.osd_on.toggled.connect(lambda v: self._set_osd("enabled", v))
        f3.addRow("", self.osd_on)
        self.osd_click = QCheckBox("Пропускать щелчки мыши (не мешает играм)")
        self.osd_click.toggled.connect(lambda v: self._set_osd("click_through", v))
        f3.addRow("", self.osd_click)
        self.osd_op = QSlider(Qt.Orientation.Horizontal)
        self.osd_op.setRange(30, 100)
        self.osd_op.valueChanged.connect(lambda v: self._set_osd("opacity", v / 100))
        f3.addRow("Непрозрачность:", self.osd_op)
        self.osd_font = QSpinBox()
        self.osd_font.setRange(8, 28)
        self.osd_font.valueChanged.connect(lambda v: self._set_osd("font", v))
        f3.addRow("Размер шрифта:", self.osd_font)
        self.osd_items: dict[str, QCheckBox] = {}
        og = QGridLayout()
        for i, (k, t) in enumerate(OSD_ITEMS.items()):
            cb = QCheckBox(t)
            cb.toggled.connect(lambda _v: self._osd_items_changed())
            og.addWidget(cb, i // 2, i % 2)
            self.osd_items[k] = cb
        f3.addRow("Показатели:", og)
        hint3 = QLabel("Оверлей — окно поверх других; в играх работает в оконном и безрамочном режимах. "
                       "Перетаскивайте его мышью (при выключенном пропуске щелчков).")
        hint3.setObjectName("Muted")
        hint3.setWordWrap(True)
        f3.addRow(hint3)
        grid.addWidget(g3, 1, 0)

        # ---------------- поведение и уведомления
        g4 = QGroupBox("ПОВЕДЕНИЕ И УВЕДОМЛЕНИЯ")
        f4 = QFormLayout(g4)
        self.tray = QCheckBox("Сворачивать в трей при закрытии")
        self.tray.toggled.connect(lambda v: self._set("minimize_to_tray", v))
        f4.addRow("", self.tray)
        self.autostart = QCheckBox("Запускать вместе с Windows")
        self.autostart.toggled.connect(self._autostart)
        f4.addRow("", self.autostart)
        self.start_min = QCheckBox("Запускать свёрнутым")
        self.start_min.toggled.connect(lambda v: self._set("start_minimized", v))
        f4.addRow("", self.start_min)
        self.alerts = QCheckBox("Уведомлять о перегреве и нехватке ресурсов")
        self.alerts.toggled.connect(lambda v: self._set_alert("enabled", v))
        f4.addRow("", self.alerts)
        self.alert_spins: dict[str, QSpinBox] = {}
        for key, title, lo, hi, suf in (("cpu_temp", "Порог CPU:", 50, 110, " °C"), ("gpu_temp", "Порог GPU:", 50, 110, " °C"),
                                        ("ram", "Порог ОЗУ:", 50, 99, " %"), ("disk_free", "Мин. свободно на диске:", 1, 50, " %")):
            sp = QSpinBox()
            sp.setRange(lo, hi)
            sp.setSuffix(suf)
            sp.valueChanged.connect(lambda v, k=key: self._set_alert(k, v))
            f4.addRow(title, sp)
            self.alert_spins[key] = sp
        grid.addWidget(g4, 2, 0)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(3, 1)
        scroll.setWidget(host)
        self.root.addWidget(scroll, 1)
        self.finish_layout()
        self.load_values()

    # --------------------------------------------------------------- загрузка значений
    def load_values(self) -> None:
        self._loading = True
        st = self.st
        self.skin.setCurrentText(st["skin"])
        self.font_scale.setValue(int(st["font_scale"] * 100))
        self.opacity.setValue(int(st["opacity"] * 100))
        self.on_top.setChecked(st["always_on_top"])
        self.interval.setValue(st["interval_ms"])
        self.history.setValue(st["history_sec"])
        self.gheight.setValue(st["graph_height"])
        self.temp_unit.setCurrentIndex(0 if st["temp_unit"] == "C" else 1)
        self.graph_list.clear()
        order = list(st["graphs"]) + [k for k in GRAPHS if k not in st["graphs"]]
        for key in order:
            it = QListWidgetItem(GRAPHS[key])
            it.setData(Qt.ItemDataRole.UserRole, key)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            it.setCheckState(Qt.CheckState.Checked if key in st["graphs"] else Qt.CheckState.Unchecked)
            it.setForeground(QColor(st.graph_color(key)))
            self.graph_list.addItem(it)
        osd = st["osd"]
        self.osd_on.setChecked(osd["enabled"])
        self.osd_click.setChecked(osd["click_through"])
        self.osd_op.setValue(int(osd["opacity"] * 100))
        self.osd_font.setValue(osd["font"])
        for k, cb in self.osd_items.items():
            cb.setChecked(k in osd["items"])
        self.tray.setChecked(st["minimize_to_tray"])
        self.autostart.setChecked(st["autostart"])
        self.start_min.setChecked(st["start_minimized"])
        self.alerts.setChecked(st["alerts"]["enabled"])
        for k, sp in self.alert_spins.items():
            sp.setValue(int(st["alerts"][k]))
        self._paint_color_buttons()
        self._loading = False

    def _paint_color_buttons(self) -> None:
        pal = self.st.palette()
        for key, b in self.color_btns.items():
            c = QColor(pal[key])
            fg = "#000" if c.lightness() > 140 else "#fff"
            b.setStyleSheet(f"QPushButton {{ background: {pal[key]}; color: {fg}; border: 1px solid {pal['border']}; }}")

    # --------------------------------------------------------------- изменения
    def _set(self, key: str, value: Any, restyle: bool = False, graphs: bool = False, restart: bool = False) -> None:
        if self._loading:
            return
        self.st[key] = value
        self.st.save()
        if restyle:
            self.ctx.apply_style()
        if graphs:
            self.ctx.rebuild_graphs()
        if restart:
            self.ctx.restart_monitor()

    def _skin_changed(self, name: str) -> None:
        if self._loading:
            return
        self.st["skin"] = name
        self.st["custom_colors"] = {}
        self.st.save()
        self._paint_color_buttons()
        self.ctx.apply_style()

    def _pick_color(self, key: str) -> None:
        cur = QColor(self.st.palette()[key])
        c = QColorDialog.getColor(cur, self, "Выберите цвет")
        if c.isValid():
            cc = dict(self.st["custom_colors"])
            cc[key] = c.name()
            self.st["custom_colors"] = cc
            self.st.save()
            self._paint_color_buttons()
            self.ctx.apply_style()

    def _reset_colors(self) -> None:
        self.st["custom_colors"] = {}
        self.st["graph_colors"] = {}
        self.st.save()
        self.load_values()
        self.ctx.apply_style()

    def _graphs_changed(self) -> None:
        if self._loading:
            return
        keys = []
        for i in range(self.graph_list.count()):
            it = self.graph_list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                keys.append(it.data(Qt.ItemDataRole.UserRole))
        self.st["graphs"] = keys
        self.st.save()
        self.ctx.rebuild_graphs()

    def _graph_color(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.ItemDataRole.UserRole)
        c = QColorDialog.getColor(QColor(self.st.graph_color(key)), self, "Цвет графика")
        if c.isValid():
            gc = dict(self.st["graph_colors"])
            gc[key] = c.name()
            self.st["graph_colors"] = gc
            self.st.save()
            self._loading = True
            item.setForeground(c)
            self._loading = False
            self.ctx.apply_style()

    def _set_osd(self, key: str, value: Any) -> None:
        if self._loading:
            return
        self.st["osd"][key] = value
        self.st.save()
        self.ctx.update_osd()

    def _osd_items_changed(self) -> None:
        if self._loading:
            return
        self.st["osd"]["items"] = [k for k, cb in self.osd_items.items() if cb.isChecked()]
        self.st.save()
        self.ctx.update_osd()

    def _set_alert(self, key: str, value: Any) -> None:
        if self._loading:
            return
        self.st["alerts"][key] = value
        self.st.save()

    def _autostart(self, on: bool) -> None:
        if self._loading:
            return
        err = set_autostart(on)
        if err:
            error_box(self, "Автозапуск", err)
            return
        self._set("autostart", on)

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Импорт скина", "", "Скин SysPulse (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                self.st.import_skin(json.load(fh))
        except (OSError, ValueError) as exc:
            error_box(self, "Импорт", f"Не удалось загрузить скин: {exc}")
            return
        self.st.save()
        self.load_values()
        self.ctx.apply_style()

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт скина", "my_skin.json", "Скин SysPulse (*.json)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(self.st.export_skin(), fh, ensure_ascii=False, indent=2)
            except OSError as exc:
                error_box(self, "Экспорт", str(exc))

    def _reset(self) -> None:
        if confirm(self, "Сброс", "Вернуть все настройки по умолчанию? Правила процессов тоже будут удалены."):
            self.st.data = copy.deepcopy(DEFAULTS)
            self.st.save()
            self.load_values()
            self.ctx.apply_style()
            self.ctx.rebuild_graphs()
            self.ctx.update_osd()
            self.ctx.restart_monitor()

