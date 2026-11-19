"""Службы Windows."""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel

from ...core import services
from ..common import Column, DictTableModel, Page, confirm, error_box, info_box, make_table, run_bg, selected_rows


class ServicesPage(Page):
    title = "Службы"
    subtitle = "Фоновые службы Windows: запуск, остановка, тип запуска, рекомендации по отключению"

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.model = DictTableModel([
            Column("display", "Служба", 260), Column("name", "Имя", 140), Column("status", "Состояние", 100),
            Column("start", "Запуск", 110), Column("recommend", "Рекомендуется", 110), Column("risk", "Риск", 70),
            Column("note", "Пояснение", 340), Column("description", "Описание", 400),
        ], key="name")
        self.view, self.proxy = make_table(self.model)
        self.filter = QComboBox()
        self.filter.setFixedWidth(260)
        self.filter.addItems(["Все службы", "Работающие", "Рекомендации по отключению", "Автозапуск, но остановлены"])
        self.filter.currentIndexChanged.connect(self._apply_filter)
        self.toolbar.addWidget(self.filter)
        self.toolbar.addWidget(self.search_box(self.proxy))
        self.tool("Обновить", self.refresh)
        bar = QHBoxLayout()
        bar.addWidget(self.button("Запустить", self._start))
        bar.addWidget(self.button("Остановить", self._stop))
        bar.addWidget(QLabel("Тип запуска:"))
        self.mode = QComboBox()
        self.mode.addItems(["Автоматически", "Автоматически (отложенно)", "Вручную", "Отключена"])
        bar.addWidget(self.mode)
        bar.addWidget(self.button("Применить", self._set_mode))
        bar.addSpacing(16)
        bar.addWidget(self.button("Применить рекомендации к выбранным", self._apply_rec, primary=True))
        bar.addStretch(1)
        self.root.addLayout(bar)
        self.root.addWidget(self.view, 1)
        self.finish_layout()

    def refresh(self) -> None:
        self.set_status("Чтение служб…")
        run_bg(services.list_services, on_done=self._show, on_error=self.set_status)

    def _show(self, rows: list[dict[str, Any]]) -> None:
        pal = self.ctx.settings.palette()
        for r in rows:
            if r["recommend_raw"] and r["raw_start"] != r["recommend_raw"] and \
                    not (r["recommend_raw"] == "manual" and r["raw_start"] == "disabled"):
                r["_color"] = pal["warn"]
            elif r["protected"]:
                r["_color"] = pal["muted"]
        self.model.set_rows(rows)
        running = sum(1 for r in rows if r["raw_status"] == "running")
        rec = sum(1 for r in rows if r.get("_color") == pal["warn"])
        self.set_status(f"Служб: {len(rows)} · работает: {running} · можно оптимизировать: {rec}. "
                        "Изменения требуют прав администратора.")

    def _apply_filter(self) -> None:
        tests = {
            1: lambda r: r["raw_status"] == "running",
            2: lambda r: bool(r["recommend_raw"]),
            3: lambda r: r["raw_start"] == "automatic" and r["raw_status"] == "stopped",
        }
        self.proxy.extra = tests.get(self.filter.currentIndex())
        self.proxy.invalidate()

    def _sel(self) -> list[dict[str, Any]]:
        rows = selected_rows(self.view)
        if not rows:
            info_box(self, "Службы", "Выберите службу.")
        return rows

    def _run(self, fn: Any, rows: list[dict[str, Any]], *args: Any) -> None:
        def work() -> list[str]:
            return [f"{r['display']}: {e}" for r in rows if (e := fn(r["name"], *args))]

        def done(errors: list[str]) -> None:
            if errors:
                error_box(self, "Службы", "\n".join(errors))
            self.refresh()
        self.set_status("Выполнение…")
        run_bg(work, on_done=done)

    def _start(self) -> None:
        self._run(services.start, self._sel())

    def _stop(self) -> None:
        rows = self._sel()
        if rows and confirm(self, "Службы", "Остановить выбранные службы?"):
            self._run(services.stop, rows)

    def _set_mode(self) -> None:
        mode = ["auto", "delayed-auto", "demand", "disabled"][self.mode.currentIndex()]
        rows = self._sel()
        if rows and (mode != "disabled" or confirm(self, "Службы", "Отключить выбранные службы? "
                                                   "Некоторые функции Windows могут перестать работать.")):
            self._run(services.set_start_type, rows, mode)

    def _apply_rec(self) -> None:
        rows = [r for r in selected_rows(self.view) if r["recommend_raw"]]
        if not rows:
            info_box(self, "Службы", "Выберите службы с рекомендациями (фильтр «Рекомендации по отключению»).")
            return
        text = "\n".join(f"{r['display']} → {r['recommend']} (риск: {r['risk']})" for r in rows)
        if confirm(self, "Применить рекомендации", text):
            names = [r["name"] for r in rows]
            def done(errs: list[str]) -> None:
                if errs:
                    error_box(self, "Службы", "\n".join(errs))
                self.refresh()
            run_bg(services.apply_recommendations, names, on_done=done)
