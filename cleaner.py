"""Глубокая очистка: кэши, временные файлы, корзина; поиск больших файлов."""
from __future__ import annotations

import functools
import threading
import time
from typing import Any

import psutil
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core import cleaner
from ...core.winutil import human_bytes, is_admin, open_in_explorer
from ..common import Column, DictTableModel, Page, confirm, info_box, make_table, run_bg, selected_rows


def scan_all(cats: list[cleaner.Category], progress: Any = None) -> dict[str, tuple[int, int]]:
    out = {}
    for c in cats:
        if progress:
            progress(f"Анализ: {c.name}…")
        out[c.id] = cleaner.scan_category(c)
    return out


def clean_all(cats: list[cleaner.Category], progress: Any = None) -> tuple[int, int, int, list[str]]:
    freed = deleted = skipped = 0
    notes = []
    for c in cats:
        if progress:
            progress(f"Очистка: {c.name}…")
        busy = cleaner.running_processes(c.process_names)
        if busy:
            notes.append(f"{c.name}: запущен {', '.join(busy)} — часть файлов занята")
        f, d, s = cleaner.clean_category(c, progress)
        freed += f
        deleted += d
        skipped += s
    return freed, deleted, skipped, notes


class CleanerPage(Page):
    title = "Очистка"
    subtitle = "Временные файлы, кэши системы, браузеров и приложений, дампы, журналы, корзина"

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.cats = cleaner.build_categories()
        self.results: dict[str, tuple[int, int]] = {}
        tabs = QTabWidget()

        # ---------------- вкладка очистки
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        bar = QHBoxLayout()
        self.scan_btn = self.button("Анализ", self.refresh)
        self.clean_btn = self.button("Очистить выбранное", self._clean, primary=True)
        self.total_label = QLabel("")
        self.total_label.setObjectName("Big")
        bar.addWidget(self.scan_btn)
        bar.addWidget(self.clean_btn)
        bar.addWidget(self.button("Выбрать рекомендуемое", self._select_default))
        bar.addWidget(self.button("Снять всё", lambda: self._check_all(False)))
        bar.addStretch(1)
        bar.addWidget(self.total_label)
        lay.addLayout(bar)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Категория", "Размер", "Файлов", "Описание"])
        self.tree.setColumnWidth(0, 340)
        self.tree.setColumnWidth(1, 100)
        self.tree.setColumnWidth(2, 80)
        self.items: dict[str, QTreeWidgetItem] = {}
        groups: dict[str, QTreeWidgetItem] = {}
        admin = is_admin()
        for c in self.cats:
            g = groups.get(c.group)
            if g is None:
                g = QTreeWidgetItem([c.group.upper()])
                g.setFlags(g.flags() | Qt.ItemFlag.ItemIsAutoTristate | Qt.ItemFlag.ItemIsUserCheckable)
                f = g.font(0)
                f.setBold(True)
                g.setFont(0, f)
                self.tree.addTopLevelItem(g)
                groups[c.group] = g
            desc = c.description + (" (нужны права администратора)" if c.admin and not admin else "")
            it = QTreeWidgetItem([c.name, "", "", desc])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(0, Qt.CheckState.Checked if c.default else Qt.CheckState.Unchecked)
            it.setData(0, Qt.ItemDataRole.UserRole, c.id)
            it.setToolTip(3, desc)
            it.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            it.setTextAlignment(2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            g.addChild(it)
            self.items[c.id] = it
        self.tree.expandAll()
        lay.addWidget(self.tree, 1)
        tabs.addTab(w, "Очистка")

        # ---------------- вкладка больших файлов
        w2 = QWidget()
        l2 = QVBoxLayout(w2)
        l2.setContentsMargins(8, 8, 8, 8)
        b2 = QHBoxLayout()
        self.drive = QComboBox()
        for p in psutil.disk_partitions(all=False):
            if "cdrom" not in p.opts and p.fstype:
                self.drive.addItem(p.mountpoint)
        self.min_mb = QSpinBox()
        self.min_mb.setRange(10, 100000)
        self.min_mb.setValue(500)
        self.min_mb.setSuffix(" МБ")
        b2.addWidget(QLabel("Диск:"))
        b2.addWidget(self.drive)
        b2.addWidget(QLabel("Больше чем:"))
        b2.addWidget(self.min_mb)
        self.find_btn = self.button("Найти", self._find_large, primary=True)
        b2.addWidget(self.find_btn)
        b2.addWidget(self.button("Стоп", self._stop_large))
        b2.addWidget(self.button("Открыть папку", self._open_large))
        b2.addWidget(self.button("В корзину", self._delete_large))
        b2.addStretch(1)
        l2.addLayout(b2)
        self.large_model = DictTableModel([
            Column("size", "Размер", 100, human_bytes, Qt.AlignmentFlag.AlignRight), Column("date", "Изменён", 130),
            Column("path", "Путь", 700)], key="path")
        self.large_view, _p = make_table(self.large_model)
        l2.addWidget(self.large_view, 1)
        tabs.addTab(w2, "Большие файлы")
        self._stop_flag = threading.Event()

        self.root.addWidget(tabs, 1)
        self.finish_layout()

    # --------------------------------------------------------------- выбор
    def _selected(self) -> list[cleaner.Category]:
        return [c for c in self.cats if self.items[c.id].checkState(0) == Qt.CheckState.Checked]

    def _select_default(self) -> None:
        for c in self.cats:
            self.items[c.id].setCheckState(0, Qt.CheckState.Checked if c.default else Qt.CheckState.Unchecked)

    def _check_all(self, on: bool) -> None:
        for it in self.items.values():
            it.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    # --------------------------------------------------------------- анализ / очистка
    def refresh(self) -> None:
        self.scan_btn.setEnabled(False)
        self.clean_btn.setEnabled(False)
        run_bg(scan_all, self.cats, on_done=self._scanned, on_progress=self.set_status, on_error=self._fail)

    def _fail(self, msg: str) -> None:
        self.scan_btn.setEnabled(True)
        self.clean_btn.setEnabled(True)
        self.set_status(f"Ошибка: {msg}")

    def _scanned(self, res: dict[str, tuple[int, int]]) -> None:
        self.results = res
        pal = self.ctx.settings.palette()
        total = 0
        for c in self.cats:
            size, count = res.get(c.id, (0, 0))
            it = self.items[c.id]
            if c.special in ("dns", "dism"):
                it.setText(1, "—")
                it.setText(2, "")
                continue
            it.setText(1, human_bytes(size))
            it.setText(2, str(count) if count else "")
            it.setForeground(1, QColor(pal["accent2"] if size > 100 * 1024 ** 2 else pal["text"]))
            if self.items[c.id].checkState(0) == Qt.CheckState.Checked:
                total += size
        self.total_label.setText(human_bytes(total))
        self.scan_btn.setEnabled(True)
        self.clean_btn.setEnabled(True)
        self.set_status("Анализ завершён. Отмечено к удалению: " + human_bytes(total))

    def _clean(self) -> None:
        cats = self._selected()
        if not cats:
            info_box(self, "Очистка", "Отметьте категории.")
            return
        busy = sorted({n for c in cats for n in cleaner.running_processes(c.process_names)})
        warn = f"\n\nЗапущены: {', '.join(busy)}. Закройте их для полной очистки." if busy else ""
        if not confirm(self, "Очистка", f"Очистить категорий: {len(cats)}?{warn}"):
            return
        self.scan_btn.setEnabled(False)
        self.clean_btn.setEnabled(False)
        started = time.time()

        def done(res: tuple[int, int, int, list[str]]) -> None:
            freed, deleted, skipped, notes = res
            msg = (f"Освобождено: {human_bytes(freed)}\nУдалено файлов: {deleted}\n"
                   f"Пропущено занятых: {skipped}\nВремя: {time.time() - started:.0f} с")
            if notes:
                msg += "\n\n" + "\n".join(notes)
            info_box(self, "Очистка завершена", msg)
            self.ctx.notify("Очистка завершена", f"Освобождено {human_bytes(freed)}")
            self.refresh()
        run_bg(clean_all, cats, on_done=done, on_progress=self.set_status, on_error=self._fail)

    # --------------------------------------------------------------- большие файлы
    def _find_large(self) -> None:
        root = self.drive.currentText()
        if not root:
            return
        self._stop_flag.clear()
        self.find_btn.setEnabled(False)

        def done(res: list[tuple[int, str, float]]) -> None:
            self.find_btn.setEnabled(True)
            self.large_model.set_rows([{"size": s, "path": p, "date": time.strftime("%d.%m.%Y %H:%M", time.localtime(m))}
                                       for s, p, m in res])
            self.set_status(f"Найдено файлов: {len(res)} (общий размер {human_bytes(sum(s for s, _, _ in res))})")

        def fail(msg: str) -> None:
            self.find_btn.setEnabled(True)
            self.set_status(msg)

        finder = functools.partial(cleaner.find_large_files, should_stop=self._stop_flag.is_set)
        run_bg(finder, root, self.min_mb.value(), 300, on_done=done,
               on_progress=self.set_status, on_error=fail)

    def _stop_large(self) -> None:
        self._stop_flag.set()

    def _open_large(self) -> None:
        for r in selected_rows(self.large_view)[:3]:
            open_in_explorer(r["path"])

    def _delete_large(self) -> None:
        rows = selected_rows(self.large_view)
        if rows and confirm(self, "Удаление", f"Переместить в корзину файлов: {len(rows)}?"):
            ok = [r for r in rows if cleaner.delete_to_recycle_bin(r["path"])]
            self.large_model.set_rows([r for r in self.large_model.rows if r not in ok])
            self.set_status(f"Перемещено в корзину: {len(ok)} из {len(rows)}")
