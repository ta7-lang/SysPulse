"""Дымовой тест интерфейса: окно создаётся, все страницы открываются, данные отображаются без исключений."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    application = QApplication.instance() or QApplication(sys.argv)
    yield application


def _pump(app, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def test_main_window_all_pages(app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from syspulse.core import settings, winutil
    monkeypatch.setattr(settings, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(winutil, "app_data_dir", lambda: tmp_path)
    errors: list[str] = []
    monkeypatch.setattr(sys, "excepthook", lambda t, v, tb: errors.append(f"{t.__name__}: {v}"))

    from syspulse.ui import common
    monkeypatch.setattr(common, "error_box", lambda *a, **k: errors.append("error_box: " + str(a[-1])))
    from syspulse.ui.main_window import NAV, MainWindow

    win = MainWindow()
    win.resize(1280, 860)
    win.show()
    _pump(app, 2.5)
    assert win.last_snapshot is not None, "поток мониторинга не прислал данные"
    shots = os.environ.get("SYSPULSE_SHOTS")
    for key, _title, _cls in NAV:
        win.navigate(key)
        _pump(app, 0.8)
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
            win.grab().save(str(Path(shots) / f"{key}.png"))

    # смена скина, графиков, оверлея — без ошибок
    for skin in settings.SKINS:
        win.settings["skin"] = skin
        win.apply_style()
    win.settings["skin"] = "Afterburner Red"
    win.apply_style()
    win.settings["graphs"] = ["cpu", "ram"]
    win.rebuild_graphs()
    win.toggle_osd(True)
    _pump(app, 1.5)
    assert win.osd.isVisible() and win.osd.lines
    if shots:
        win.osd.grab().save(str(Path(shots) / "osd.png"))
    win.toggle_osd(False)
    win.settings["temp_unit"] = "F"
    win.rebuild_graphs()
    win.settings["interval_ms"] = 500
    win.restart_monitor()
    _pump(app, 1.2)
    win.quit()
    _pump(app, 0.3)
    assert not errors, errors
