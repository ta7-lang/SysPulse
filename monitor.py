"""Главная панель мониторинга: индикаторы CPU/GPU, шкалы ресурсов, графики."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ...core.settings import GRAPHS
from ...core.winutil import human_bytes, human_duration, human_rate
from ..common import Page
from ..widgets import Gauge, Graph, HealthRing, Meter


def _mb(v: float) -> str:
    return f"{v:.0f}" if v >= 10 else f"{v:.1f}"


GRAPH_FMT = {
    "cpu": lambda v: f"{v:.0f}%", "gpu": lambda v: f"{v:.0f}%", "ram": lambda v: f"{v:.0f}%",
    "cpu_temp": lambda v: f"{v:.0f}°", "gpu_temp": lambda v: f"{v:.0f}°", "cpu_freq": lambda v: f"{v:.0f}",
    "vram": lambda v: f"{v:.0f}", "disk_read": _mb, "disk_write": _mb, "net_down": _mb, "net_up": _mb,
}
GRAPH_MAX = {"cpu": 100.0, "gpu": 100.0, "ram": 100.0}


class MonitorPage(Page):
    title = "Мониторинг"
    subtitle = "Показатели компьютера в реальном времени"

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.root.setSpacing(12)

        head = QFrame()
        head.setObjectName("Card")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(14, 12, 14, 12)
        hl.setSpacing(18)

        self.cpu_gauge = Gauge("CPU", "% загрузка", 100, "TEMP", "°C", 110)
        self.gpu_gauge = Gauge("GPU", "% загрузка", 100, "TEMP", "°C", 110)
        hl.addWidget(self.cpu_gauge, 3)

        center = QVBoxLayout()
        center.setSpacing(4)
        top = QHBoxLayout()
        self.health = HealthRing()
        top.addWidget(self.health)
        hinfo = QVBoxLayout()
        self.health_text = QLabel("Запустите диагностику,\nчтобы оценить состояние")
        self.health_text.setObjectName("Muted")
        hinfo.addWidget(self.health_text)
        btns = QHBoxLayout()
        self.quick_btn = QPushButton("⚡ Быстрая оптимизация")
        self.quick_btn.setObjectName("Primary")
        self.quick_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.quick_btn.clicked.connect(lambda: self.ctx.quick_optimize())
        diag_btn = QPushButton("Диагностика")
        diag_btn.clicked.connect(lambda: self.ctx.navigate("diagnostics"))
        btns.addWidget(self.quick_btn)
        btns.addWidget(diag_btn)
        hinfo.addLayout(btns)
        top.addLayout(hinfo, 1)
        center.addLayout(top)

        self.meters: dict[str, Meter] = {}
        for key, label in (("ram", "Оперативная память"), ("vram", "Видеопамять"), ("swap", "Файл подкачки"),
                           ("disk", "Активность дисков"), ("down", "Сеть ↓ приём"), ("up", "Сеть ↑ отдача")):
            m = Meter(label)
            self.meters[key] = m
            center.addWidget(m)

        stats = QGridLayout()
        stats.setHorizontalSpacing(14)
        self.stat_labels: dict[str, QLabel] = {}
        for i, (key, label) in enumerate((("uptime", "Время работы"), ("procs", "Процессы"), ("threads", "Потоки"),
                                          ("handles", "Дескрипторы"), ("power", "Питание"), ("fans", "Вентиляторы"))):
            t = QLabel(label.upper())
            t.setObjectName("CardTitle")
            v = QLabel("—")
            v.setObjectName("Accent")
            stats.addWidget(t, (i // 3) * 2, i % 3)
            stats.addWidget(v, (i // 3) * 2 + 1, i % 3)
            self.stat_labels[key] = v
        center.addSpacing(4)
        center.addLayout(stats)
        center.addStretch(1)
        hl.addLayout(center, 4)
        hl.addWidget(self.gpu_gauge, 3)
        self.root.addWidget(head, 0)

        # ---- графики
        cap = QHBoxLayout()
        lbl = QLabel("АППАРАТНЫЙ МОНИТОРИНГ")
        lbl.setObjectName("CardTitle")
        cap.addWidget(lbl)
        cap.addStretch(1)
        self.src_label = QLabel("")
        self.src_label.setObjectName("StatusLine")
        cap.addWidget(self.src_label)
        self.root.addLayout(cap)
        self.graph_host = QWidget()
        self.graph_layout = QVBoxLayout(self.graph_host)
        self.graph_layout.setContentsMargins(0, 0, 4, 0)
        self.graph_layout.setSpacing(4)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.graph_host)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.root.addWidget(scroll, 1)
        self.graphs: dict[str, Graph] = {}
        self.rebuild_graphs()

        self.anim = QTimer(self)
        self.anim.timeout.connect(self._animate)
        self.anim.start(33)
        self.finish_layout()

    def _animate(self) -> None:
        if self.isVisible():
            self.cpu_gauge.tick()
            self.gpu_gauge.tick()

    # --------------------------------------------------------------- оформление
    def rebuild_graphs(self) -> None:
        old = {k: list(g.data) for k, g in self.graphs.items()}
        while self.graph_layout.count():
            item = self.graph_layout.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()
        self.graphs.clear()
        st = self.ctx.settings
        hist = max(10, int(st["history_sec"] * 1000 / max(st["interval_ms"], 250)))
        for key in st["graphs"]:
            if key not in GRAPHS:
                continue
            g = Graph(GRAPHS[key].replace("°C", self.ctx.temp_unit_label), hist, GRAPH_MAX.get(key), GRAPH_FMT.get(key))
            g.setFixedHeight(int(st["graph_height"]))
            for v in old.get(key, [])[-hist:]:
                g.data.append(v)
            self.graphs[key] = g
            self.graph_layout.addWidget(g)
        self.graph_layout.addStretch(1)
        self.apply_palette()

    def apply_palette(self) -> None:
        pal = self.ctx.settings.palette()
        self.cpu_gauge.set_palette(pal)
        self.gpu_gauge.set_palette(pal)
        self.health.set_palette(pal)
        for m in self.meters.values():
            m.set_palette(pal)
        for key, g in self.graphs.items():
            g.set_style(pal, self.ctx.settings.graph_color(key))

    def set_health(self, score: int | None, summary: str) -> None:
        self.health.set_score(score)
        self.health_text.setText(summary)

    # --------------------------------------------------------------- данные
    def on_snapshot(self, snap: dict[str, Any]) -> None:
        cpu = snap["cpu"]
        ram = snap["ram"]
        gpu = snap.get("gpu") or {}
        unit_f = self.ctx.temp_fmt
        cpu_lines = []
        if cpu.get("freq"):
            cpu_lines.append(f"{cpu['freq'] / 1000:.2f} ГГц")
        cpu_lines.append(f"{len(cpu.get('per_core') or [])} потоков")
        self.cpu_gauge.sub_unit = self.ctx.temp_unit_label
        self.gpu_gauge.sub_unit = self.ctx.temp_unit_label
        self.cpu_gauge.set_values(cpu["total"], unit_f(cpu.get("temp")), cpu_lines)
        gpu_lines = []
        if gpu.get("clock"):
            gpu_lines.append(f"{gpu['clock']} МГц")
        if gpu.get("power"):
            gpu_lines.append(f"{gpu['power']:.0f} Вт")
        if gpu.get("fan") is not None:
            gpu_lines.append(f"Вентилятор {gpu['fan']}%")
        if not gpu:
            gpu_lines = ["Нет данных о GPU"]
        self.gpu_gauge.set_values(gpu.get("util"), unit_f(gpu.get("temp")), gpu_lines)

        self.meters["ram"].set_value(ram["percent"] / 100,
                                     f"{human_bytes(ram['used'])} / {human_bytes(ram['total'])}  ({ram['percent']:.0f}%)")
        mu, mt = gpu.get("mem_used"), gpu.get("mem_total")
        if mu is not None and mt:
            self.meters["vram"].set_value(mu / mt, f"{human_bytes(mu)} / {human_bytes(mt)}")
        elif mu is not None:
            self.meters["vram"].set_value(0, human_bytes(mu))
        else:
            self.meters["vram"].set_value(0, "—")
        sw = snap["swap"]
        self.meters["swap"].set_value(sw["percent"] / 100,
                                      f"{human_bytes(sw['used'])} / {human_bytes(sw['total'])}" if sw["total"] else "отключён")
        d = snap["disk"]
        act = d.get("active")
        self.meters["disk"].set_value((act or 0) / 100, (f"{act:.0f}%   " if act is not None else "") +
                                      f"R {human_rate(d['read_bps'])}  W {human_rate(d['write_bps'])}")
        n = snap["net"]
        scale = 100 * 1024 * 1024 / 8  # шкала до 100 Мбит/с, далее — логарифмически
        for key, val in (("down", n["down_bps"]), ("up", n["up_bps"])):
            frac = val / scale if val < scale else min(1.0, 0.75 + 0.25 * (val / scale) / 10)
            self.meters[key].set_value(frac, f"{val * 8 / 1e6:.2f} Мбит/с  ({human_rate(val)})")

        self.stat_labels["uptime"].setText(human_duration(snap["uptime"]))
        self.stat_labels["procs"].setText(str(snap["procs"]))
        self.stat_labels["threads"].setText(str(snap.get("threads") or "—"))
        self.stat_labels["handles"].setText(f"{snap['handles']:,}".replace(",", " ") if snap.get("handles") else "—")
        bat = snap.get("battery")
        if bat:
            self.stat_labels["power"].setText(f"{bat['percent']:.0f}% {'⚡' if bat['plugged'] else '🔋'}")
        else:
            self.stat_labels["power"].setText(f"{cpu['power']:.0f} Вт CPU" if cpu.get("power") else "Сеть")
        fans = snap.get("fans") or []
        self.stat_labels["fans"].setText(", ".join(f"{v:.0f}" for _n, v in fans[:3]) + " об/мин" if fans else "—")
        src = snap.get("temp_source")
        self.src_label.setText(f"Источник температур: {src}" if src else
                               "Температуры: запустите от администратора или LibreHardwareMonitor")

        values = {
            "cpu": cpu["total"], "cpu_temp": unit_f(cpu.get("temp")), "cpu_freq": cpu.get("freq"),
            "gpu": gpu.get("util"), "gpu_temp": unit_f(gpu.get("temp")),
            "vram": (mu / 1024 / 1024) if mu is not None else None, "ram": ram["percent"],
            "disk_read": d["read_bps"] / 1024 / 1024, "disk_write": d["write_bps"] / 1024 / 1024,
            "net_down": n["down_bps"] * 8 / 1e6, "net_up": n["up_bps"] * 8 / 1e6,
        }
        for key, g in self.graphs.items():
            g.push(values.get(key))
