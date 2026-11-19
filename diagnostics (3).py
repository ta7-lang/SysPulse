"""Диагностика: собирает данные из всех модулей и формирует список проблем с решениями."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psutil

from .hardware import DEVICE_ERRORS, battery_wear
from .winutil import IS_WINDOWS, as_list, ps_json, reg_get, reg_subkeys

log = logging.getLogger(__name__)

CRITICAL, WARNING, INFO, OK = "critical", "warning", "info", "ok"
SEVERITY_ORDER = {CRITICAL: 0, WARNING: 1, INFO: 2, OK: 3}
SEVERITY_RU = {CRITICAL: "Критично", WARNING: "Внимание", INFO: "Совет", OK: "Норма"}


@dataclass
class Issue:
    severity: str
    category: str
    title: str
    detail: str = ""
    action: str = ""        # идентификатор действия для интерфейса
    action_label: str = ""


_EVENTS_SCRIPT = r"""
$o = @{}
$since = (Get-Date).AddDays(-1)
$sys = Get-WinEvent -FilterHashtable @{ LogName = 'System'; Level = 1, 2; StartTime = $since } -MaxEvents 400
$o.sysErrors = @($sys | Group-Object ProviderName | Sort-Object Count -Descending | Select-Object -First 8 |
  ForEach-Object { @{ src = $_.Name; n = $_.Count; msg = (($_.Group | Select-Object -First 1).Message -split "`n")[0] } })
$crash = Get-WinEvent -FilterHashtable @{ LogName = 'Application'; Id = 1000; StartTime = (Get-Date).AddDays(-7) } -MaxEvents 300
$o.appCrashes = @($crash | ForEach-Object { $_.Properties[0].Value } | Group-Object | Sort-Object Count -Descending |
  Select-Object -First 8 | ForEach-Object { @{ app = $_.Name; n = $_.Count } })
$hang = Get-WinEvent -FilterHashtable @{ LogName = 'Application'; Id = 1002; StartTime = (Get-Date).AddDays(-7) } -MaxEvents 300
$o.appHangs = @($hang | ForEach-Object { $_.Properties[0].Value } | Group-Object | Sort-Object Count -Descending |
  Select-Object -First 8 | ForEach-Object { @{ app = $_.Name; n = $_.Count } })
$o.bsod = @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; Id = 1001; ProviderName = 'Microsoft-Windows-WER-SystemErrorReporting'; StartTime = (Get-Date).AddDays(-30) } |
  ForEach-Object { $_.TimeCreated.ToString('yyyy-MM-dd HH:mm') })
$o.unexpected = @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; Id = 41; ProviderName = 'Microsoft-Windows-Kernel-Power'; StartTime = (Get-Date).AddDays(-30) } |
  ForEach-Object { $_.TimeCreated.ToString('yyyy-MM-dd HH:mm') })
$o.boot = @(Get-WinEvent -FilterHashtable @{ LogName = 'Microsoft-Windows-Diagnostics-Performance/Operational'; Id = 100 } -MaxEvents 5 |
  ForEach-Object { $x = [xml]$_.ToXml(); @{ t = $_.TimeCreated.ToString('yyyy-MM-dd HH:mm');
    ms = [int](($x.Event.EventData.Data | Where-Object { $_.Name -eq 'BootTime' }).'#text');
    post = [int](($x.Event.EventData.Data | Where-Object { $_.Name -eq 'BootPostBootTime' }).'#text') } })
$o.diskErrors = @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'disk','Ntfs','stornvme','storahci'; Level = 1, 2, 3; StartTime = (Get-Date).AddDays(-7) } -MaxEvents 50).Count
$o.whea = @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-WHEA-Logger'; StartTime = (Get-Date).AddDays(-30) } -MaxEvents 50).Count
$o
"""


def event_summary() -> dict[str, Any]:
    if not IS_WINDOWS:
        return {}
    data = ps_json(_EVENTS_SCRIPT, timeout=120, depth=4)
    if not isinstance(data, dict):
        return {}
    for k in ("sysErrors", "appCrashes", "appHangs", "bsod", "unexpected", "boot"):
        data[k] = as_list(data.get(k))
    return data


def pending_reboot() -> bool:
    if not IS_WINDOWS:
        return False
    if reg_subkeys("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending"):
        return True
    if reg_subkeys("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired"):
        return True
    return bool(reg_get("HKLM", r"SYSTEM\CurrentControlSet\Control\Session Manager", "PendingFileRenameOperations"))


def analyze(snapshot: dict[str, Any] | None, hw: dict[str, Any], sec: dict[str, Any], events: dict[str, Any],
            startup_count: int, junk_bytes: int | None, hung: list[str],
            progress: Callable[[str], None] | None = None) -> list[Issue]:
    issues: list[Issue] = []
    add = issues.append

    # ---------------- нагрузка и температуры
    if snapshot:
        cpu = snapshot.get("cpu", {})
        ram = snapshot.get("ram", {})
        if cpu.get("total", 0) > 90:
            add(Issue(WARNING, "Процессор", f"Высокая загрузка CPU: {cpu['total']:.0f}%",
                      "Найдите процесс-нагрузчик на вкладке «Процессы».", "goto:processes", "К процессам"))
        t = cpu.get("temp")
        if t and t >= 90:
            add(Issue(CRITICAL, "Процессор", f"Перегрев CPU: {t:.0f} °C",
                      "Проверьте систему охлаждения, термопасту и пыль в корпусе."))
        elif t and t >= 80:
            add(Issue(WARNING, "Процессор", f"Высокая температура CPU: {t:.0f} °C", "Возможен троттлинг."))
        gpu = snapshot.get("gpu") or {}
        gt = gpu.get("temp")
        if gt and gt >= 87:
            add(Issue(CRITICAL, "Видеокарта", f"Перегрев GPU: {gt:.0f} °C", "Почистите радиатор, проверьте вентиляторы."))
        if ram.get("percent", 0) >= 90:
            add(Issue(CRITICAL, "Память", f"ОЗУ почти заполнена: {ram['percent']:.0f}%",
                      "Закройте лишние программы или освободите память.", "free_ram", "Освободить"))
        elif ram.get("percent", 0) >= 80:
            add(Issue(WARNING, "Память", f"Высокая загрузка ОЗУ: {ram['percent']:.0f}%", "", "free_ram", "Освободить"))
        sw = snapshot.get("swap", {})
        if sw.get("total", 0) == 0 and IS_WINDOWS:
            add(Issue(WARNING, "Память", "Файл подкачки отключён",
                      "При нехватке ОЗУ программы будут аварийно закрываться."))
        up = snapshot.get("uptime", 0)
        if up > 7 * 86400:
            add(Issue(INFO, "Система", f"Компьютер не перезагружался {int(up // 86400)} дн.",
                      "Перезагрузка освобождает ресурсы и завершает установку обновлений."))
        if snapshot.get("temp_source") is None and IS_WINDOWS:
            add(Issue(INFO, "Датчики", "Температура CPU недоступна",
                      "Windows не отдаёт её штатно. Запустите программу от имени администратора или "
                      "LibreHardwareMonitor — SysPulse подхватит его датчики автоматически."))

    # ---------------- диски
    for p in hw.get("partitions", []):
        free_pct = 100 - p["percent"]
        if free_pct < 5:
            add(Issue(CRITICAL, "Диски", f"Диск {p['mount']} заполнен: свободно {free_pct:.0f}%",
                      "Windows и программы могут работать со сбоями.", "goto:cleaner", "Очистить"))
        elif free_pct < 12:
            add(Issue(WARNING, "Диски", f"Мало места на {p['mount']}: свободно {free_pct:.0f}%", "",
                      "goto:cleaner", "Очистить"))
    for d in hw.get("disks", []):
        if not isinstance(d, dict):
            continue
        health = (d.get("health") or "").lower()
        if health and health not in ("healthy", "0"):
            add(Issue(CRITICAL, "Диски", f"Диск «{d.get('name')}»: состояние {d.get('health')}",
                      "Срочно сделайте резервную копию данных!"))
        if (d.get("wear") or 0) >= 80:
            add(Issue(WARNING, "Диски", f"SSD «{d.get('name')}» изношен на {d['wear']}%", "Планируйте замену."))
        if (d.get("temp") or 0) >= 65:
            add(Issue(WARNING, "Диски", f"Горячий накопитель «{d.get('name')}»: {d['temp']} °C"))
        if (d.get("rerr") or 0) > 0:
            add(Issue(WARNING, "Диски", f"«{d.get('name')}»: неисправимых ошибок чтения — {d['rerr']}"))
    if hw.get("smartfail"):
        add(Issue(CRITICAL, "Диски", "S.M.A.R.T. предсказывает отказ диска", ", ".join(map(str, hw["smartfail"]))))
    if events.get("diskErrors"):
        add(Issue(WARNING, "Диски", f"Ошибки дисковой подсистемы за неделю: {events['diskErrors']}",
                  "Проверьте диск (chkdsk) и кабели.", "chkdsk", "Проверить диск"))

    # ---------------- устройства
    for dev in hw.get("badDevices", []):
        if not isinstance(dev, dict):
            continue
        code = int(dev.get("code") or 0)
        if code == 22:
            continue  # отключено пользователем
        add(Issue(WARNING if code != 43 else CRITICAL, "Устройства", f"{dev.get('name') or 'Неизвестное устройство'}",
                  DEVICE_ERRORS.get(code, f"Код ошибки {code}") + ". Обновите или переустановите драйвер.",
                  "devmgmt", "Диспетчер устройств"))
    wear = battery_wear(hw)
    if wear is not None and wear >= 40:
        add(Issue(WARNING, "Аккумулятор", f"Износ аккумулятора: {wear:.0f}%", "Ёмкость заметно снижена."))
    if events.get("whea"):
        add(Issue(CRITICAL, "Оборудование", f"Аппаратные ошибки WHEA за месяц: {events['whea']}",
                  "Возможны проблемы с CPU/ОЗУ/PCIe или нестабильный разгон."))

    # ---------------- стабильность
    if events.get("bsod"):
        add(Issue(CRITICAL, "Стабильность", f"Синие экраны (BSOD) за 30 дней: {len(events['bsod'])}",
                  "Последний: " + str(events["bsod"][0]) + ". Проверьте драйверы, ОЗУ (mdsched) и системные файлы.",
                  "sfc", "Проверить файлы"))
    if len(events.get("unexpected", [])) >= 2:
        add(Issue(WARNING, "Стабильность", f"Неожиданные выключения за 30 дней: {len(events['unexpected'])}",
                  "Возможны проблемы с питанием или перегрев."))
    for c in events.get("appCrashes", [])[:5]:
        if isinstance(c, dict) and int(c.get("n") or 0) >= 3:
            add(Issue(WARNING, "Программы", f"«{c.get('app')}» аварийно завершалась {c.get('n')} раз за неделю",
                      "Обновите или переустановите программу.", "goto:programs", "К программам"))
    for h in events.get("appHangs", [])[:5]:
        if isinstance(h, dict) and int(h.get("n") or 0) >= 3:
            add(Issue(INFO, "Программы", f"«{h.get('app')}» зависала {h.get('n')} раз за неделю"))
    for name in hung:
        add(Issue(WARNING, "Программы", f"Сейчас не отвечает: {name}", "", "goto:processes", "К процессам"))
    sys_err = sum(int(e.get("n") or 0) for e in events.get("sysErrors", []) if isinstance(e, dict))
    if sys_err >= 20:
        top = ", ".join(str(e.get("src")) for e in events["sysErrors"][:3] if isinstance(e, dict))
        add(Issue(WARNING, "Журнал событий", f"Системных ошибок за сутки: {sys_err}", f"Чаще всего: {top}"))

    # ---------------- загрузка
    boots = [b for b in events.get("boot", []) if isinstance(b, dict) and b.get("ms")]
    if boots:
        sec_boot = boots[0]["ms"] / 1000
        if sec_boot > 90:
            add(Issue(WARNING, "Загрузка", f"Медленная загрузка Windows: {sec_boot:.0f} с",
                      "Отключите лишнее в автозагрузке.", "goto:startup", "Автозагрузка"))
    if startup_count > 15:
        add(Issue(WARNING, "Загрузка", f"Много программ в автозагрузке: {startup_count}",
                  "Каждая замедляет запуск и съедает память.", "goto:startup", "Настроить"))
    elif startup_count > 8:
        add(Issue(INFO, "Загрузка", f"Программ в автозагрузке: {startup_count}", "", "goto:startup", "Настроить"))
    if pending_reboot():
        add(Issue(INFO, "Система", "Ожидается перезагрузка для завершения обновлений"))

    # ---------------- мусор
    if junk_bytes is not None:
        gb = junk_bytes / 1024 ** 3
        if gb >= 5:
            add(Issue(WARNING, "Мусор", f"Ненужные файлы: {gb:.1f} ГБ", "", "goto:cleaner", "Очистить"))
        elif gb >= 1:
            add(Issue(INFO, "Мусор", f"Ненужные файлы: {gb:.1f} ГБ", "", "goto:cleaner", "Очистить"))

    # ---------------- безопасность
    defender = sec.get("defender") or {}
    av_list = sec.get("av") or []
    any_av_on = any(a.get("enabled") for a in av_list) or bool(defender.get("rtp"))
    if IS_WINDOWS and (defender or av_list) and not any_av_on:
        add(Issue(CRITICAL, "Безопасность", "Антивирусная защита отключена",
                  "Включите защиту в реальном времени.", "goto:security", "Безопасность"))
    if defender.get("av") and (defender.get("sigAge") or 0) > 7:
        add(Issue(WARNING, "Безопасность", f"Антивирусные базы устарели ({defender['sigAge']} дн.)", "",
                  "defender_update", "Обновить"))
    for t in sec.get("threats", []):
        add(Issue(CRITICAL, "Безопасность", f"Угроза: {t.get('name')}", str(t.get("resources") or ""),
                  "goto:security", "Подробнее"))
    for fw in sec.get("firewall", []):
        if not fw.get("enabled"):
            add(Issue(WARNING, "Безопасность", f"Брандмауэр отключён (профиль {fw.get('name')})", "",
                      "firewall_on", "Включить"))
    if IS_WINDOWS and not sec.get("uac", True):
        add(Issue(WARNING, "Безопасность", "Контроль учётных записей (UAC) отключён"))
    sus_hosts = [h for h in sec.get("hosts", []) if h.get("suspicious")]
    if sus_hosts:
        add(Issue(CRITICAL, "Безопасность", "Подозрительные записи в файле hosts",
                  ", ".join(f"{h['host']} → {h['ip']}" for h in sus_hosts[:5]), "goto:security", "Подробнее"))
    if (sec.get("lastUpdateDays") or 0) > 60:
        add(Issue(WARNING, "Обновления", f"Обновления Windows не ставились {sec['lastUpdateDays']} дн."))

    # ---------------- подозрительные процессы
    try:
        from .processes import suspicion
        for p in psutil.process_iter(["name", "exe"]):
            warn = suspicion(p.info.get("name") or "", p.info.get("exe") or "")
            if warn:
                add(Issue(WARNING, "Безопасность", f"Подозрительный процесс: {p.info['name']}",
                          f"{warn}\n{p.info['exe']}", "goto:processes", "К процессам"))
    except Exception:
        log.exception("process heuristics")

    if not issues:
        add(Issue(OK, "Система", "Проблем не обнаружено", "Компьютер в хорошем состоянии."))
    issues.sort(key=lambda i: SEVERITY_ORDER[i.severity])
    return issues


def health_score(issues: list[Issue]) -> int:
    score = 100
    for i in issues:
        score -= {CRITICAL: 15, WARNING: 6, INFO: 1, OK: 0}[i.severity]
    return max(0, min(100, score))
