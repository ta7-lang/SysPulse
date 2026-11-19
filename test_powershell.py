"""Проверка всех генерируемых скриптов PowerShell синтаксическим анализатором самого PowerShell.

Запускается, если в системе есть powershell.exe (Windows) или pwsh (PowerShell 7 на любой ОС).
"""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from syspulse.core import cleaner, diagnostics, hardware, optimizer, security, sensors, startup, winutil

PS = shutil.which("powershell") or shutil.which("pwsh")


def _collect(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    scripts: list[str] = []

    def fake_run(cmd, timeout=120):
        if isinstance(cmd, list) and "-EncodedCommand" in cmd:
            scripts.append(base64.b64decode(cmd[-1]).decode("utf-16-le"))
        return 0, ""

    monkeypatch.setattr(winutil, "run", fake_run)
    for mod in (winutil, hardware, security, diagnostics, startup, cleaner, optimizer):
        monkeypatch.setattr(mod, "IS_WINDOWS", True, raising=False)
    monkeypatch.setattr(security, "reg_get", lambda *a, **k: 1)
    exe = tmp_path / "a'b.exe"
    exe.write_text("x")

    hardware.collect_hardware()
    security.security_status()
    security.check_signatures([str(exe)])
    diagnostics.event_summary()
    startup.list_tasks()
    startup._resolve_shortcuts([str(tmp_path / "it's.lnk")])
    item = startup.StartupItem(name="T'ask", command="x", location="", kind="task", key="\\My\\")
    monkeypatch.setattr(startup, "backups_dir", lambda: tmp_path)
    startup.set_enabled(item, False)
    startup.delete_item(item)
    bk = tmp_path / "startup_t.json"
    bk.write_text(json.dumps({"kind": "task", "name": "T", "key": "\\"}), encoding="utf-8")
    bk.with_suffix(".xml").write_text("<x/>", encoding="utf-8")
    startup.restore_backup(bk)
    delivery = next(c for c in cleaner.build_categories() if c.special == "delivery")
    cleaner.scan_category(delivery)
    cleaner.clean_category(delivery)
    optimizer.optimize_drives()
    optimizer.create_restore_point("it's")
    scripts.append(winutil._PS_PREFIX + sensors._TEMP_SCRIPT)
    return scripts


def test_scripts_are_collected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    scripts = _collect(monkeypatch, tmp_path)
    assert len(scripts) >= 15
    assert all("ConvertTo-Json" in s or "Optimize-Volume" in s or "Checkpoint" in s or "ScheduledTask" in s
               or "DeliveryOptimization" in s or "while ($true)" in s for s in scripts)


@pytest.mark.skipif(PS is None, reason="PowerShell не найден")
def test_scripts_parse_without_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    scripts = _collect(monkeypatch, tmp_path)
    folder = tmp_path / "ps"
    folder.mkdir()
    for i, s in enumerate(scripts):
        (folder / f"s{i:02d}.ps1").write_text(s, encoding="utf-8-sig")
    checker = folder / "check.ps1"
    checker.write_text(
        "$bad = @(); Get-ChildItem -LiteralPath '" + str(folder).replace("'", "''") + "' -Filter 's*.ps1' | "
        "ForEach-Object { $t = $null; $e = $null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$t, [ref]$e); "
        "if ($e.Count) { $bad += \"$($_.Name): $($e[0].Message) @ $($e[0].Extent.StartLineNumber)\" } }; "
        "if ($bad.Count) { $bad; exit 1 } else { 'OK' }", encoding="utf-8-sig")
    assert PS is not None
    proc = subprocess.run([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(checker)],
                          capture_output=True, text=True, timeout=120, check=False)
    assert proc.returncode == 0 and "OK" in proc.stdout, proc.stdout + proc.stderr
