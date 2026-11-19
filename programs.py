"""Установленные программы."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout

from ...core import programs
from ...core.winutil import human_bytes, open_in_explorer
from ..common import Column, DictTableModel, Page, confirm, error_box, info_box, make_table, run_bg, selected_rows


class ProgramsPage(Page):
    title = "Программы"
    subtitle = "Установленные приложения: размер, дата установки, удаление"

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.model = DictTableModel([
            Column("name", "Название", 320), Column("publisher", "Издатель", 200), Column("version", "Версия", 110),
            Column("size", "Размер", 90, lambda v: human_bytes(v) if v else "", Qt.AlignmentFlag.AlignRight),
            Column("date", "Установлена", 100), Column("scope", "Для", 100), Column("location", "Папка", 300),
        ], key="key")
        self.view, self.proxy = make_table(self.model)
        self.toolbar.addWidget(self.search_box(self.proxy))
        self.tool("Обновить", self.refresh)
        bar = QHBoxLayout()
        bar.addWidget(self.button("Удалить программу", self._uninstall, primary=True))
        bar.addWidget(self.button("Открыть папку", self._open))
        bar.addWidget(self.button("Программы и компоненты Windows", lambda: ctx.run_action("appwiz")))
        bar.addStretch(1)
        self.root.addLayout(bar)
        self.root.addWidget(self.view, 1)
        self.finish_layout()

    def refresh(self) -> None:
        self.set_status("Чтение списка программ…")
        run_bg(programs.list_programs, on_done=self._show, on_error=self.set_status)

    def _show(self, rows: list[dict[str, Any]]) -> None:
        pal = self.ctx.settings.palette()
        for r in rows:
            if r["broken"]:
                r["_color"] = pal["warn"]
                r["_tip"] = "Папка программы не найдена — возможно, остаток после удаления"
        self.model.set_rows(rows)
        total = sum(r["size"] for r in rows)
        self.set_status(f"Программ: {len(rows)} · общий размер (по данным установщиков): {human_bytes(total)}")

    def _uninstall(self) -> None:
        rows = selected_rows(self.view)
        if not rows:
            info_box(self, "Программы", "Выберите программу.")
            return
        r = rows[0]
        if confirm(self, "Удаление", f"Запустить удаление «{r['name']}»?"):
            err = programs.uninstall(r)
            if err:
                error_box(self, "Удаление", err)

    def _open(self) -> None:
        for r in selected_rows(self.view)[:3]:
            if r["location"]:
                open_in_explorer(r["location"])
