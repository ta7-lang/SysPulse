"""Оверлей (OSD) поверх окон — компактный монитор в стиле RivaTuner/Afterburner."""
from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QMouseEvent, QPainter
from PySide6.QtWidgets import QWidget

from ..core.winutil import human_bytes


class OsdWindow(QWidget):
    def __init__(self, ctx: Any) -> None:
        super().__init__(None)
        self.ctx = ctx
        self.lines: list[tuple[str, str]] = []
        self._drag: QPoint | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.apply_settings()

    def apply_settings(self) -> None:
        osd = self.ctx.settings["osd"]
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
                 | Qt.WindowType.WindowDoesNotAcceptFocus)
        if osd["click_through"]:
            flags |= Qt.WindowType.WindowTransparentForInput
        visible = self.isVisible()
        self.setWindowFlags(flags)
        self.setWindowOpacity(float(osd["opacity"]))
        self.move(int(osd.get("x") or 30), int(osd.get("y") or 30))
        if osd["enabled"]:
            self.show()
        elif visible:
            self.hide()
        self._resize()

    def update_snapshot(self, snap: dict[str, Any]) -> None:
        if not self.isVisible():
            return
        items = self.ctx.settings["osd"]["items"]
        cpu, ram, gpu = snap["cpu"], snap["ram"], snap.get("gpu") or {}
        tf = self.ctx.temp_fmt
        unit = self.ctx.temp_unit_label
        lines: list[tuple[str, str]] = []

        def temp(v: float | None) -> str:
            t = tf(v)
            return f"{t:.0f}{unit}" if t is not None else "—"

        cpu_parts = []
        if "cpu" in items:
            cpu_parts.append(f"{cpu['total']:3.0f} %")
        if "cpu_temp" in items:
            cpu_parts.append(temp(cpu.get("temp")))
        if "cpu_freq" in items and cpu.get("freq"):
            cpu_parts.append(f"{cpu['freq']:.0f} МГц")
        if cpu_parts:
            lines.append(("CPU", "   ".join(cpu_parts)))
        gpu_parts = []
        if "gpu" in items:
            gpu_parts.append(f"{gpu['util']:3.0f} %" if gpu.get("util") is not None else "—")
        if "gpu_temp" in items:
            gpu_parts.append(temp(gpu.get("temp")))
        if "vram" in items and gpu.get("mem_used") is not None:
            gpu_parts.append(f"{gpu['mem_used'] / 1024 ** 2:.0f} МБ")
        if gpu_parts:
            lines.append(("GPU", "   ".join(gpu_parts)))
        if "ram" in items:
            lines.append(("RAM", f"{human_bytes(ram['used'])}  ({ram['percent']:.0f} %)"))
        if "disk" in items:
            d = snap["disk"]
            lines.append(("DSK", f"R {d['read_bps'] / 1e6:.1f}  W {d['write_bps'] / 1e6:.1f} МБ/с"))
        if "net" in items:
            n = snap["net"]
            lines.append(("NET", f"↓ {n['down_bps'] * 8 / 1e6:.1f}  ↑ {n['up_bps'] * 8 / 1e6:.1f} Мбит/с"))
        if "time" in items:
            lines.append(("TIME", time.strftime("%H:%M:%S")))
        self.lines = lines
        self._resize()
        self.update()

    def _font(self, bold: bool = False) -> QFont:
        f = QFont("Consolas")
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setPixelSize(int(self.ctx.settings["osd"]["font"]) + 3)
        f.setBold(bold)
        return f

    def _resize(self) -> None:
        fm = QFontMetrics(self._font(True))
        width = max([fm.horizontalAdvance(f"{a}  {b}") for a, b in self.lines] or [120]) + 30
        height = max(1, len(self.lines)) * (fm.height() + 2) + 14
        self.setFixedSize(width, height)

    def paintEvent(self, _event: Any) -> None:
        pal = self.ctx.settings.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(pal["bg"])
        bg.setAlpha(200)
        p.setBrush(bg)
        p.setPen(QColor(pal["accent"]))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 6, 6)
        fm = QFontMetrics(self._font(True))
        lh = fm.height() + 2
        label_w = fm.horizontalAdvance("TIME") + 10
        for i, (label, value) in enumerate(self.lines):
            y = 7 + i * lh
            p.setFont(self._font(True))
            p.setPen(QColor(pal["accent"]))
            p.drawText(QRectF(10, y, label_w, lh), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            p.setPen(QColor(pal["text"]))
            p.drawText(QRectF(10 + label_w, y, self.width() - label_w - 14, lh),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, value)
        p.end()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _e: QMouseEvent) -> None:
        if self._drag is not None:
            self._drag = None
            self.ctx.settings["osd"]["x"] = self.x()
            self.ctx.settings["osd"]["y"] = self.y()
            self.ctx.settings.save()
