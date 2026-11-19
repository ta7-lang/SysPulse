"""Виджеты в стиле MSI Afterburner: стрелочные индикаторы, графики мониторинга, шкалы."""
from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QSizePolicy, QWidget

Palette = dict[str, str]


def _font(size: float, bold: bool = False, family: str = "Bahnschrift") -> QFont:
    f = QFont(family)
    f.setStyleHint(QFont.StyleHint.SansSerif)
    f.setPixelSize(max(6, int(size)))
    f.setBold(bold)
    return f


# ======================================================================= стрелочный индикатор
class Gauge(QWidget):
    """Круговой индикатор с двумя шкалами: внешняя — основная величина, внутренняя — дополнительная."""

    START, SPAN = 225.0, 270.0

    def __init__(self, title: str, unit: str = "%", maximum: float = 100.0,
                 sub_title: str = "", sub_unit: str = "°C", sub_max: float = 110.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title, self.unit, self.maximum = title, unit, maximum
        self.sub_title, self.sub_unit, self.sub_max = sub_title, sub_unit, sub_max
        self.value: float | None = None
        self.sub_value: float | None = None
        self.shown = 0.0       # для плавной анимации стрелки
        self.lines: list[str] = []
        self.pal: Palette = {}
        self.sub_color = "#ffb020"
        self.setMinimumSize(220, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def sizeHint(self) -> QSize:
        return QSize(280, 280)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        return w

    def set_palette(self, pal: Palette) -> None:
        self.pal = pal
        self.sub_color = pal.get("warn", "#ffb020")
        self.update()

    def set_values(self, value: float | None, sub: float | None = None, lines: list[str] | None = None) -> None:
        self.value = value
        self.sub_value = sub
        if lines is not None:
            self.lines = lines
        self.update()

    def tick(self) -> None:
        target = self.value or 0.0
        if abs(target - self.shown) > 0.05:
            self.shown += (target - self.shown) * 0.35
            self.update()

    def _angle(self, frac: float) -> float:
        return self.START - self.SPAN * max(0.0, min(1.0, frac))

    def paintEvent(self, _event: Any) -> None:  # noqa: C901
        pal = self.pal or {"bg": "#0a0a0c", "panel": "#131316", "accent": "#e3172d", "text": "#eee",
                           "muted": "#888", "border": "#2b2b33", "graph_bg": "#060607", "accent2": "#ff5266"}
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height())
        p.translate((self.width() - side) / 2, (self.height() - side) / 2)
        s = side / 300.0
        c = QPointF(150 * s, 150 * s)
        accent = QColor(pal["accent"])
        light = QColor(pal["bg"]).lightness() > 128

        # внешнее кольцо «металл»
        ring = QRadialGradient(c, 148 * s)
        if light:
            ring.setColorAt(0.86, QColor("#c9cbd3"))
            ring.setColorAt(0.93, QColor("#f4f5f7"))
            ring.setColorAt(1.0, QColor("#a7aab3"))
        else:
            ring.setColorAt(0.86, QColor("#050506"))
            ring.setColorAt(0.92, QColor("#3a3a42"))
            ring.setColorAt(0.97, QColor("#18181c"))
            ring.setColorAt(1.0, QColor("#2d2d33"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(ring))
        p.drawEllipse(c, 148 * s, 148 * s)

        # лицевая панель
        face = QRadialGradient(QPointF(150 * s, 110 * s), 140 * s)
        face.setColorAt(0.0, QColor(pal["panel"]).lighter(125 if not light else 102))
        face.setColorAt(1.0, QColor(pal["graph_bg"]))
        p.setBrush(QBrush(face))
        p.drawEllipse(c, 128 * s, 128 * s)

        # подложка шкалы
        r_main = 112 * s
        rect = QRectF(c.x() - r_main, c.y() - r_main, 2 * r_main, 2 * r_main)
        track = QPen(QColor(pal["border"]), 10 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
        p.setPen(track)
        p.drawArc(rect, int(self.START * 16), int(-self.SPAN * 16))

        # деления и подписи
        p.setFont(_font(11 * s))
        for i in range(0, 51):
            frac = i / 50
            a = math.radians(self._angle(frac))
            major = i % 5 == 0
            r1 = 98 * s if major else 101 * s
            r2 = 92 * s if major else 97 * s
            col = QColor(pal["text"] if major else pal["muted"])
            if frac > 0.8:
                col = QColor(pal["accent2"]) if major else accent.darker(130)
            p.setPen(QPen(col, (1.8 if major else 1.0) * s))
            p.drawLine(QPointF(c.x() + r1 * math.cos(a), c.y() - r1 * math.sin(a)),
                       QPointF(c.x() + r2 * math.cos(a), c.y() - r2 * math.sin(a)))
            if major and i % 10 == 0:
                rl = 80 * s
                label = f"{self.maximum * frac:.0f}"
                pt = QPointF(c.x() + rl * math.cos(a), c.y() - rl * math.sin(a))
                p.setPen(QColor(pal["muted"]))
                p.drawText(QRectF(pt.x() - 18 * s, pt.y() - 8 * s, 36 * s, 16 * s), Qt.AlignmentFlag.AlignCenter, label)

        # основная дуга значения со свечением
        frac = (self.shown / self.maximum) if self.maximum else 0
        if frac > 0.001:
            span = -self.SPAN * max(0.0, min(1.0, frac))
            glow = QColor(accent)
            for width, alpha_ in ((22, 30), (16, 55)):
                glow.setAlpha(alpha_)
                p.setPen(QPen(glow, width * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
                p.drawArc(rect, int(self.START * 16), int(span * 16))
            grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
            grad.setColorAt(0, accent.darker(140))
            grad.setColorAt(1, QColor(pal["accent2"]))
            p.setPen(QPen(QBrush(grad), 10 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            p.drawArc(rect, int(self.START * 16), int(span * 16))
            # стрелка-маркер
            a = math.radians(self._angle(frac))
            tip = QPointF(c.x() + 122 * s * math.cos(a), c.y() - 122 * s * math.sin(a))
            base = QPointF(c.x() + 104 * s * math.cos(a), c.y() - 104 * s * math.sin(a))
            p.setPen(QPen(QColor("#ffffff"), 3 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(base, tip)

        # внутренняя дуга: дополнительная величина (температура)
        r_sub = 64 * s
        srect = QRectF(c.x() - r_sub, c.y() - r_sub, 2 * r_sub, 2 * r_sub)
        p.setPen(QPen(QColor(pal["border"]), 4 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        p.drawArc(srect, int(self.START * 16), int(-self.SPAN * 16))
        if self.sub_value is not None and self.sub_max:
            sf = max(0.0, min(1.0, self.sub_value / self.sub_max))
            sc = QColor(self.sub_color)
            if self.sub_value >= self.sub_max * 0.8:
                sc = QColor(pal.get("bad", "#ff3b3b"))
            p.setPen(QPen(sc, 4 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            p.drawArc(srect, int(self.START * 16), int(-self.SPAN * sf * 16))

        # тексты
        p.setPen(QColor(pal["muted"]))
        p.setFont(_font(13 * s, True))
        p.drawText(QRectF(0, 68 * s, 300 * s, 20 * s), Qt.AlignmentFlag.AlignCenter, self.title.upper())
        p.setPen(QColor(pal["text"]))
        p.setFont(_font(46 * s, True))
        val = "—" if self.value is None else f"{self.value:.0f}"
        p.drawText(QRectF(0, 104 * s, 300 * s, 56 * s), Qt.AlignmentFlag.AlignCenter, val)
        p.setFont(_font(13 * s))
        p.setPen(QColor(pal["muted"]))
        p.drawText(QRectF(0, 152 * s, 300 * s, 18 * s), Qt.AlignmentFlag.AlignCenter, self.unit)
        if self.sub_title:
            sv = "—" if self.sub_value is None else f"{self.sub_value:.0f}{self.sub_unit}"
            p.setPen(QColor(self.sub_color))
            p.setFont(_font(15 * s, True))
            p.drawText(QRectF(0, 172 * s, 300 * s, 20 * s), Qt.AlignmentFlag.AlignCenter, f"{self.sub_title} {sv}")
        p.setFont(_font(12 * s))
        p.setPen(QColor(pal["text"]))
        for i, line in enumerate(self.lines[:3]):
            p.drawText(QRectF(20 * s, (226 + i * 17) * s, 260 * s, 17 * s), Qt.AlignmentFlag.AlignCenter, line)
        p.end()


# ======================================================================= график мониторинга
class Graph(QWidget):
    """Прокручивающийся график в стиле окна мониторинга Afterburner."""

    def __init__(self, title: str, history: int = 120, fixed_max: float | None = None,
                 fmt: Callable[[float], str] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title = title
        self.data: deque[float | None] = deque(maxlen=history)
        self.fixed_max = fixed_max
        self.fmt = fmt or (lambda v: f"{v:.0f}")
        self.pal: Palette = {}
        self.color = "#e3172d"
        self.setMinimumHeight(54)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_history(self, n: int) -> None:
        self.data = deque(list(self.data)[-n:], maxlen=n)

    def set_style(self, pal: Palette, color: str) -> None:
        self.pal, self.color = pal, color
        self.update()

    def push(self, value: float | None) -> None:
        self.data.append(value)
        self.update()

    def paintEvent(self, _event: Any) -> None:
        pal = self.pal or {"graph_bg": "#060607", "grid": "#1d1d24", "muted": "#888", "text": "#eee", "border": "#2b2b33"}
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(pal["graph_bg"]))
        p.setPen(QPen(QColor(pal["border"]), 1))
        p.drawRect(0, 0, w - 1, h - 1)

        values = [v for v in self.data if v is not None]
        vmax = self.fixed_max or (max(values) * 1.15 if values and max(values) > 0 else 1.0)
        vmin = 0.0
        top, bottom = 16, h - 3
        plot_h = bottom - top

        # сетка
        p.setPen(QPen(QColor(pal["grid"]), 1))
        for i in range(1, 4):
            y = top + plot_h * i / 4
            p.drawLine(1, int(y), w - 2, int(y))
        step = max(w / 12, 30)
        x = w - 1.0
        while x > 0:
            p.drawLine(int(x), top, int(x), bottom)
            x -= step

        # линия
        n = self.data.maxlen or 1
        dx = (w - 2) / max(n - 1, 1)
        offset = n - len(self.data)
        path = QPainterPath()
        fill = QPainterPath()
        started = False
        last_x = 0.0
        first_x = 0.0
        for i, v in enumerate(self.data):
            if v is None:
                continue
            xx = 1 + (offset + i) * dx
            frac = (v - vmin) / (vmax - vmin) if vmax > vmin else 0
            yy = bottom - plot_h * max(0.0, min(1.0, frac))
            if not started:
                path.moveTo(xx, yy)
                fill.moveTo(xx, bottom)
                fill.lineTo(xx, yy)
                first_x = xx
                started = True
            else:
                path.lineTo(xx, yy)
                fill.lineTo(xx, yy)
            last_x = xx
        col = QColor(self.color)
        if started:
            fill.lineTo(last_x, bottom)
            fill.lineTo(first_x, bottom)
            grad = QLinearGradient(0, top, 0, bottom)
            c1, c2 = QColor(col), QColor(col)
            c1.setAlpha(110)
            c2.setAlpha(8)
            grad.setColorAt(0, c1)
            grad.setColorAt(1, c2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(grad))
            p.drawPath(fill)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(col, 1.6))
            p.drawPath(path)

        # подписи
        p.setFont(_font(11, False, "Segoe UI"))
        p.setPen(QColor(pal["muted"]))
        p.drawText(QRectF(6, 1, w * 0.6, 15), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.title)
        if values:
            mn, mx = min(values), max(values)
            p.drawText(QRectF(w * 0.4, 1, w * 0.6 - 70, 15), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"мин {self.fmt(mn)}   макс {self.fmt(mx)}")
            cur = self.data[-1]
            p.setPen(QColor(pal["text"]))
            p.setFont(_font(12, True))
            p.drawText(QRectF(w - 66, 1, 60, 15), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       "—" if cur is None else self.fmt(cur))
        else:
            p.drawText(QRectF(w - 120, 1, 114, 15), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       "нет данных")
        p.end()


# ======================================================================= шкала-«ползунок»
class Meter(QWidget):
    """Горизонтальная шкала как у ползунков Afterburner: подпись, полоса, значение."""

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.label = label
        self.frac = 0.0
        self.text = "—"
        self.pal: Palette = {}
        self.setFixedHeight(38)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_palette(self, pal: Palette) -> None:
        self.pal = pal
        self.update()

    def set_value(self, frac: float | None, text: str) -> None:
        self.frac = max(0.0, min(1.0, frac or 0.0))
        self.text = text
        self.update()

    def paintEvent(self, _event: Any) -> None:
        pal = self.pal or {"muted": "#888", "text": "#eee", "border": "#2b2b33", "accent": "#e3172d",
                           "accent2": "#ff5266", "graph_bg": "#060607", "warn": "#ffb020", "bad": "#ff3b3b"}
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        p.setFont(_font(11, True))
        p.setPen(QColor(pal["muted"]))
        p.drawText(QRectF(0, 0, w * 0.6, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self.label.upper())
        p.setPen(QColor(pal["text"]))
        p.setFont(_font(12, True))
        p.drawText(QRectF(w * 0.3, 0, w * 0.7, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self.text)
        bar = QRectF(0, 21, w, 10)
        p.setPen(QPen(QColor(pal["border"]), 1))
        p.setBrush(QColor(pal["graph_bg"]))
        p.drawRoundedRect(bar, 3, 3)
        if self.frac > 0:
            fill = QRectF(bar.x() + 1, bar.y() + 1, (bar.width() - 2) * self.frac, bar.height() - 2)
            base = QColor(pal["accent"])
            if self.frac > 0.9:
                base = QColor(pal.get("bad", "#ff3b3b"))
            g = QLinearGradient(fill.topLeft(), fill.topRight())
            g.setColorAt(0, base.darker(170))
            g.setColorAt(1, base)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawRoundedRect(fill, 2, 2)
            # «ручка» ползунка
            hx = fill.right()
            p.setBrush(QColor(pal["text"]))
            p.drawRoundedRect(QRectF(hx - 2, bar.y() - 3, 4, bar.height() + 6), 1.5, 1.5)
        # риски
        p.setPen(QPen(QColor(pal["border"]), 1))
        for i in range(1, 10):
            x = bar.x() + bar.width() * i / 10
            p.drawLine(QPointF(x, 33), QPointF(x, 36))
        p.end()


# ======================================================================= индикатор здоровья
class HealthRing(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.score: int | None = None
        self.pal: Palette = {}
        self.setFixedSize(92, 92)

    def set_palette(self, pal: Palette) -> None:
        self.pal = pal
        self.update()

    def set_score(self, score: int | None) -> None:
        self.score = score
        self.update()

    def paintEvent(self, _event: Any) -> None:
        pal = self.pal or {"border": "#333", "text": "#eee", "muted": "#888", "good": "#38d27a", "warn": "#ffb020",
                           "bad": "#ff3b3b"}
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(8, 8, 76, 76)
        p.setPen(QPen(QColor(pal["border"]), 7))
        p.drawEllipse(r)
        if self.score is not None:
            col = pal["good"] if self.score >= 80 else pal["warn"] if self.score >= 55 else pal["bad"]
            p.setPen(QPen(QColor(col), 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(r, 90 * 16, int(-360 * 16 * self.score / 100))
        p.setPen(QColor(pal["text"]))
        p.setFont(_font(24, True))
        p.drawText(QRectF(0, 22, 92, 34), Qt.AlignmentFlag.AlignCenter, "—" if self.score is None else str(self.score))
        p.setPen(QColor(pal["muted"]))
        p.setFont(_font(9, True))
        p.drawText(QRectF(0, 54, 92, 14), Qt.AlignmentFlag.AlignCenter, "ЗДОРОВЬЕ")
        p.end()
