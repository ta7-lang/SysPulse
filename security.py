"""Безопасность: антивирус, брандмауэр, проверка Microsoft Defender (офлайн), hosts."""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout

from ...core import security
from ...core.winutil import powershell
from ..common import Page, confirm, info_box, run_bg


class SecurityPage(Page):
    title = "Безопасность"
    subtitle = "Состояние защиты, проверка на вирусы встроенным антивирусом Windows (работает без интернета)"

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.tool("Обновить", self.refresh)
        cards = QGridLayout()
        cards.setSpacing(10)
        self.cards: dict[str, tuple[QLabel, QLabel]] = {}
        for i, (key, title) in enumerate((("av", "Антивирус"), ("rtp", "Защита в реальном времени"),
                                          ("sig", "Антивирусные базы"), ("fw", "Брандмауэр"),
                                          ("uac", "Контроль учётных записей"), ("upd", "Обновления Windows"))):
            f = QFrame()
            f.setObjectName("Card")
            v = QVBoxLayout(f)
            v.setContentsMargins(12, 8, 12, 10)
            t = QLabel(title.upper())
            t.setObjectName("CardTitle")
            val = QLabel("—")
            val.setStyleSheet("font-size: 16px; font-weight: 600")
            sub = QLabel("")
            sub.setObjectName("Muted")
            sub.setWordWrap(True)
            v.addWidget(t)
            v.addWidget(val)
            v.addWidget(sub)
            cards.addWidget(f, i // 3, i % 3)
            self.cards[key] = (val, sub)
        self.root.addLayout(cards)

        scan = QHBoxLayout()
        self.scan_btns = [
            self.button("Быстрая проверка", lambda: self._scan("quick"), primary=True),
            self.button("Полная проверка", lambda: self._scan("full")),
            self.button("Проверить папку…", self._scan_folder),
            self.button("Обновить базы", self._update, tip="Требуется интернет; без него используются текущие базы"),
            self.button("Удалить найденные угрозы", self._remove),
            self.button("Включить брандмауэр", lambda: ctx.run_action("firewall_on")),
        ]
        for b in self.scan_btns:
            scan.addWidget(b)
        scan.addStretch(1)
        self.root.addLayout(scan)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Здесь появятся результаты проверок, обнаруженные угрозы и подозрительные записи hosts.")
        self.root.addWidget(self.log, 1)
        self.finish_layout()

    def refresh(self) -> None:
        self.set_status("Получение состояния защиты…")
        run_bg(security.security_status, on_done=self._show, on_error=self.set_status)

    def _card(self, key: str, text: str, sub: str, state: str) -> None:
        pal = self.ctx.settings.palette()
        val, s = self.cards[key]
        val.setText(text)
        val.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {pal[state]}")
        s.setText(sub)

    def _show(self, st: dict[str, Any]) -> None:
        d = st.get("defender") or {}
        avs = st.get("av") or []
        names = ", ".join(a.get("name", "") for a in avs) or ("Microsoft Defender" if d else "не найден")
        any_on = any(a.get("enabled") for a in avs) or bool(d.get("av"))
        self._card("av", "Активен" if any_on else "Отключён", names, "good" if any_on else "bad")
        if d:
            self._card("rtp", "Включена" if d.get("rtp") else "Выключена",
                       f"Последняя быстрая проверка: {d.get('quickAge', '—')} дн. назад", "good" if d.get("rtp") else "bad")
            age = d.get("sigAge") or 0
            self._card("sig", f"{age} дн.", f"Обновлены: {d.get('sigDate', '—')}", "good" if age <= 3 else
                       "warn" if age <= 7 else "bad")
        else:
            third = [a for a in avs if "defender" not in str(a.get("name", "")).lower()]
            self._card("rtp", "Сторонний антивирус" if third else "Нет данных", names, "good" if third else "warn")
            upd = all(a.get("updated") for a in avs) if avs else False
            self._card("sig", "Актуальны" if upd else "Нет данных", "", "good" if upd else "warn")
        fw = st.get("firewall") or []
        off = [f["name"] for f in fw if not f.get("enabled")]
        self._card("fw", "Включён" if fw and not off else "Отключён" if off else "Нет данных",
                   ("Выключены профили: " + ", ".join(off)) if off else "Все профили активны",
                   "good" if fw and not off else "bad" if off else "warn")
        self._card("uac", "Включён" if st.get("uac") else "Отключён", "", "good" if st.get("uac") else "bad")
        days = st.get("lastUpdateDays")
        self._card("upd", f"{days} дн. назад" if days is not None else "Нет данных",
                   f"Последнее обновление: {st.get('lastUpdate', '—')}",
                   "good" if (days or 99) <= 30 else "warn" if (days or 99) <= 60 else "bad")
        lines = []
        threats = st.get("threats") or []
        if threats:
            lines.append("=== ОБНАРУЖЕННЫЕ УГРОЗЫ (журнал Defender) ===")
            for t in threats:
                lines.append(f"• {t.get('name')} — опасность: {security.SEVERITY.get(t.get('severity', 0), '?')}, "
                             f"{'активна' if t.get('active') else 'обезврежена'}\n   {t.get('resources', '')}")
        hosts = st.get("hosts") or []
        if hosts:
            lines.append("\n=== ЗАПИСИ ФАЙЛА HOSTS ===")
            for h in hosts:
                mark = "  ⚠ ПОДОЗРИТЕЛЬНО" if h["suspicious"] else ""
                lines.append(f"{h['ip']:<16} {h['host']}{mark}")
        if lines:
            self.log.setPlainText("\n".join(lines))
        self.set_status("Состояние обновлено" + ("" if security.defender_available() else
                                                  " · Microsoft Defender не найден — проверка недоступна"))

    def _busy(self, on: bool) -> None:
        for b in self.scan_btns:
            b.setEnabled(not on)

    def _append(self, text: str) -> None:
        self.log.appendPlainText(text)

    def _scan(self, kind: str, path: str = "") -> None:
        if kind == "full" and not confirm(self, "Полная проверка", "Полная проверка может занять 1–3 часа. Начать?"):
            return
        self._busy(True)
        names = {"quick": "Быстрая проверка", "full": "Полная проверка", "custom": f"Проверка {path}"}
        self._append(f"\n▶ {names[kind]} запущена…")
        self.set_status(f"{names[kind]} выполняется — можно продолжать работу")

        def done(res: tuple[bool, str]) -> None:
            self._busy(False)
            self._append(res[1])
            self.set_status("Проверка завершена")
            self.ctx.notify("Проверка на вирусы", res[1].splitlines()[0] if res[1] else "Готово")
            self.refresh()
        def fail(msg: str) -> None:
            self._busy(False)
            self._append(msg)
        run_bg(security.defender_scan, kind, path, on_done=done, on_error=fail)

    def _scan_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Папка для проверки")
        if path:
            self._scan("custom", path.replace("/", "\\"))

    def _update(self) -> None:
        self._busy(True)
        self._append("\n▶ Обновление антивирусных баз…")

        def done(res: tuple[bool, str]) -> None:
            self._busy(False)
            self._append(res[1] or "Готово")
            self.refresh()
        run_bg(security.defender_update, on_done=done)

    def _remove(self) -> None:
        if confirm(self, "Угрозы", "Удалить все активные угрозы, обнаруженные Microsoft Defender?"):
            def done(res: tuple[bool, str]) -> None:
                info_box(self, "Угрозы", res[1])
                self.refresh()
            run_bg(security.defender_remove_threats, on_done=done)


def enable_firewall() -> str:
    code, out = powershell("Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled True; if($?){'OK'}", 60)
    return "" if "OK" in out else (out or f"Ошибка {code}. Нужны права администратора.")

