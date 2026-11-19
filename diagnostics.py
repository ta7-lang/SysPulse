"""Диагностика системы: поиск проблем и системные средства восстановления."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ...core import cleaner, diagnostics, hardware, processes, security, startup
from ..common import Page, run_bg


def full_check(snapshot: dict[str, Any] | None, hw_cache: dict[str, Any] | None,
               progress: Any = None) -> dict[str, Any]:
    def step(msg: str) -> None:
        if progress:
            progress(msg)

    step("Оборудование и S.M.A.R.T.…")
    hw = hw_cache or hardware.collect_hardware()
    step("Безопасность…")
    sec = security.security_status()
    step("Журналы событий: сбои, BSOD, загрузка…")
    events = diagnostics.event_summary()
    step("Автозагрузка…")
    st_items = startup.list_items(include_tasks=False)
    st_count = sum(1 for i in st_items if i.enabled)
    step("Оценка объёма мусора…")
    junk = 0
    for cat in cleaner.build_categories():
        if cat.default and not cat.special:
            junk += cleaner.scan_category(cat)[0]
    junk += cleaner.recycle_bin_size()[0]
    step("Зависшие программы…")
    _vis, hung_pids = processes.window_pids()
    hung = []
    if hung_pids:
        import psutil
        for pid in hung_pids:
            try:
                hung.append(psutil.Process(pid).name())
            except psutil.Error:
                pass
    step("Анализ…")
    issues = diagnostics.analyze(snapshot, hw, sec, events, st_count, junk, hung)
    return {"issues": issues, "hw": hw, "sec": sec, "events": events, "junk": junk, "startup": st_count,
            "score": diagnostics.health_score(issues)}


class DiagnosticsPage(Page):
    title = "Диагностика"
    subtitle = "Поиск проблем с оборудованием, драйверами, дисками, программами, загрузкой и безопасностью"

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.run_btn = self.tool("Запустить проверку", self.refresh, primary=True)

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.summary = QLabel("Нажмите «Запустить проверку». Анализ занимает 10–60 секунд.")
        self.summary.setObjectName("Muted")
        self.summary.setWordWrap(True)
        ll.addWidget(self.summary)
        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(6)
        self.list_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.list_host)
        ll.addWidget(scroll, 1)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)
        tools = QFrame()
        tools.setObjectName("Card")
        tl = QGridLayout(tools)
        tl.setContentsMargins(12, 10, 12, 12)
        cap = QLabel("СРЕДСТВА ВОССТАНОВЛЕНИЯ")
        cap.setObjectName("CardTitle")
        tl.addWidget(cap, 0, 0, 1, 2)
        actions = [
            ("Проверить системные файлы (SFC)", "sfc", "sfc /scannow — находит и восстанавливает повреждённые файлы Windows"),
            ("Восстановить образ (DISM)", "dism", "DISM /RestoreHealth — чинит хранилище компонентов"),
            ("Проверить диск (CHKDSK)", "chkdsk", "chkdsk C: /scan — онлайн-проверка файловой системы"),
            ("Проверить память", "mdsched", "Средство диагностики памяти Windows (перезагрузка)"),
            ("Сбросить сеть", "netreset", "Сброс Winsock и TCP/IP — при проблемах с интернетом"),
            ("Диспетчер устройств", "devmgmt", "Открыть диспетчер устройств"),
            ("Журнал событий", "eventvwr", "Открыть просмотр событий"),
            ("Монитор надёжности", "perfmon_rel", "История сбоев Windows"),
        ]
        for i, (title, act, tip) in enumerate(actions):
            b = QPushButton(title)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, a=act: self.ctx.run_action(a))
            tl.addWidget(b, 1 + i // 2, i % 2)
        rl.addWidget(tools)
        self.details = QLabel("")
        self.details.setObjectName("Muted")
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.RichText)
        self.details.setAlignment(Qt.AlignmentFlag.AlignTop)
        det = QFrame()
        det.setObjectName("Card")
        dl = QVBoxLayout(det)
        c2 = QLabel("ЖУРНАЛЫ И ЗАГРУЗКА")
        c2.setObjectName("CardTitle")
        dl.addWidget(c2)
        dl.addWidget(self.details, 1)
        rl.addWidget(det, 1)
        split.addWidget(right)
        split.setSizes([640, 420])
        self.root.addWidget(split, 1)
        self.finish_layout()

    def on_show(self) -> None:
        pass  # проверка запускается только по кнопке

    def refresh(self) -> None:
        self.run_btn.setEnabled(False)
        self.set_status("Проверка…")
        run_bg(full_check, self.ctx.last_snapshot, self.ctx.hw_info, on_done=self._show,
               on_progress=self.set_status, on_error=self._fail)

    def _fail(self, msg: str) -> None:
        self.run_btn.setEnabled(True)
        self.set_status(f"Ошибка: {msg}")

    def _show(self, res: dict[str, Any]) -> None:
        self.run_btn.setEnabled(True)
        self.ctx.hw_info = res["hw"]
        pal = self.ctx.settings.palette()
        colors = {diagnostics.CRITICAL: pal["bad"], diagnostics.WARNING: pal["warn"],
                  diagnostics.INFO: pal["accent2"], diagnostics.OK: pal["good"]}
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()
        issues: list[diagnostics.Issue] = res["issues"]
        for idx, issue in enumerate(issues):
            card = QFrame()
            card.setObjectName("Card")
            card.setStyleSheet(f"#Card {{ border-left: 4px solid {colors[issue.severity]}; }}")
            h = QHBoxLayout(card)
            h.setContentsMargins(12, 8, 10, 8)
            v = QVBoxLayout()
            v.setSpacing(2)
            top = QLabel(f"<span style='color:{colors[issue.severity]}; font-weight:600'>"
                         f"{diagnostics.SEVERITY_RU[issue.severity].upper()}</span>"
                         f"<span style='color:{pal['muted']}'> · {issue.category}</span>")
            title = QLabel(issue.title)
            title.setWordWrap(True)
            title.setStyleSheet("font-weight:600")
            v.addWidget(top)
            v.addWidget(title)
            if issue.detail:
                d = QLabel(issue.detail)
                d.setObjectName("Muted")
                d.setWordWrap(True)
                v.addWidget(d)
            h.addLayout(v, 1)
            if issue.action:
                b = QPushButton(issue.action_label or "Исправить")
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _=False, a=issue.action: self.ctx.run_action(a))
                h.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
            self.list_layout.insertWidget(idx, card)

        crit = sum(1 for i in issues if i.severity == diagnostics.CRITICAL)
        warn = sum(1 for i in issues if i.severity == diagnostics.WARNING)
        summary = f"Оценка: {res['score']}/100 · критичных: {crit} · предупреждений: {warn}"
        self.summary.setText(summary)
        self.ctx.set_health(res["score"], f"Критичных: {crit}\nПредупреждений: {warn}")
        self.set_status("Проверка завершена")

        ev = res["events"]
        parts = []
        boots = [b for b in ev.get("boot", []) if isinstance(b, dict) and b.get("ms")]
        if boots:
            parts.append("<b>Время загрузки Windows</b> (последние):<br>" + "<br>".join(
                f"{b['t']}: {b['ms'] / 1000:.1f} с (+{(b.get('post') or 0) / 1000:.0f} с после входа)" for b in boots))
        else:
            parts.append("<b>Время загрузки</b>: доступно при запуске от имени администратора.")
        crashes = [c for c in ev.get("appCrashes", []) if isinstance(c, dict)]
        if crashes:
            parts.append("<b>Сбои программ за 7 дней:</b><br>" + "<br>".join(
                f"{c.get('app')}: {c.get('n')}" for c in crashes))
        hangs = [c for c in ev.get("appHangs", []) if isinstance(c, dict)]
        if hangs:
            parts.append("<b>Зависания программ за 7 дней:</b><br>" + "<br>".join(
                f"{c.get('app')}: {c.get('n')}" for c in hangs))
        errs = [e for e in ev.get("sysErrors", []) if isinstance(e, dict)]
        if errs:
            parts.append("<b>Источники системных ошибок за сутки:</b><br>" + "<br>".join(
                f"{e.get('src')}: {e.get('n')}" for e in errs))
        if ev.get("bsod"):
            parts.append("<b>BSOD за 30 дней:</b> " + ", ".join(map(str, ev["bsod"][:5])))
        parts.append(f"<b>Мусор:</b> {res['junk'] / 1024 ** 3:.2f} ГБ · <b>Автозагрузка:</b> {res['startup']} программ")
        self.details.setText("<br><br>".join(parts))
