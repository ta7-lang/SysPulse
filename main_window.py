"""Главное окно: безрамочное окно в стиле MSI Afterburner, боковое меню, трей, оверлей."""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QGuiApplication, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QSizeGrip,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .. import __app_name__, __version__
from ..core import cleaner, optimizer, processes
from ..core.sensors import Sampler
from ..core.settings import Settings
from ..core.winutil import IS_WINDOWS, human_bytes, is_admin, popen_detached, relaunch_as_admin, run_elevated_console
from . import icons
from .common import Page, confirm, error_box, info_box, run_bg
from .osd import OsdWindow
from .pages.cleaner import CleanerPage
from .pages.diagnostics import DiagnosticsPage
from .pages.hardware import HardwarePage
from .pages.monitor import MonitorPage
from .pages.optimizer import OptimizerPage
from .pages.processes import ProcessesPage
from .pages.programs import ProgramsPage
from .pages.registry import RegistryPage
from .pages.security import SecurityPage, enable_firewall
from .pages.services import ServicesPage
from .pages.settings import SettingsPage
from .pages.startup import StartupPage
from .theme import build_qss

log = logging.getLogger(__name__)

NAV = [
    ("monitor", "Мониторинг", MonitorPage),
    ("hardware", "Компоненты", HardwarePage),
    ("diagnostics", "Диагностика", DiagnosticsPage),
    ("processes", "Процессы", ProcessesPage),
    ("startup", "Автозагрузка", StartupPage),
    ("services", "Службы", ServicesPage),
    ("programs", "Программы", ProgramsPage),
    ("cleaner", "Очистка", CleanerPage),
    ("registry", "Реестр", RegistryPage),
    ("security", "Безопасность", SecurityPage),
    ("optimizer", "Оптимизация", OptimizerPage),
    ("settings", "Настройки", SettingsPage),
]

QUICK_CATEGORIES = {"user_temp", "win_temp", "shader", "crash", "dns", "chrome", "edge", "yandex", "brave",
                    "opera", "firefox", "vivaldi", "discord", "steam"}


# ======================================================================= поток мониторинга
class MonitorThread(QThread):
    snapshot = Signal(dict)

    def __init__(self, interval_ms: int) -> None:
        super().__init__()
        self.interval_ms = interval_ms
        self._running = True

    def run(self) -> None:
        sampler = Sampler()
        try:
            while self._running:
                t0 = time.monotonic()
                try:
                    self.snapshot.emit(sampler.sample())
                except Exception:
                    log.exception("Ошибка сбора показателей")
                left = self.interval_ms / 1000 - (time.monotonic() - t0)
                end = time.monotonic() + max(left, 0.05)
                while self._running and time.monotonic() < end:
                    self.msleep(50)
        finally:
            sampler.close()

    def stop(self) -> None:
        self._running = False
        self.wait(3000)


# ======================================================================= заголовок окна
class TitleBar(QFrame):
    def __init__(self, win: MainWindow) -> None:
        super().__init__()
        self.win = win
        self.setObjectName("TitleBar")
        self.setFixedHeight(44)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 0, 0)
        lay.setSpacing(8)
        self.logo_icon = QLabel()
        lay.addWidget(self.logo_icon)
        a = QLabel("SYS")
        a.setObjectName("Logo")
        b = QLabel("PULSE")
        b.setObjectName("LogoAccent")
        lay.addWidget(a)
        lay.addWidget(b)
        lay.addSpacing(10)
        self.admin = QPushButton()
        self.admin.setCursor(Qt.CursorShape.PointingHandCursor)
        self.admin.clicked.connect(win.elevate)
        lay.addWidget(self.admin)
        lay.addStretch(1)
        self.osd_btn = QPushButton("OSD")
        self.osd_btn.setObjectName("Badge")
        self.osd_btn.setCheckable(True)
        self.osd_btn.setToolTip("Оверлей поверх окон и игр")
        self.osd_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.osd_btn.toggled.connect(win.toggle_osd)
        lay.addWidget(self.osd_btn)
        lay.addSpacing(6)
        for text, slot, name in (("—", win.showMinimized, "TitleBtn"), ("☐", win.toggle_max, "TitleBtn"),
                                 ("✕", win.close, "CloseBtn")):
            btn = QPushButton(text)
            btn.setObjectName(name)
            btn.clicked.connect(lambda _=False, s=slot: s())
            lay.addWidget(btn)
        self.refresh_admin()

    def refresh_admin(self) -> None:
        if is_admin():
            self.admin.setText("● АДМИНИСТРАТОР")
            self.admin.setObjectName("Badge")
            self.admin.setEnabled(False)
        else:
            self.admin.setText("▲ Ограниченный режим — получить права администратора")
            self.admin.setObjectName("BadgeWarn")
        self.admin.style().unpolish(self.admin)
        self.admin.style().polish(self.admin)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.win.windowHandle() is not None:
            self.win.windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, _e: QMouseEvent) -> None:
        self.win.toggle_max()


# ======================================================================= главное окно
class MainWindow(QMainWindow):
    EDGE = 6

    def __init__(self, start_minimized: bool = False) -> None:
        super().__init__()
        self.settings = Settings()
        self.last_snapshot: dict[str, Any] | None = None
        self.hw_info: dict[str, Any] | None = None
        self._alert_times: dict[str, float] = {}
        self._last_disk_check = 0.0
        self._really_quit = False
        self._really_closed = False
        self.setWindowTitle(f"{__app_name__} {__version__}")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setMinimumSize(1060, 680)
        self.setWindowIcon(icons.app_icon())

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(1, 1, 1, 1)
        v.setSpacing(0)
        self.title_bar = TitleBar(self)
        v.addWidget(self.title_bar)
        body = QHBoxLayout()
        body.setSpacing(0)
        v.addLayout(body, 1)

        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(206)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 10, 0, 8)
        sl.setSpacing(1)
        self.stack = QStackedWidget()
        self.pages: dict[str, Page] = {}
        self.nav_buttons: dict[str, QPushButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, title, cls in NAV:
            btn = QPushButton("  " + title)
            btn.setObjectName("NavBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self.navigate(k))
            group.addButton(btn)
            sl.addWidget(btn)
            self.nav_buttons[key] = btn
            page = cls(self)
            self.pages[key] = page
            self.stack.addWidget(page)
        sl.addStretch(1)
        ver = QLabel(f"  v{__version__} · офлайн")
        ver.setObjectName("Version")
        sl.addWidget(ver)
        body.addWidget(side)
        body.addWidget(self.stack, 1)
        grip = QSizeGrip(root)
        grip.setFixedSize(14, 14)
        v.addWidget(grip, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)

        self.osd = OsdWindow(self)
        self._setup_tray()
        self.apply_style()

        w = self.settings["window"]
        self.resize(int(w.get("w") or 1240), int(w.get("h") or 800))
        if w.get("x") is not None and w.get("y") is not None \
                and QGuiApplication.screenAt(QPoint(int(w["x"]) + 40, int(w["y"]) + 20)) is not None:
            self.move(int(w["x"]), int(w["y"]))  # только если точка на одном из мониторов

        self.monitor: MonitorThread | None = None
        self.restart_monitor()
        self.rules_timer = QTimer(self)
        self.rules_timer.timeout.connect(self._apply_rules)
        self.rules_timer.start(4000)
        QApplication.instance().installEventFilter(self)  # type: ignore[union-attr]
        self.navigate("monitor")
        self.title_bar.osd_btn.setChecked(bool(self.settings["osd"]["enabled"]))
        if start_minimized and self.tray is not None:
            QTimer.singleShot(0, self.hide)

    # --------------------------------------------------------------- контекст для страниц
    @property
    def temp_unit_label(self) -> str:
        return "°F" if self.settings["temp_unit"] == "F" else "°C"

    def temp_fmt(self, value: float | None) -> float | None:
        if value is None:
            return None
        return value * 9 / 5 + 32 if self.settings["temp_unit"] == "F" else value

    def navigate(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        self.nav_buttons[key].setChecked(True)
        self.stack.setCurrentWidget(page)
        page.on_show()

    def notify(self, title: str, text: str) -> None:
        if self.tray is not None and self.tray.isVisible():
            self.tray.showMessage(title, text, QSystemTrayIcon.MessageIcon.Information, 5000)

    def set_health(self, score: int | None, summary: str) -> None:
        mon = self.pages["monitor"]
        if isinstance(mon, MonitorPage):
            mon.set_health(score, summary)

    def apply_style(self) -> None:
        pal = self.settings.palette()
        QApplication.instance().setStyleSheet(build_qss(pal, float(self.settings["font_scale"])))  # type: ignore[union-attr]
        self.setWindowOpacity(float(self.settings["opacity"]))
        on_top = bool(self.settings["always_on_top"])
        if bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) != on_top:
            was_visible = self.isVisible()
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on_top)
            if was_visible:
                self.show()
        for key, btn in self.nav_buttons.items():
            btn.setIcon(icons.icon(key, pal["muted"], 20, pal["accent"]))
        self.title_bar.logo_icon.setPixmap(icons.app_icon(pal["accent"], 26).pixmap(26, 26))
        mon = self.pages.get("monitor")
        if isinstance(mon, MonitorPage):
            mon.apply_palette()
        self.osd.update()
        if self.tray is not None:
            self.tray.setIcon(icons.app_icon(pal["accent"]))

    def rebuild_graphs(self) -> None:
        mon = self.pages.get("monitor")
        if isinstance(mon, MonitorPage):
            mon.rebuild_graphs()

    def restart_monitor(self) -> None:
        if self.monitor is not None:
            self.monitor.stop()
        self.monitor = MonitorThread(int(self.settings["interval_ms"]))
        self.monitor.snapshot.connect(self._on_snapshot)
        self.monitor.start()

    def update_osd(self) -> None:
        self.osd.apply_settings()
        self.title_bar.osd_btn.blockSignals(True)
        self.title_bar.osd_btn.setChecked(bool(self.settings["osd"]["enabled"]))
        self.title_bar.osd_btn.blockSignals(False)
        if self.last_snapshot:
            self.osd.update_snapshot(self.last_snapshot)

    def toggle_osd(self, on: bool) -> None:
        self.settings["osd"]["enabled"] = on
        self.settings.save()
        self.update_osd()
        settings_page = self.pages.get("settings")
        if isinstance(settings_page, SettingsPage):
            settings_page.load_values()

    # --------------------------------------------------------------- действия
    def run_action(self, action: str) -> None:
        drive = os.environ.get("SYSTEMDRIVE", "C:")
        if action.startswith("goto:"):
            self.navigate(action[5:])
        elif action == "free_ram":
            run_bg(optimizer.free_memory, on_done=lambda r: info_box(
                self, "Память", f"Освобождено {human_bytes(r[0])}\n\n" + "\n".join(r[1])))
        elif action == "sfc":
            self._console("sfc /scannow")
        elif action == "dism":
            self._console("DISM /Online /Cleanup-Image /RestoreHealth")
        elif action == "chkdsk":
            self._console(f"chkdsk {drive} /scan")
        elif action == "mdsched":
            run_elevated_console("mdsched.exe", keep_open=False)
        elif action == "netreset":
            if confirm(self, "Сброс сети", "Сбросить Winsock и TCP/IP? Потребуется перезагрузка."):
                run_bg(optimizer.network_reset, on_done=lambda err: info_box(
                    self, "Сброс сети", err or "Готово. Перезагрузите компьютер."))
        elif action in ("devmgmt", "eventvwr", "appwiz"):
            target = {"devmgmt": "devmgmt.msc", "eventvwr": "eventvwr.msc", "appwiz": "appwiz.cpl"}[action]
            if IS_WINDOWS:
                os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606
        elif action == "perfmon_rel":
            popen_detached(["perfmon.exe", "/rel"])
        elif action == "defender_update":
            self.navigate("security")
            sec = self.pages["security"]
            if isinstance(sec, SecurityPage):
                sec._update()
        elif action == "firewall_on":
            run_bg(enable_firewall, on_done=lambda err: (error_box(self, "Брандмауэр", err) if err
                                                         else info_box(self, "Брандмауэр", "Брандмауэр включён.")))
        else:
            log.warning("Неизвестное действие %s", action)

    def _console(self, cmd: str) -> None:
        if not run_elevated_console(cmd):
            error_box(self, "Запуск", "Не удалось запустить консоль администратора.")

    def quick_optimize(self) -> None:
        cats = [c for c in cleaner.build_categories() if c.id in QUICK_CATEGORIES and (is_admin() or not c.admin)]
        text = ("Будут выполнены безопасные действия:\n• очистка временных файлов, кэшей браузеров и шейдеров\n"
                "• удаление дампов памяти\n• сброс кэша DNS\n• освобождение оперативной памяти\n\n"
                "Корзина, история и пароли браузеров не затрагиваются. Продолжить?")
        if not confirm(self, "Оптимизация в один клик", text):
            return

        def work(progress: Any) -> tuple[int, int, int]:
            freed = deleted = 0
            for c in cats:
                progress(f"Очистка: {c.name}")
                f, d, _s = cleaner.clean_category(c)
                freed += f
                deleted += d
            progress("Освобождение памяти…")
            ram, _notes = optimizer.free_memory()
            return freed, deleted, ram

        mon = self.pages["monitor"]

        def done(res: tuple[int, int, int]) -> None:
            mon.set_status("")
            info_box(self, "Оптимизация завершена",
                     f"Удалено файлов: {res[1]}\nОсвобождено на диске: {human_bytes(res[0])}\n"
                     f"Освобождено ОЗУ: {human_bytes(res[2])}")
            self.notify("Оптимизация завершена", f"Диск: {human_bytes(res[0])}, ОЗУ: {human_bytes(res[2])}")
        run_bg(work, on_done=done, on_progress=mon.set_status)

    def elevate(self) -> None:
        if is_admin():
            return
        if relaunch_as_admin():
            self._really_quit = True
            self.close()
        else:
            error_box(self, "Права администратора", "Запуск с правами администратора отменён или недоступен.")

    def _apply_rules(self) -> None:
        rules = self.settings["process_rules"]
        if rules:
            run_bg(processes.apply_rules, list(rules), on_done=self._rules_done, on_error=lambda _e: None)

    def _rules_done(self, done: list[str]) -> None:
        for msg in done:
            log.info(msg)
            if msg.startswith("Заблокирован"):
                self.notify("Правило процессов", msg)

    # --------------------------------------------------------------- данные
    def _on_snapshot(self, snap: dict[str, Any]) -> None:
        self.last_snapshot = snap
        current = self.stack.currentWidget()
        mon = self.pages["monitor"]
        mon.on_snapshot(snap)  # графики копят историю всегда
        if current is not mon and isinstance(current, Page):
            current.on_snapshot(snap)
        self.osd.update_snapshot(snap)
        self._check_alerts(snap)
        if self.tray is not None:
            gpu = snap.get("gpu") or {}
            t = self.temp_fmt(snap["cpu"].get("temp"))
            self.tray.setToolTip(f"{__app_name__}\nCPU {snap['cpu']['total']:.0f}%"
                                 + (f" · {t:.0f}{self.temp_unit_label}" if t is not None else "")
                                 + (f"\nGPU {gpu['util']:.0f}%" if gpu.get("util") is not None else "")
                                 + f"\nRAM {snap['ram']['percent']:.0f}%")

    def _check_alerts(self, snap: dict[str, Any]) -> None:
        a = self.settings["alerts"]
        if not a.get("enabled"):
            return
        now = time.time()

        def fire(key: str, text: str) -> None:
            if now - self._alert_times.get(key, 0) > 300:
                self._alert_times[key] = now
                self.notify("Предупреждение", text)

        t = snap["cpu"].get("temp")
        if t and t >= a["cpu_temp"]:
            fire("cpu_temp", f"Температура процессора {t:.0f} °C")
        gt = (snap.get("gpu") or {}).get("temp")
        if gt and gt >= a["gpu_temp"]:
            fire("gpu_temp", f"Температура видеокарты {gt:.0f} °C")
        if snap["ram"]["percent"] >= a["ram"]:
            fire("ram", f"Оперативная память заполнена на {snap['ram']['percent']:.0f}%")
        if now - self._last_disk_check >= 60:
            self._last_disk_check = now
            import psutil
            for p in psutil.disk_partitions(all=False):
                if "cdrom" in p.opts or not p.fstype:
                    continue
                try:
                    u = psutil.disk_usage(p.mountpoint)
                except OSError:
                    continue
                if 100 - u.percent < a["disk_free"]:
                    fire("disk" + p.mountpoint, f"Мало места на диске {p.mountpoint}: {100 - u.percent:.0f}% свободно")

    # --------------------------------------------------------------- трей
    def _setup_tray(self) -> None:
        self.tray: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(icons.app_icon(), self)
        menu = QMenu()
        for text, slot in (("Открыть SysPulse", self.show_normal), ("Оверлей вкл/выкл", self._tray_osd),
                           ("Быстрая оптимизация", self.quick_optimize)):
            act = QAction(text, menu)
            act.triggered.connect(lambda _=False, s=slot: s())
            menu.addAction(act)
        menu.addSeparator()
        quit_act = QAction("Выход", menu)
        quit_act.triggered.connect(self.quit)
        menu.addAction(quit_act)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_normal()

    def _tray_osd(self) -> None:
        self.title_bar.osd_btn.toggle()

    def show_normal(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def toggle_max(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def quit(self) -> None:
        self._really_quit = True
        self.close()

    # --------------------------------------------------------------- изменение размера безрамочного окна
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if self._really_closed or not isinstance(obj, QWidget):
            return False
        if event.type() == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent) \
                and event.button() == Qt.MouseButton.LeftButton and not self.isMaximized() \
                and isinstance(obj, QWidget) and obj.window() is self:
            edges = self._edges(self.mapFromGlobal(event.globalPosition().toPoint()))
            if edges and self.windowHandle() is not None:
                self.windowHandle().startSystemResize(edges)
                return True
        elif event.type() == QEvent.Type.MouseMove and isinstance(obj, QWidget) and obj.window() is self \
                and isinstance(event, QMouseEvent) and not event.buttons():
            edges = self._edges(self.mapFromGlobal(event.globalPosition().toPoint()))
            shape = Qt.CursorShape.ArrowCursor
            if edges in (Qt.Edge.LeftEdge, Qt.Edge.RightEdge):
                shape = Qt.CursorShape.SizeHorCursor
            elif edges in (Qt.Edge.TopEdge, Qt.Edge.BottomEdge):
                shape = Qt.CursorShape.SizeVerCursor
            elif edges in (Qt.Edge.TopEdge | Qt.Edge.LeftEdge, Qt.Edge.BottomEdge | Qt.Edge.RightEdge):
                shape = Qt.CursorShape.SizeFDiagCursor
            elif edges:
                shape = Qt.CursorShape.SizeBDiagCursor
            if edges:
                self.setCursor(shape)
            else:
                self.unsetCursor()
        return False

    def _edges(self, pos: QPoint) -> Qt.Edge:
        e = Qt.Edge(0)
        if pos.x() <= self.EDGE:
            e |= Qt.Edge.LeftEdge
        if pos.x() >= self.width() - self.EDGE:
            e |= Qt.Edge.RightEdge
        if pos.y() <= self.EDGE:
            e |= Qt.Edge.TopEdge
        if pos.y() >= self.height() - self.EDGE:
            e |= Qt.Edge.BottomEdge
        return e

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._really_quit and self.settings["minimize_to_tray"] and self.tray is not None:
            event.ignore()
            self.hide()
            if not self.settings.data.get("_tray_hint_shown"):
                self.notify(__app_name__, "Программа продолжает работать в трее. Выход — через меню значка.")
                self.settings.data["_tray_hint_shown"] = True
            return
        geo = self.normalGeometry()
        self.settings["window"] = {"w": geo.width(), "h": geo.height(), "x": geo.x(), "y": geo.y()}
        self.settings.save()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self.rules_timer.stop()
        if self.monitor is not None:
            self.monitor.stop()
        self.osd.close()
        if self.tray is not None:
            self.tray.hide()
        self._really_closed = True
        event.accept()
        if app is not None:
            app.quit()
