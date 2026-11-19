"""Очистка реестра с резервным копированием."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from PySide6.QtWidgets import QCheckBox, QFileDialog, QHBoxLayout, QLabel

from ...core import registry
from ...core.winutil import backups_dir, is_admin, open_in_explorer, reg_import
from ..common import Column, DictTableModel, Page, confirm, error_box, info_box, make_table, run_bg


class RegistryPage(Page):
    title = "Реестр"
    subtitle = "Поиск записей, ссылающихся на удалённые файлы. Перед исправлением создаётся .reg-копия."

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.issues: list[registry.RegIssue] = []
        self.checks: dict[str, QCheckBox] = {}
        opts = QHBoxLayout()
        opts.addWidget(QLabel("Проверять:"))
        for sid, title, _fn in registry.SCANNERS:
            cb = QCheckBox(title)
            cb.setChecked(True)
            self.checks[sid] = cb
            opts.addWidget(cb)
        opts.addStretch(1)
        self.root.addLayout(opts)
        self.model = DictTableModel([
            Column("sel", "", 34, checkable=True), Column("category", "Категория", 200),
            Column("description", "Проблема", 300), Column("location", "Расположение", 420), Column("target", "Ссылка", 300),
        ], key="id")
        self.view, self.proxy = make_table(self.model)
        self.scan_btn = self.tool("Сканировать", self.refresh)
        self.fix_btn = self.tool("Исправить отмеченные", self._fix, primary=True)
        bar = QHBoxLayout()
        bar.addWidget(self.button("Отметить всё", lambda: self._mark(True)))
        bar.addWidget(self.button("Снять всё", lambda: self._mark(False)))
        bar.addWidget(self.button("Папка резервных копий", lambda: open_in_explorer(str(backups_dir()))))
        bar.addWidget(self.button("Восстановить из .reg…", self._restore))
        bar.addStretch(1)
        note = QLabel("Очистка реестра почти не ускоряет ПК — она убирает «хвосты» удалённых программ. "
                      "Мы трогаем только записи с несуществующими путями.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        self.root.addLayout(bar)
        self.root.addWidget(note)
        self.root.addWidget(self.view, 1)
        self.finish_layout()

    def on_show(self) -> None:
        pass

    def refresh(self) -> None:
        enabled = {k for k, cb in self.checks.items() if cb.isChecked()}
        self.scan_btn.setEnabled(False)
        self.set_status("Сканирование реестра…")
        run_bg(registry.scan, enabled, on_done=self._show, on_error=self._fail)

    def _fail(self, msg: str) -> None:
        self.scan_btn.setEnabled(True)
        self.set_status(msg)

    def _show(self, issues: list[registry.RegIssue]) -> None:
        self.scan_btn.setEnabled(True)
        self.issues = issues
        rows = []
        for i, issue in enumerate(issues):
            d = asdict(issue)
            d.update(id=i, location=issue.location, sel="")
            rows.append(d)
        self.model.set_rows(rows)
        self.model.checked = set(range(len(rows)))
        self.model.layoutChanged.emit()
        self.set_status(f"Найдено проблем: {len(issues)}" + ("" if is_admin() else
                        " · записи HKLM можно исправить только от имени администратора"))

    def _mark(self, on: bool) -> None:
        self.model.checked = set(range(len(self.issues))) if on else set()
        self.model.layoutChanged.emit()

    def _fix(self) -> None:
        chosen = [self.issues[i] for i in sorted(self.model.checked) if i < len(self.issues)]
        if not chosen:
            info_box(self, "Реестр", "Нет отмеченных записей. Сначала выполните сканирование.")
            return
        if not confirm(self, "Реестр", f"Исправить записей: {len(chosen)}?\nПеред изменением будет создана резервная копия."):
            return

        def done(res: tuple[int, list[str], Any]) -> None:
            fixed, errors, folder = res
            text = f"Исправлено: {fixed}\nКопия: {folder}"
            if errors:
                text += "\n\nНе исправлено:\n" + "\n".join(errors[:15])
            info_box(self, "Реестр", text)
            self.refresh()
        run_bg(registry.fix, chosen, on_done=done)

    def _restore(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Файл резервной копии", str(backups_dir()), "Файлы реестра (*.reg)")
        if path and confirm(self, "Восстановление", f"Импортировать {path} в реестр?"):
            from pathlib import Path
            ok, out = reg_import(Path(path))
            (info_box if ok else error_box)(self, "Восстановление", out or ("Готово" if ok else "Ошибка"))
