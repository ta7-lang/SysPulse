"""Безопасность: состояние антивируса и брандмауэра, проверка Microsoft Defender
(офлайн-базы), подписи файлов, файл hosts, UAC, подозрительные процессы."""
from __future__ import annotations

import logging
import os
import re
from typing import Any

from .winutil import IS_WINDOWS, as_list, ps_json, reg_get, run

log = logging.getLogger(__name__)

MPCMDRUN = os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Windows Defender", "MpCmdRun.exe")

_STATUS_SCRIPT = r"""
$o = @{}
$mp = Get-MpComputerStatus
if ($mp) { $o.defender = @{ service = $mp.AMServiceEnabled; av = $mp.AntivirusEnabled; rtp = $mp.RealTimeProtectionEnabled;
  sigAge = $mp.AntivirusSignatureAge; sigDate = if ($mp.AntivirusSignatureLastUpdated) { $mp.AntivirusSignatureLastUpdated.ToString('yyyy-MM-dd HH:mm') } else { '' };
  quickAge = $mp.QuickScanAge; fullAge = $mp.FullScanAge; tamper = $mp.IsTamperProtected; version = $mp.AMProductVersion } }
$o.av = @(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | ForEach-Object { @{ name = $_.displayName; state = [int]$_.productState } })
$o.firewall = @(Get-NetFirewallProfile | ForEach-Object { @{ name = [string]$_.Name; enabled = [bool]$_.Enabled } })
$o.threats = @(Get-MpThreat | ForEach-Object { @{ name = $_.ThreatName; severity = [int]$_.SeverityID; active = $_.IsActive;
  resources = ($_.Resources -join '; ') } })
$hf = Get-HotFix | Where-Object { $_.InstalledOn } | Sort-Object InstalledOn -Descending | Select-Object -First 1
if ($hf) { $o.lastUpdate = $hf.InstalledOn.ToString('yyyy-MM-dd'); $o.lastUpdateDays = [int]((Get-Date) - $hf.InstalledOn).TotalDays }
$o
"""

SEVERITY = {0: "Неизвестно", 1: "Низкая", 2: "Средняя", 4: "Высокая", 5: "Критическая"}


def decode_av_state(state: int) -> tuple[bool, bool]:
    """productState из SecurityCenter2 → (включён, базы актуальны)."""
    enabled = ((state >> 8) & 0xFF) in (0x10, 0x11)
    up_to_date = (state & 0xFF) == 0x00
    return enabled, up_to_date


def security_status() -> dict[str, Any]:
    data: dict[str, Any] = {}
    if IS_WINDOWS:
        res = ps_json(_STATUS_SCRIPT, timeout=90)
        if isinstance(res, dict):
            data = res
    data["av"] = [dict(a, enabled=decode_av_state(int(a.get("state") or 0))[0],
                       updated=decode_av_state(int(a.get("state") or 0))[1])
                  for a in as_list(data.get("av")) if isinstance(a, dict)]
    data["firewall"] = [f for f in as_list(data.get("firewall")) if isinstance(f, dict)]
    data["threats"] = [t for t in as_list(data.get("threats")) if isinstance(t, dict)]
    data["uac"] = reg_get("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System", "EnableLUA", 1) != 0
    data["hosts"] = hosts_entries()
    return data


# ----------------------------------------------------------------- hosts
SENSITIVE_DOMAINS = ("microsoft", "windowsupdate", "google", "yandex", "vk.com", "mail.ru", "kaspersky", "eset",
                     "drweb", "avast", "bitdefender", "malwarebytes", "sberbank", "tinkoff", "gosuslugi", "paypal")


def hosts_entries() -> list[dict[str, Any]]:
    path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), r"System32\drivers\etc\hosts")
    entries: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                ip, hosts = parts[0], parts[1:]
                for h in hosts:
                    if h.lower() in ("localhost", "localhost.localdomain"):
                        continue
                    suspicious = any(d in h.lower() for d in SENSITIVE_DOMAINS) and ip not in ("0.0.0.0", "127.0.0.1", "::1")
                    entries.append({"ip": ip, "host": h, "suspicious": suspicious})
    except OSError:
        pass
    return entries


# ----------------------------------------------------------------- Defender
def defender_available() -> bool:
    return IS_WINDOWS and os.path.exists(MPCMDRUN)


def defender_scan(kind: str = "quick", path: str = "") -> tuple[bool, str]:
    """kind: quick | full | custom. Проверка работает без интернета по локальным базам."""
    if not defender_available():
        return False, "Microsoft Defender не найден (возможно, установлен другой антивирус)."
    args = [MPCMDRUN, "-Scan", "-ScanType", {"quick": "1", "full": "2", "custom": "3"}[kind]]
    if kind == "custom":
        args += ["-File", path]
    code, out = run(args, timeout=6 * 3600)
    # 0 — угроз нет, 2 — найдены угрозы
    found = re.findall(r"(?im)^\s*Threat\s*:\s*(.+)$", out)
    if code == 2 or found:
        return True, "Обнаружены угрозы:\n" + ("\n".join(found) or out[-1500:])
    if code == 0:
        return True, "Проверка завершена. Угроз не обнаружено."
    return False, f"Проверка завершилась с кодом {code}.\n{out[-1500:]}"


def defender_update() -> tuple[bool, str]:
    if not defender_available():
        return False, "Microsoft Defender не найден."
    code, out = run([MPCMDRUN, "-SignatureUpdate"], timeout=1800)
    return code == 0, out[-1000:] or ("Базы обновлены." if code == 0 else f"Код {code}")


def defender_remove_threats() -> tuple[bool, str]:
    code, out = run(["powershell.exe", "-NoProfile", "-Command", "Remove-MpThreat"], timeout=600)
    return code == 0, out or ("Угрозы удалены." if code == 0 else f"Код {code}")


# ----------------------------------------------------------------- подписи
def check_signatures(paths: list[str]) -> dict[str, tuple[str, str]]:
    """Проверяет цифровые подписи пачкой. path -> (статус, издатель)."""
    paths = [p for p in dict.fromkeys(paths) if p and os.path.isfile(p)]
    if not paths or not IS_WINDOWS:
        return {}
    result: dict[str, tuple[str, str]] = {}
    for i in range(0, len(paths), 60):
        chunk = paths[i:i + 60]
        arr = ",".join("'" + p.replace("'", "''") + "'" for p in chunk)
        data = ps_json(
            f"@({arr}) | ForEach-Object {{ $s = Get-AuthenticodeSignature -LiteralPath $_; "
            "@{ p = $_; s = [string]$s.Status; c = if ($s.SignerCertificate) { $s.SignerCertificate.GetNameInfo(0, $false) } else { '' } } }",
            timeout=120)
        for d in as_list(data):
            if isinstance(d, dict) and d.get("p"):
                raw = str(d.get("s") or "")
                status = {"Valid": "Подписан", "NotSigned": "Без подписи", "HashMismatch": "Подпись повреждена",
                          "NotTrusted": "Недоверенная подпись", "UnknownError": "Ошибка проверки"}.get(raw, raw)
                result[str(d["p"])] = (status, str(d.get("c") or ""))
    return result
