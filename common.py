"""Общие компоненты интерфейса: фоновые задачи, модель таблиц, диалоги."""
from __future__ import annotations

import logging
import traceback
from collections.abc import Callable, Sequence
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QRunnable,
    QSortFilterProxyModel,
    Qt,
    QThreadPool,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger(__name__)


# ======================================================================= фоновые задачи
class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn: Callable[..., Any], args: tuple[Any, ...], with_progress: bool) -> None:
        super().__init__()
        self.fn, self.args, self.with_progress = fn, args, with_progress
        self.signals = _Signals()

    def run(self) -> None:
        try:
            if self.with_progress:
                result = self.fn(*self.args, progress=self.signals.progress.emit)
            else:
                result = self.fn(*self.args)
        except Exception as exc:  # noqa: BLE001 - отдаём ошибку в интерфейс
            log.error("Ошибка фоновой задачи: %s", traceback.format_exc())
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.done.emit(result)


class _Relay(QObject):
    """Живёт в потоке интерфейса: гарантирует, что колбэки выполняются в нём (queued connection)."""

    def __init__(self, task: _Task, on_done: Callable[[Any], Any] | None, on_error: Callable[[str], Any] | None,
                 on_progress: Callable[[str], Any] | None) -> None:
        super().__init__()
        self.task, self._done, self._error, self._progress = task, on_done, on_error, on_progress
        task.signals.done.connect(self.handle_done)
        task.signals.failed.connect(self.handle_failed)
        task.signals.progress.connect(self.handle_progress)

    @Slot(object)
    def handle_done(self, result: Any) -> None:
        _alive.discard(self)
        if self._done:
            self._done(result)

    @Slot(str)
    def handle_failed(self, msg: str) -> None:
        _alive.discard(self)
        if self._error:
            self._error(msg)
        else:
            error_box(None, "Ошибка", msg)

    @Slot(str)
    def handle_progress(self, msg: str) -> None:
        if self._progress:
            self._progress(msg)


_alive: set[_Relay] = set()


def run_bg(fn: Callable[..., Any], *args: Any, on_done: Callable[[Any], Any] | None = None,
           on_error: Callable[[str], Any] | None = None, on_progress: Callable[[str], Any] | None = None) -> None:
    """Выполняет fn(*args) в пуле потоков; колбэки вызываются в потоке интерфейса."""
    task = _Task(fn, args, on_progress is not None)
    task.setAutoDelete(False)
    _alive.add(_Relay(task, on_done, on_error, on_progress))
    QThreadPool.globalInstance().start(task)


# ======================================================================= таблицы
class Column:
    def __init__(self, key: str, title: str, width: int = 0, fmt: Callable[[Any], str] | None = None,
                 align: Qt.AlignmentFlag | None = None, checkable: bool = False) -> None:
        self.key, self.title, self.width, self.fmt, self.align, self.checkable = key, title, width, fmt, align, checkable


SortRole = Qt.ItemDataRole.UserRole + 1
RowRole = Qt.ItemDataRole.UserRole + 2


class DictTableModel(QAbstractTableModel):
    """Табличная модель над списком словарей. Цвет строки: ключ '_color' (hex)."""

    def __init__(self, columns: Sequence[Column], key: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.columns = list(columns)
        self.rows: list[dict[str, Any]] = []
        self.key = key
        self.checked: set[Any] = set()

    def set_rows(self, rows: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self.rows = rows
        if self.key:
            valid = {r.get(self.key) for r in rows}
            self.checked &= valid
        self.endResetModel()

    def update_rows(self, rows: list[dict[str, Any]]) -> None:
        """Обновляет данные без сброса выделения, если набор ключей не изменился."""
        if self.key and len(rows) == len(self.rows) and all(
                a.get(self.key) == b.get(self.key) for a, b in zip(rows, self.rows, strict=True)):
            self.rows = rows
            if rows:
                self.dataChanged.emit(self.index(0, 0), self.index(len(rows) - 1, len(self.columns) - 1))
        else:
            self.set_rows(rows)

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.columns[section].title
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if self.columns[index.column()].checkable:
            f |= Qt.ItemFlag.ItemIsUserCheckable
        return f

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        row = self.rows[index.row()]
        col = self.columns[index.column()]
        value = row.get(col.key)
        if role == Qt.ItemDataRole.DisplayRole:
            if col.checkable:
                return col.fmt(value) if col.fmt else ""
            if col.fmt:
                try:
                    return col.fmt(value)
                except (TypeError, ValueError):
                    return str(value)
            return "" if value is None else str(value)
        if role == Qt.ItemDataRole.CheckStateRole and col.checkable:
            ident = row.get(self.key) if self.key else index.row()
            return Qt.CheckState.Checked if ident in self.checked else Qt.CheckState.Unchecked
        if role == SortRole:
            if isinstance(value, (int, float)):
                return value
            return str(value or "").lower()
        if role == RowRole:
            return row
        if role == Qt.ItemDataRole.ForegroundRole and row.get("_color"):
            return QColor(row["_color"])
        if role == Qt.ItemDataRole.TextAlignmentRole and col.align is not None:
            return int(col.align | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            return row.get("_tip") or (str(value) if value not in (None, "") else None)
        return None

    def setData(self, index: QModelIndex | QPersistentModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role == Qt.ItemDataRole.CheckStateRole and self.columns[index.column()].checkable:
            ident = self.rows[index.row()].get(self.key) if self.key else index.row()
            if Qt.CheckState(value) == Qt.CheckState.Checked:
                self.checked.add(ident)
            else:
                self.checked.discard(ident)
            self.dataChanged.emit(index, index)
            return True
        return False


class FilterProxy(QSortFilterProxyModel):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.setSortRole(SortRole)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setFilterKeyColumn(-1)
        self.extra: Callable[[dict[str, Any]], bool] | None = None

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex | QPersistentModelIndex) -> bool:
        model = self.sourceModel()
        if self.extra is not None and isinstance(model, DictTableModel) and not self.extra(model.rows[source_row]):
            return False
        return super().filterAcceptsRow(source_row, source_parent)


def make_table(model: DictTableModel, sortable: bool = True) -> tuple[QTableView, FilterProxy]:
    proxy = FilterProxy()
    proxy.setSourceModel(model)
    view = QTableView()
    view.setModel(proxy)
    view.setSortingEnabled(sortable)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    view.setAlternatingRowColors(True)
    view.verticalHeader().setVisible(False)
    view.verticalHeader().setDefaultSectionSize(26)
    view.setShowGrid(False)
    view.setWordWrap(False)
    view.horizontalHeader().setHighlightSections(False)
    view.horizontalHeader().setStretchLastSection(True)
    for i, col in enumerate(model.columns):
        if col.width:
            view.setColumnWidth(i, col.width)
    view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    return view, proxy


def selected_rows(view: QTableView) -> list[dict[str, Any]]:
    rows = []
    sel = view.selectionModel()
    if sel is None:
        return rows
    for idx in sel.selectedRows():
        row = idx.data(RowRole)
        if row is not None:
            rows.append(row)
    return rows


# ======================================================================= виджеты-оболочки
def confirm(parent: QWidget | None, title: str, text: str) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Question)
    yes = box.addButton("Да", QMessageBox.ButtonRole.YesRole)
    box.addButton("Отмена", QMessageBox.ButtonRole.NoRole)
    box.exec()
    return box.clickedButton() is yes


def info_box(parent: QWidget | None, title: str, text: str) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Information)
    box.addButton("OK", QMessageBox.ButtonRole.AcceptRole)
    box.exec()


def error_box(parent: QWidget | None, title: str, text: str) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Warning)
    box.addButton("OK", QMessageBox.ButtonRole.AcceptRole)
    box.exec()


class Page(QWidget):
    """Базовая страница: заголовок, панель инструментов, строка состояния."""

    title = ""
    subtitle = ""

    def __init__(self, ctx: Any) -> None:
        super().__init__()
        self.ctx = ctx
        self.loaded = False
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(18, 14, 18, 12)
        self.root.setSpacing(10)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        t = QLabel(self.title.upper())
        t.setObjectName("PageTitle")
        s = QLabel(self.subtitle)
        s.setObjectName("PageSubtitle")
        s.setWordWrap(True)
        titles.addWidget(t)
        titles.addWidget(s)
        head.addLayout(titles, 1)
        self.toolbar = QHBoxLayout()
        self.toolbar.setSpacing(6)
        head.addLayout(self.toolbar)
        self.root.addLayout(head)
        self.status = QLabel("")
        self.status.setObjectName("StatusLine")

    def finish_layout(self) -> None:
        self.root.addWidget(self.status)

    def button(self, text: str, slot: Callable[[], Any], primary: bool = False, tip: str = "") -> QPushButton:
        b = QPushButton(text)
        if primary:
            b.setObjectName("Primary")
        if tip:
            b.setToolTip(tip)
        b.clicked.connect(lambda _=False: slot())
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    def tool(self, text: str, slot: Callable[[], Any], primary: bool = False, tip: str = "") -> QPushButton:
        b = self.button(text, slot, primary, tip)
        self.toolbar.addWidget(b)
        return b

    def search_box(self, proxy: FilterProxy, placeholder: str = "Поиск…") -> QLineEdit:
        e = QLineEdit()
        e.setPlaceholderText(placeholder)
        e.setClearButtonEnabled(True)
        e.setFixedWidth(220)
        e.textChanged.connect(proxy.setFilterFixedString)
        return e

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def on_show(self) -> None:
        """Вызывается при переходе на страницу."""
        if not self.loaded:
            self.loaded = True
            self.refresh()

    def refresh(self) -> None:
        pass

    def on_snapshot(self, snap: dict[str, Any]) -> None:
        pass


def hline() -> QFrame:
    f = QFrame()
    f.setObjectName("HLine")
    f.setFixedHeight(1)
    return f
