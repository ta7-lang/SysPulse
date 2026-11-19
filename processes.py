"""Процессы: контроль фоновых процессов, приоритеты, правила автоматической блокировки."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QInputDialog, QListWidget, QMenu, QPushButton, QVBoxLayout

from ...core import processes
from ...core.security import check_signatures
from ...core.winutil import human_bytes, open_in_explorer
from ..common import Column, DictTableModel, Page, confirm, error_box, info_box, make_table, run_bg, selected_rows

FILTERS = ["Все процессы", "Приложения", "Фоновые", "Системные", "Подозрительные", "Не отвечают", "Тяжёлые (>300 МБ / >5% CPU)"]


class ProcessesPage(Page):
    title = "Процессы"
    subtitle = "Контроль приложений и фоновых процессов: завершение, заморозка, приоритет, правила блокировки"

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.tracker = processes.ProcessTracker()
        self.busy = False
        self.model = DictTableModel([
            Column("name", "Имя", 210), Column("pid", "PID", 64, align=Qt.AlignmentFlag.AlignRight),
            Column("kind", "Тип", 100), Column("cpu", "CPU %", 70, lambda v: f"{v:.1f}", Qt.AlignmentFlag.AlignRight),
            Column("mem", "Память", 90, human_bytes, Qt.AlignmentFlag.AlignRight),
            Column("threads", "Потоки", 64, align=Qt.AlignmentFlag.AlignRight), Column("status", "Состояние", 110),
            Column("priority", "Приоритет", 110), Column("user", "Пользователь", 110), Column("started", "Запущен", 90),
            Column("warn", "Предупреждение", 220), Column("exe", "Путь", 300),
        ], key="pid")
        self.view, self.proxy = make_table(self.model)
        self.view.sortByColumn(3, Qt.SortOrder.DescendingOrder)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._menu)

        self.filter = QComboBox()
        self.filter.addItems(FILTERS)
        self.filter.setFixedWidth(230)
        self.filter.currentIndexChanged.connect(self._apply_filter)
        self.toolbar.addWidget(self.filter)
        self.toolbar.addWidget(self.search_box(self.proxy, "Имя, путь, PID…"))
        self.tool("Правила…", self._rules_dialog, tip="Автоматическая блокировка и приоритеты процессов")
        self.tool("Завершить", self._kill, primary=True)

        bar = QHBoxLayout()
        for text, slot, tip in (
                ("Завершить дерево", lambda: self._kill(tree=True), "Завершить процесс вместе с дочерними"),
                ("Заморозить", self._suspend, "Приостановить процесс (не тратит CPU, память сохраняется)"),
                ("Разморозить", lambda: self._suspend(resume=True), ""),
                ("Папка", self._open, "Открыть расположение файла"),
                ("Подпись", self._signature, "Проверить цифровую подпись файла"),
                ("Блокировать", self._block, "Завершать процесс при каждом запуске (правило)"),
                ("Понижать приоритет", self._low_rule, "Всегда запускать с приоритетом «ниже среднего» (правило)")):
            bar.addWidget(self.button(text, slot, tip=tip))
        bar.addStretch(1)
        self.root.addLayout(bar)
        self.root.addWidget(self.view, 1)
        self.finish_layout()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)

    def on_show(self) -> None:
        self.refresh()
        self.timer.start(2000)

    def hideEvent(self, event: Any) -> None:
        self.timer.stop()
        super().hideEvent(event)

    def refresh(self) -> None:
        if self.busy:
            return
        self.busy = True
        run_bg(self.tracker.snapshot, on_done=self._show, on_error=self._fail)

    def _fail(self, msg: str) -> None:
        self.busy = False
        self.set_status(msg)

    def _show(self, rows: list[dict[str, Any]]) -> None:
        self.busy = False
        pal = self.ctx.settings.palette()
        for r in rows:
            if r["warn"]:
                r["_color"] = pal["warn"]
            elif r["status"] == "Не отвечает":
                r["_color"] = pal["bad"]
            elif r["kind"] == "Системный":
                r["_color"] = pal["muted"]
        rows.sort(key=lambda r: r["pid"])
        self.model.update_rows(rows)
        self.proxy.invalidate()
        bg = sum(1 for r in rows if r["kind"] == "Фоновый")
        apps = sum(1 for r in rows if r["kind"] == "Приложение")
        mem_bg = sum(r["mem"] for r in rows if r["kind"] == "Фоновый")
        sus = sum(1 for r in rows if r["warn"])
        self.set_status(f"Всего: {len(rows)} · приложений: {apps} · фоновых: {bg} (занимают {human_bytes(mem_bg)}) · "
                        f"подозрительных: {sus} · правил: {len(self.ctx.settings['process_rules'])}")

    def _apply_filter(self) -> None:
        idx = self.filter.currentIndex()
        tests = {
            1: lambda r: r["kind"] == "Приложение",
            2: lambda r: r["kind"] == "Фоновый",
            3: lambda r: r["kind"] == "Системный",
            4: lambda r: bool(r["warn"]),
            5: lambda r: r["status"] == "Не отвечает",
            6: lambda r: r["mem"] > 300 * 1024 ** 2 or r["cpu"] > 5,
        }
        self.proxy.extra = tests.get(idx)
        self.proxy.invalidate()

    # --------------------------------------------------------------- действия
    def _selection(self) -> list[dict[str, Any]]:
        rows = selected_rows(self.view)
        if not rows:
            info_box(self, "Процессы", "Выберите процесс в таблице.")
        return rows

    def _kill(self, tree: bool = False) -> None:
        rows = self._selection()
        if not rows:
            return
        names = ", ".join(f"{r['name']} ({r['pid']})" for r in rows[:6])
        if not confirm(self, "Завершение процесса", f"Завершить {'дерево процессов' if tree else 'процесс'}: {names}?\n"
                                                    "Несохранённые данные будут потеряны."):
            return
        errors = [e for r in rows if (e := processes.kill(r["pid"], tree))]
        if errors:
            error_box(self, "Процессы", "\n".join(errors))
        self.refresh()

    def _suspend(self, resume: bool = False) -> None:
        rows = self._selection()
        errors = [e for r in rows if (e := processes.suspend(r["pid"], resume))]
        if errors:
            error_box(self, "Процессы", "\n".join(errors))
        self.refresh()

    def _priority(self, key: str) -> None:
        errors = [e for r in self._selection() if (e := processes.set_priority(r["pid"], key))]
        if errors:
            error_box(self, "Приоритет", "\n".join(errors))
        self.refresh()

    def _open(self) -> None:
        for r in self._selection()[:3]:
            open_in_explorer(r["exe"])

    def _signature(self) -> None:
        rows = [r for r in self._selection() if r["exe"]]
        if not rows:
            return
        self.set_status("Проверка цифровых подписей…")

        def done(res: dict[str, tuple[str, str]]) -> None:
            text = "\n".join(f"{r['name']}: {res.get(r['exe'], ('нет данных', ''))[0]} "
                             f"{res.get(r['exe'], ('', ''))[1]}".strip() for r in rows)
            info_box(self, "Цифровая подпись", text or "Нет данных")
            self.set_status("")
        run_bg(check_signatures, [r["exe"] for r in rows], on_done=done)

    def _add_rule(self, name: str, action: str, priority: str = "below") -> None:
        rules = [r for r in self.ctx.settings["process_rules"] if r.get("name", "").lower() != name.lower()]
        rules.append({"name": name, "action": action, "priority": priority})
        self.ctx.settings["process_rules"] = rules
        self.ctx.settings.save()

    def _block(self) -> None:
        rows = self._selection()
        crit = [r["name"] for r in rows if r["critical"]]
        if crit:
            error_box(self, "Правила", "Нельзя блокировать системные процессы: " + ", ".join(crit))
            return
        if rows and confirm(self, "Блокировка", "Процессы будут автоматически завершаться при каждом запуске:\n" +
                            "\n".join(r["name"] for r in rows) + "\n\nОтменить можно в «Правила…»."):
            for r in rows:
                self._add_rule(r["name"], "block")
                processes.kill(r["pid"])
            self.refresh()

    def _low_rule(self) -> None:
        for r in self._selection():
            if not r["critical"]:
                self._add_rule(r["name"], "priority", "below")
                processes.set_priority(r["pid"], "below")
        self.refresh()

    def _rules_dialog(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Правила процессов")
        dlg.resize(460, 360)
        lay = QVBoxLayout(dlg)
        lst = QListWidget()

        def fill() -> None:
            lst.clear()
            for r in self.ctx.settings["process_rules"]:
                act = "блокировать запуск" if r["action"] == "block" else \
                    f"приоритет: {processes.PRIORITY_NAMES.get(r.get('priority', ''), '')}"
                lst.addItem(f"{r['name']} — {act}")
        fill()
        lay.addWidget(lst)
        row = QHBoxLayout()
        add_b = QPushButton("Добавить…")
        del_b = QPushButton("Удалить")
        close_b = QPushButton("Закрыть")

        def add() -> None:
            name, ok = QInputDialog.getText(dlg, "Новое правило", "Имя процесса (например, updater.exe):")
            if not ok or not name.strip():
                return
            if name.strip().lower() in processes.CRITICAL:
                error_box(dlg, "Правила", "Это системный процесс.")
                return
            choice, ok = QInputDialog.getItem(dlg, "Действие", "Что делать:",
                                              ["Блокировать запуск", "Низкий приоритет", "Ниже среднего",
                                               "Выше среднего", "Высокий приоритет"], 0, False)
            if not ok:
                return
            mapping = {"Блокировать запуск": ("block", "below"), "Низкий приоритет": ("priority", "idle"),
                       "Ниже среднего": ("priority", "below"), "Выше среднего": ("priority", "above"),
                       "Высокий приоритет": ("priority", "high")}
            act, prio = mapping[choice]
            self._add_rule(name.strip(), act, prio)
            fill()

        def remove() -> None:
            i = lst.currentRow()
            if i >= 0:
                rules = list(self.ctx.settings["process_rules"])
                rules.pop(i)
                self.ctx.settings["process_rules"] = rules
                self.ctx.settings.save()
                fill()

        add_b.clicked.connect(add)
        del_b.clicked.connect(remove)
        close_b.clicked.connect(dlg.accept)
        for b in (add_b, del_b):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close_b)
        lay.addLayout(row)
        dlg.exec()

    def _menu(self, pos: QPoint) -> None:
        if not selected_rows(self.view):
            return
        m = QMenu(self)
        m.addAction("Завершить", self._kill)
        m.addAction("Завершить дерево процессов", lambda: self._kill(tree=True))
        m.addSeparator()
        m.addAction("Заморозить", self._suspend)
        m.addAction("Разморозить", lambda: self._suspend(resume=True))
        pm = m.addMenu("Приоритет")
        for key, title in processes.PRIORITY_NAMES.items():
            if key != "realtime":
                pm.addAction(title, lambda k=key: self._priority(k))
        m.addSeparator()
        m.addAction("Открыть расположение файла", self._open)
        m.addAction("Проверить цифровую подпись", self._signature)
        m.addSeparator()
        m.addAction("Блокировать запуск (правило)", self._block)
        m.addAction("Всегда понижать приоритет (правило)", self._low_rule)
        m.exec(self.view.viewport().mapToGlobal(pos))
