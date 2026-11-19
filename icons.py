"""Векторные иконки, нарисованные QPainter (не зависят от шрифтов и файлов)."""
from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

Draw = Callable[[QPainter], None]


def _gauge(p: QPainter) -> None:
    p.drawArc(QRectF(3, 5, 18, 18), 30 * 16, 120 * 16)
    p.drawArc(QRectF(3, 5, 18, 18), 150 * 16, 60 * 16)
    p.drawArc(QRectF(3, 5, 18, 18), -30 * 16, 60 * 16)
    p.drawLine(QPointF(12, 14), QPointF(17, 9))
    p.drawEllipse(QPointF(12, 14), 1.2, 1.2)


def _chip(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(6, 6, 12, 12), 1.5, 1.5)
    p.drawRect(QRectF(9.5, 9.5, 5, 5))
    for i in (9, 12, 15):
        p.drawLine(QPointF(i, 3), QPointF(i, 6))
        p.drawLine(QPointF(i, 18), QPointF(i, 21))
        p.drawLine(QPointF(3, i), QPointF(6, i))
        p.drawLine(QPointF(18, i), QPointF(21, i))


def _pulse(p: QPainter) -> None:
    path = QPainterPath(QPointF(2, 13))
    for x, y in ((7, 13), (9, 7), (12, 19), (15, 4), (17, 13), (22, 13)):
        path.lineTo(x, y)
    p.drawPath(path)


def _list(p: QPainter) -> None:
    for y in (6, 12, 18):
        p.drawEllipse(QPointF(5, y), 1.2, 1.2)
        p.drawLine(QPointF(9, y), QPointF(21, y))


def _rocket(p: QPainter) -> None:
    path = QPainterPath(QPointF(12, 2))
    path.cubicTo(17, 6, 17, 13, 15, 17)
    path.lineTo(9, 17)
    path.cubicTo(7, 13, 7, 6, 12, 2)
    p.drawPath(path)
    p.drawEllipse(QPointF(12, 9), 1.8, 1.8)
    p.drawLine(QPointF(9, 13), QPointF(6, 18))
    p.drawLine(QPointF(15, 13), QPointF(18, 18))
    p.drawLine(QPointF(12, 19), QPointF(12, 22))


def _cogs(p: QPainter) -> None:
    c = QPointF(12, 12)
    for i in range(8):
        a = i * math.pi / 4
        p.drawLine(QPointF(12 + 6.5 * math.cos(a), 12 + 6.5 * math.sin(a)),
                   QPointF(12 + 9.5 * math.cos(a), 12 + 9.5 * math.sin(a)))
    p.drawEllipse(c, 6.5, 6.5)
    p.drawEllipse(c, 2.5, 2.5)


def _apps(p: QPainter) -> None:
    for x in (4, 13):
        for y in (4, 13):
            p.drawRoundedRect(QRectF(x, y, 7, 7), 1.5, 1.5)


def _broom(p: QPainter) -> None:
    p.drawLine(QPointF(19, 3), QPointF(12, 12))
    path = QPainterPath(QPointF(10, 10))
    path.lineTo(15, 14)
    path.lineTo(11, 21)
    path.lineTo(3, 15)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QPointF(7, 18), QPointF(9.5, 14.5))


def _cubes(p: QPainter) -> None:
    p.drawRect(QRectF(3, 13, 8, 8))
    p.drawRect(QRectF(13, 13, 8, 8))
    p.drawRect(QRectF(3, 3, 8, 8))
    p.save()
    p.translate(17, 7)
    p.rotate(45)
    p.drawRect(QRectF(-3.5, -3.5, 7, 7))
    p.restore()


def _shield(p: QPainter) -> None:
    path = QPainterPath(QPointF(12, 2))
    path.lineTo(20, 5)
    path.cubicTo(20, 13, 17, 18, 12, 22)
    path.cubicTo(7, 18, 4, 13, 4, 5)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QPointF(8.5, 12), QPointF(11, 14.5))
    p.drawLine(QPointF(11, 14.5), QPointF(15.5, 9.5))


def _bolt(p: QPainter) -> None:
    path = QPainterPath(QPointF(13, 2))
    for x, y in ((5, 13), (11, 13), (10, 22), (19, 10), (13, 10), (13, 2)):
        path.lineTo(x, y)
    p.drawPath(path)


def _brush(p: QPainter) -> None:
    p.drawEllipse(QPointF(12, 12), 9, 9)
    for (x, y) in ((8, 8), (15, 7.5), (16.5, 13), (7, 13.5)):
        p.drawEllipse(QPointF(x, y), 1.6, 1.6)
    p.drawArc(QRectF(10, 14, 6, 6), 0, 180 * 16)


def _warn(p: QPainter) -> None:
    path = QPainterPath(QPointF(12, 3))
    path.lineTo(22, 20)
    path.lineTo(2, 20)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QPointF(12, 9), QPointF(12, 14))
    p.drawPoint(QPointF(12, 17))


def _power(p: QPainter) -> None:
    p.drawArc(QRectF(4, 4, 16, 16), 120 * 16, 300 * 16)
    p.drawLine(QPointF(12, 2), QPointF(12, 11))


def _box(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(3, 6, 18, 14), 1.5, 1.5)
    p.drawLine(QPointF(3, 10), QPointF(21, 10))
    p.drawLine(QPointF(9, 3), QPointF(15, 3))
    p.drawLine(QPointF(9, 3), QPointF(9, 6))
    p.drawLine(QPointF(15, 3), QPointF(15, 6))


ICONS: dict[str, Draw] = {
    "monitor": _gauge, "hardware": _chip, "diagnostics": _pulse, "processes": _list, "startup": _rocket,
    "services": _cogs, "programs": _apps, "cleaner": _broom, "registry": _cubes, "security": _shield,
    "optimizer": _bolt, "settings": _brush, "warn": _warn, "power": _power, "box": _box,
}


def icon(name: str, color: str, size: int = 24, active: str | None = None) -> QIcon:
    ic = QIcon()
    for mode, col in ((QIcon.Mode.Normal, color), (QIcon.Mode.Active, active or color),
                      (QIcon.Mode.Selected, active or color)):
        ic.addPixmap(pixmap(name, col, size), mode, QIcon.State.Off)
        ic.addPixmap(pixmap(name, active or col, size), mode, QIcon.State.On)
    return ic


def pixmap(name: str, color: str, size: int = 24) -> QPixmap:
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 24, size / 24)
    pen = QPen(QColor(color), 1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    ICONS.get(name, _box)(p)
    p.end()
    return pm


def app_icon(accent: str = "#e3172d", size: int = 64) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 64
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#111114"))
    p.drawRoundedRect(QRectF(2 * s, 2 * s, 60 * s, 60 * s), 12 * s, 12 * s)
    pen = QPen(QColor(accent), 5 * s)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawArc(QRectF(10 * s, 10 * s, 44 * s, 44 * s), -40 * 16, 260 * 16)
    pen.setWidthF(4 * s)
    p.setPen(pen)
    path = QPainterPath(QPointF(14 * s, 34 * s))
    for x, y in ((24, 34), (28, 24), (34, 42), (39, 28), (43, 34), (50, 34)):
        path.lineTo(x * s, y * s)
    p.drawPath(path)
    p.end()
    return QIcon(pm)
