"""Автозагрузка: просмотр, отключение (обратимо), удаление с резервной копией."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QFileDialog, QHBoxLayout

from ...core import startup
from ...core.security import check_signatures
from ...core.winutil import backups_dir, is_admin, open_in_explorer
from ..common import Column, DictTableModel, Page, confirm, error_box, info_box, make_table, run_bg, selected_rows


class StartupPage(Page):
    title = "Автозагрузка"
    subtitle = "Программы, которые запускаются вместе с Windows. Отключение обратимо (как в Диспетчере задач)."

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.items: dict[str, startup.StartupItem] = {}
        self.signers: dict[str, tuple[str, str]] = {}
        self.model = DictTableModel([
            Column("state", "Состояние", 100), Column("name", "Имя", 220), Column("signer", "Издатель / подпись", 220),
            Column("location", "Где прописано", 220), Column("command", "Команда", 420),
        ], key="uid")
        self.view, self.proxy = make_table(self.model)
        self.view.doubleClicked.connect(lambda _i: self._toggle())
        self.show_tasks = QCheckBox("Задания планировщика")
        self.show_tasks.setChecked(True)
        self.show_tasks.toggled.connect(lambda _v: self.refresh())
        self.toolbar.addWidget(self.show_tasks)
        self.toolbar.addWidget(self.search_box(self.proxy))
        self.tool("Обновить", self.refresh)
        bar = QHBoxLayout()
        bar.addWidget(self.button("Отключить", lambda: self._set(False), primary=True))
        bar.addWidget(self.button("Включить", lambda: self._set(True)))
        bar.addWidget(self.button("Удалить запись", self._delete, tip="С резервной копией в папке backups"))
        bar.addWidget(self.button("Восстановить из копии…", self._restore))
        bar.addWidget(self.button("Открыть папку файла", self._open))
        bar.addWidget(self.button("Проверить подписи", self._signatures))
        bar.addStretch(1)
        self.root.addLayout(bar)
        self.root.addWidget(self.view, 1)
        self.finish_layout()

    def refresh(self) -> None:
        self.set_status("Чтение автозагрузки…")
        run_bg(startup.list_items, self.show_tasks.isChecked(), on_done=self._show,
               on_error=lambda e: self.set_status(e))

    def _show(self, items: list[startup.StartupItem]) -> None:
        pal = self.ctx.settings.palette()
        self.items = {i.uid: i for i in items}
        rows = []
        for i in items:
            sig = self.signers.get(i.path)
            signer = f"{sig[1] or '—'} ({sig[0]})" if sig else (i.extra.get("author") or "")
            row = {"uid": i.uid, "state": "Включено" if i.enabled else "Отключено", "name": i.name,
                   "signer": signer, "location": i.location, "command": i.command}
            if not i.exists:
                row["state"] = "Файл не найден"
                row["_color"] = pal["bad"]
                row["_tip"] = "Программа удалена, а запись осталась — её можно удалить."
            elif not i.enabled:
                row["_color"] = pal["muted"]
            elif sig and sig[0] != "Подписан":
                row["_color"] = pal["warn"]
            rows.append(row)
        self.model.set_rows(rows)
        on = sum(1 for i in items if i.enabled)
        broken = sum(1 for i in items if not i.exists)
        note = "" if is_admin() else " · записи HKLM и задания требуют прав администратора"
        self.set_status(f"Записей: {len(items)} · включено: {on} · битых: {broken}{note}")

    def _selected(self) -> list[startup.StartupItem]:
        res = [self.items[r["uid"]] for r in selected_rows(self.view) if r["uid"] in self.items]
        if not res:
            info_box(self, "Автозагрузка", "Выберите запись.")
        return res

    def _set(self, enabled: bool) -> None:
        errors = [f"{i.name}: {e}" for i in self._selected() if (e := startup.set_enabled(i, enabled))]
        if errors:
            error_box(self, "Автозагрузка", "\n".join(errors))
        self.refresh()

    def _toggle(self) -> None:
        sel = self._selected()
        if sel:
            self._set(not sel[0].enabled)

    def _delete(self) -> None:
        sel = self._selected()
        if not sel or not confirm(self, "Удаление", "Удалить записи автозагрузки?\n" + "\n".join(i.name for i in sel) +
                                  "\n\nРезервная копия будет сохранена — запись можно восстановить."):
            return
        errors = [f"{i.name}: {e}" for i in sel if (e := startup.delete_item(i))]
        if errors:
            error_box(self, "Автозагрузка", "\n".join(errors))
        self.refresh()

    def _restore(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Резервная копия записи", str(backups_dir()), "JSON (startup_*.json)")
        if path:
            err = startup.restore_backup(Path(path))
            if err:
                error_box(self, "Восстановление", err)
            else:
                info_box(self, "Восстановление", "Запись восстановлена.")
            self.refresh()

    def _open(self) -> None:
        for i in self._selected()[:3]:
            open_in_explorer(i.path)

    def _signatures(self) -> None:
        paths = [i.path for i in self.items.values() if i.path]
        self.set_status("Проверка цифровых подписей…")

        def done(res: dict[str, tuple[str, str]]) -> None:
            self.signers.update(res)
            self._show(list(self.items.values()))
        run_bg(check_signatures, paths, on_done=done)

    def sort_default(self) -> None:
        self.view.sortByColumn(1, Qt.SortOrder.AscendingOrder)
