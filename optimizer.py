"""Оптимизация: твики, электропитание, память, системные инструменты."""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core import optimizer
from ...core.winutil import human_bytes, is_admin
from ..common import Page, confirm, error_box, info_box, run_bg


class OptimizerPage(Page):
    title = "Оптимизация"
    subtitle = "Ускорение интерфейса, загрузки, игр; освобождение памяти; схемы питания; системные инструменты"

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self._updating = False
        self.tool("Создать точку восстановления", self._restore_point,
                  tip="Рекомендуется перед изменением настроек системы")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(12)

        # ---- быстрые действия
        quick = self._card("БЫСТРЫЕ ДЕЙСТВИЯ")
        grid = QGridLayout()
        actions = [
            ("⚡ Оптимизация в один клик", self.ctx.quick_optimize, True,
             "Очистка временных файлов и кэшей, освобождение ОЗУ, сброс DNS"),
            ("Освободить оперативную память", self._free_ram, False,
             "Сжатие рабочих наборов процессов и очистка standby-списка (нужны права администратора)"),
            ("Сбросить кэш DNS", lambda: self._simple(optimizer.flush_dns, "Кэш DNS очищен"), False, ""),
            ("Перезапустить Проводник", lambda: self._simple(optimizer.restart_explorer, "Проводник перезапущен"), False,
             "Помогает при зависшей панели задач"),
            ("Перестроить кэш значков", lambda: self._simple(optimizer.rebuild_icon_cache, "Кэш значков перестроен"),
             False, "Исправляет неправильные или пустые значки"),
            ("Оптимизировать диски (TRIM/дефрагментация)", self._drives, False,
             "SSD — команда TRIM, HDD — дефрагментация. Нужны права администратора"),
        ]
        for i, (title, slot, primary, tip) in enumerate(actions):
            b = QPushButton(title)
            if primary:
                b.setObjectName("Primary")
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, s=slot: s())
            grid.addWidget(b, i // 3, i % 3)
        self._lay(quick).addLayout(grid)
        lay.addWidget(quick)

        # ---- электропитание
        power = self._card("СХЕМА ЭЛЕКТРОПИТАНИЯ")
        prow = QHBoxLayout()
        self.plans = QComboBox()
        self.plans.setMinimumWidth(320)
        prow.addWidget(self.plans)
        prow.addWidget(self.button("Активировать", self._set_plan, primary=True))
        prow.addWidget(self.button("Добавить «Максимальная производительность»", self._ultimate))
        prow.addStretch(1)
        self._lay(power).addLayout(prow)
        lay.addWidget(power)

        # ---- твики
        self.tweak_boxes: dict[str, QCheckBox] = {}
        groups: dict[str, QFrame] = {}
        for t in optimizer.TWEAKS:
            card = groups.get(t.group)
            if card is None:
                card = self._card(t.group.upper())
                groups[t.group] = card
                lay.addWidget(card)
            row = QHBoxLayout()
            cb = QCheckBox(t.title + ("  🛡" if t.admin else "") + ("  ⟳" if t.reboot else ""))
            cb.setToolTip(t.description + ("\nТребуются права администратора." if t.admin else ""))
            cb.toggled.connect(lambda on, tw=t, box=cb: self._toggle(tw, on, box))
            desc = QLabel(t.description)
            desc.setObjectName("Muted")
            desc.setWordWrap(True)
            row.addWidget(cb, 2)
            row.addWidget(desc, 3)
            self._lay(card).addLayout(row)
            self.tweak_boxes[t.id] = cb
        legend = QLabel("🛡 — нужны права администратора. Все твики обратимы: снимите флажок, чтобы вернуть значение Windows по умолчанию.")
        legend.setObjectName("Muted")
        legend.setWordWrap(True)
        lay.addWidget(legend)
        lay.addStretch(1)
        scroll.setWidget(host)
        self.root.addWidget(scroll, 1)
        self.finish_layout()

    def _card(self, title: str) -> QFrame:
        f = QFrame()
        f.setObjectName("Card")
        v = QVBoxLayout(f)
        v.setContentsMargins(14, 10, 14, 12)
        v.setSpacing(8)
        t = QLabel(title)
        t.setObjectName("CardTitle")
        v.addWidget(t)
        return f

    @staticmethod
    def _lay(frame: QFrame) -> QVBoxLayout:
        lay = frame.layout()
        assert isinstance(lay, QVBoxLayout)
        return lay

    def refresh(self) -> None:
        self._updating = True
        for t in optimizer.TWEAKS:
            cb = self.tweak_boxes[t.id]
            st = t.state()
            cb.setChecked(bool(st))
            cb.setEnabled(st is not None and (is_admin() or not t.admin))
        self._updating = False
        run_bg(optimizer.power_plans, on_done=self._plans)

    def _plans(self, plans: list[dict[str, Any]]) -> None:
        self.plans.clear()
        for p in plans:
            self.plans.addItem(("● " if p["active"] else "   ") + p["name"], p["guid"])
            if p["active"]:
                self.plans.setCurrentIndex(self.plans.count() - 1)

    def _toggle(self, tweak: optimizer.Tweak, on: bool, box: QCheckBox) -> None:
        if self._updating:
            return
        err = tweak.apply(on)
        if err:
            error_box(self, "Твик", err)
            self._updating = True
            box.setChecked(not on)
            self._updating = False
        else:
            self.set_status(f"{'Применено' if on else 'Отменено'}: {tweak.title}" +
                            (" (нужна перезагрузка)" if tweak.reboot else ""))

    def _simple(self, fn: Any, ok_text: str) -> None:
        run_bg(fn, on_done=lambda err: error_box(self, "Ошибка", err) if err else self.set_status(ok_text))

    def _free_ram(self) -> None:
        self.set_status("Освобождение памяти…")

        def done(res: tuple[int, list[str]]) -> None:
            freed, notes = res
            self.set_status(f"Освобождено {human_bytes(freed)}")
            info_box(self, "Память", f"Доступно больше на {human_bytes(freed)}\n\n" + "\n".join(notes))
        run_bg(optimizer.free_memory, on_done=done)

    def _drives(self) -> None:
        if not is_admin():
            error_box(self, "Диски", "Нужны права администратора.")
            return
        self.set_status("Оптимизация дисков… это может занять время")
        run_bg(optimizer.optimize_drives, on_done=lambda r: info_box(self, "Оптимизация дисков", r[1]))

    def _set_plan(self) -> None:
        guid = self.plans.currentData()
        if guid:
            err = optimizer.set_power_plan(guid)
            if err:
                error_box(self, "Электропитание", err)
            run_bg(optimizer.power_plans, on_done=self._plans)

    def _ultimate(self) -> None:
        err = optimizer.add_ultimate_plan()
        if err:
            error_box(self, "Электропитание", err)
        run_bg(optimizer.power_plans, on_done=self._plans)

    def _restore_point(self) -> None:
        if confirm(self, "Точка восстановления", "Создать точку восстановления системы? Это займёт до минуты."):
            self.set_status("Создание точки восстановления…")
            run_bg(optimizer.create_restore_point, on_done=lambda r: (info_box if r[0] else error_box)(
                self, "Точка восстановления", r[1]))

