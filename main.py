"""Точка входа SysPulse."""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys


def _setup_logging() -> None:
    from syspulse.core.winutil import app_data_dir
    log_dir = app_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(log_dir / "syspulse.log", maxBytes=1_000_000, backupCount=3,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def main() -> int:
    _setup_logging()
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication

    from syspulse import __app_name__
    from syspulse.ui.main_window import MainWindow

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    app.setQuitOnLastWindowClosed(False)

    # единственный экземпляр: повторный запуск просто показывает окно
    key = "SysPulse-single-instance"
    sock = QLocalSocket()
    sock.connectToServer(key)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        return 0
    QLocalServer.removeServer(key)
    server = QLocalServer()
    server.listen(key)

    win = MainWindow(start_minimized="--minimized" in sys.argv)

    def on_connection() -> None:
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.close()
        win.show_normal()

    server.newConnection.connect(on_connection)
    if "--minimized" not in sys.argv or win.tray is None:
        win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
