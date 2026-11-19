"""Тесты платформонезависимой логики."""
from __future__ import annotations

import os
import struct
import time
from pathlib import Path

import pytest

from syspulse.core import cleaner, diagnostics, processes, programs, security, sensors, settings, startup, winutil


# ----------------------------------------------------------------- winutil
def test_human_bytes() -> None:
    assert winutil.human_bytes(512) == "512 Б"
    assert winutil.human_bytes(1536) == "1.5 КБ"
    assert winutil.human_bytes(5 * 1024 ** 3) == "5.0 ГБ"
    assert winutil.human_bytes(None) == "—"


def test_as_list_and_decode() -> None:
    assert winutil.as_list(None) == []
    assert winutil.as_list({"a": 1}) == [{"a": 1}]
    assert winutil.as_list([1, 2]) == [1, 2]
    assert winutil.decode_output("привет".encode()) == "привет"
    assert winutil.decode_output(b"") == ""


def test_human_duration() -> None:
    assert winutil.human_duration(59) == "0 мин"
    assert winutil.human_duration(3 * 3600 + 120) == "3 ч 2 мин"
    assert winutil.human_duration(2 * 86400 + 3600) == "2 д 1 ч 0 мин"


def test_powershell_cmd_is_encoded() -> None:
    import base64
    cmd = winutil.powershell_cmd("Get-Date")
    assert cmd[0] == "powershell.exe" and "-EncodedCommand" in cmd
    decoded = base64.b64decode(cmd[-1]).decode("utf-16-le")
    assert decoded.endswith("Get-Date") and "UTF8" in decoded


# ----------------------------------------------------------------- автозагрузка
@pytest.mark.parametrize(("cmd", "expected"), [
    ('"C:\\Program Files\\App\\app.exe" --tray', "C:\\Program Files\\App\\app.exe"),
    ("C:\\Program Files\\App\\app.exe --tray", "C:\\Program Files\\App\\app.exe"),
    ("C:\\Tools\\tool.exe", "C:\\Tools\\tool.exe"),
    ("rundll32.exe shell32.dll,Control_RunDLL", "rundll32.exe"),
    ('"C:\\x\\y.exe"', "C:\\x\\y.exe"),
    ("", ""),
])
def test_extract_path(cmd: str, expected: str) -> None:
    assert startup.extract_path(cmd) == expected


def test_extract_path_expands_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYAPP", "/opt/my app")
    assert startup.extract_path("%MYAPP%/run.exe -x") in ("/opt/my app/run.exe", "%MYAPP%/run.exe")


def test_startup_approved_roundtrip() -> None:
    assert startup.approved_enabled(None) is True
    assert startup.approved_enabled(b"\x02" + b"\x00" * 11) is True
    assert startup.approved_enabled(b"\x06" + b"\x00" * 11) is True
    assert startup.approved_enabled(b"\x03" + b"\x00" * 11) is False
    off = startup.approved_value(False)
    assert len(off) == 12 and off[0] == 3 and startup.approved_enabled(off) is False
    filetime = struct.unpack("<Q", off[4:])[0]
    unix = filetime / 10_000_000 - 11644473600
    assert abs(unix - time.time()) < 5
    assert startup.approved_enabled(startup.approved_value(True)) is True


# ----------------------------------------------------------------- датчики
def test_gpu_engine_aggregation() -> None:
    values = {
        "pid_100_luid_0x00000000_0x0000C2E5_phys_0_eng_0_engtype_3D": 30.0,
        "pid_200_luid_0x00000000_0x0000C2E5_phys_0_eng_0_engtype_3D": 25.0,
        "pid_100_luid_0x00000000_0x0000C2E5_phys_0_eng_5_engtype_VideoDecode": 70.0,
        "pid_300_luid_0x00000000_0x0000AAAA_phys_0_eng_0_engtype_3D": 10.0,
        "garbage": 99.0,
    }
    assert sensors.aggregate_gpu_engines(values) == 70.0
    values["pid_100_luid_0x00000000_0x0000C2E5_phys_0_eng_5_engtype_VideoDecode"] = 10.0
    assert sensors.aggregate_gpu_engines(values) == 55.0
    assert sensors.aggregate_gpu_engines({}) is None


def test_temperature_provider_parsing() -> None:
    tp = sensors.TemperatureProvider()
    tp.data = {"acpi": [45.0, 52.5]}
    assert tp.cpu_temp() == 52.5
    tp.data = {"src": "root/LibreHardwareMonitor", "lhm": [
        {"n": "Core #1", "p": "/intelcpu/0", "t": "Temperature", "v": 61},
        {"n": "CPU Package", "p": "/intelcpu/0", "t": "Temperature", "v": 66},
        {"n": "GPU Core", "p": "/gpu-nvidia/0", "t": "Temperature", "v": 55},
        {"n": "GPU Hot Spot", "p": "/gpu-nvidia/0", "t": "Temperature", "v": 80},
        {"n": "Fan #1", "p": "/lpc/nct6798d", "t": "Fan", "v": 1200},
        {"n": "CPU Package", "p": "/intelcpu/0", "t": "Power", "v": 45.5},
    ]}
    assert tp.cpu_temp() == 66
    assert tp.gpu_temp() == 55
    assert tp.fans() == [("Fan #1", 1200.0)]
    assert tp.cpu_power() == 45.5
    tp.data = {"lhm": {"n": "x", "p": "/amdcpu/0", "t": "Temperature", "v": 70}}  # одиночный объект из ConvertTo-Json
    assert tp.cpu_temp() == 70


def test_sampler_runs_anywhere() -> None:
    s = sensors.Sampler()
    try:
        snap = s.sample()
        snap2 = s.sample()
    finally:
        s.close()
    for key in ("cpu", "ram", "swap", "disk", "net", "procs", "uptime"):
        assert key in snap2
    assert 0 <= snap2["cpu"]["total"] <= 100
    assert snap2["ram"]["total"] > 0
    assert snap["disk"]["read_bps"] >= 0


# ----------------------------------------------------------------- процессы
def test_suspicion_heuristics() -> None:
    assert processes.suspicion("svchost.exe", r"c:\users\bob\appdata\roaming\svchost.exe")
    assert processes.suspicion("app.exe", r"C:\Users\bob\AppData\Local\Temp\x\app.exe")
    assert processes.suspicion("invoice.pdf.exe", r"D:\docs\invoice.pdf.exe")
    assert processes.suspicion("chrome.exe", r"C:\Program Files\Google\Chrome\Application\chrome.exe") == ""
    assert processes.suspicion("x.exe", "") == ""


def test_process_tracker_snapshot() -> None:
    tr = processes.ProcessTracker()
    tr.snapshot()
    rows = tr.snapshot()
    assert rows and {"pid", "name", "cpu", "mem", "kind"} <= set(rows[0])
    assert any(r["pid"] == os.getpid() and r["critical"] for r in rows)


def test_kill_protects_critical(monkeypatch: pytest.MonkeyPatch) -> None:
    class P:
        def name(self) -> str:
            return "lsass.exe"
    monkeypatch.setattr(processes.psutil, "Process", lambda pid: P())
    assert "критический" in processes.kill(123)
    assert "Критический" in processes.suspend(123)


# ----------------------------------------------------------------- очистка
def _make_files(root: Path) -> None:
    (root / "sub").mkdir(parents=True)
    (root / "a.tmp").write_bytes(b"x" * 100)
    (root / "sub" / "b.log").write_bytes(b"y" * 50)
    (root / "keep.txt").write_bytes(b"z" * 10)


def test_cleaner_scan_and_clean(tmp_path: Path) -> None:
    _make_files(tmp_path)
    cat = cleaner.Category("t", "G", "Test", "", [cleaner.Target(str(tmp_path), ("*.tmp", "*.log"))])
    assert cleaner.scan_category(cat) == (150, 2)
    freed, deleted, skipped = cleaner.clean_category(cat)
    assert (freed, deleted, skipped) == (150, 2, 0)
    assert (tmp_path / "keep.txt").exists()
    assert not (tmp_path / "sub").exists()  # пустая папка удалена
    assert tmp_path.exists()                # корень не трогаем


def test_cleaner_respects_min_age_and_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target"
    outside = tmp_path / "outside"
    target.mkdir()
    outside.mkdir()
    (outside / "precious.dat").write_bytes(b"1" * 1000)
    (target / "fresh.tmp").write_bytes(b"1" * 10)
    old = target / "old.tmp"
    old.write_bytes(b"1" * 20)
    past = time.time() - 5 * 3600
    os.utime(old, (past, past))
    try:
        os.symlink(outside, target / "link")
    except OSError:
        pass
    cat = cleaner.Category("t", "G", "Test", "", [cleaner.Target(str(target), min_age_hours=2)])
    assert cleaner.scan_category(cat) == (20, 1)
    cleaner.clean_category(cat)
    assert (outside / "precious.dat").exists()
    assert (target / "fresh.tmp").exists()


def test_cleaner_glob_roots(tmp_path: Path) -> None:
    for prof in ("Default", "Profile 1"):
        d = tmp_path / prof / "Cache"
        d.mkdir(parents=True)
        (d / "f").write_bytes(b"1" * 7)
    cat = cleaner.Category("t", "G", "Test", "", [cleaner.Target(str(tmp_path / "*" / "Cache"))])
    assert cleaner.scan_category(cat) == (14, 2)


def test_categories_are_well_formed() -> None:
    cats = cleaner.build_categories()
    ids = [c.id for c in cats]
    assert len(ids) == len(set(ids))
    for c in cats:
        assert c.targets or c.special, c.id


def test_find_large_files(tmp_path: Path) -> None:
    (tmp_path / "big.bin").write_bytes(b"0" * (2 * 1024 * 1024))
    (tmp_path / "small.bin").write_bytes(b"0" * 100)
    res = cleaner.find_large_files(str(tmp_path), min_mb=1)
    assert [Path(p).name for _s, p, _m in res] == ["big.bin"]
    assert cleaner.find_large_files(str(tmp_path), min_mb=1, should_stop=lambda: True) == []


# ----------------------------------------------------------------- безопасность
def test_av_state_decoding() -> None:
    assert security.decode_av_state(397568) == (True, True)    # 0x061100 — Defender включён
    assert security.decode_av_state(393472) == (False, True)   # 0x060100 — выключен
    assert security.decode_av_state(397584) == (True, False)   # 0x061110 — базы устарели


def test_hosts_parsing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    etc = tmp_path / "System32" / "drivers" / "etc"
    etc.mkdir(parents=True)
    (etc / "hosts").write_text("# comment\n127.0.0.1 localhost\n0.0.0.0 ads.example.com\n"
                               "1.2.3.4 www.sberbank.ru sberbank.ru # фишинг\n", encoding="utf-8")
    monkeypatch.setenv("WINDIR", str(tmp_path))
    if os.sep == "/":  # путь в модуле собран с обратными слэшами
        monkeypatch.setattr(security.os.path, "join", lambda *a: "/".join(x.replace("\\", "/") for x in a))
    entries = security.hosts_entries()
    hosts = {e["host"]: e["suspicious"] for e in entries}
    assert hosts == {"ads.example.com": False, "www.sberbank.ru": True, "sberbank.ru": True}


# ----------------------------------------------------------------- диагностика
def test_analyze_detects_problems(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagnostics, "IS_WINDOWS", True)
    monkeypatch.setattr(diagnostics, "pending_reboot", lambda: True)
    snap = {"cpu": {"total": 97, "temp": 95}, "ram": {"percent": 93}, "swap": {"total": 1}, "uptime": 10 * 86400,
            "gpu": {"temp": 90}, "temp_source": "ACPI"}
    hw = {"partitions": [{"mount": "C:\\", "percent": 97.0}],
          "disks": [{"name": "SSD", "health": "Warning", "wear": 85, "temp": 70, "rerr": 3}],
          "badDevices": [{"name": "USB", "code": 43}, {"name": "Off", "code": 22}], "smartfail": ["disk0"]}
    sec = {"defender": {"av": True, "rtp": False, "sigAge": 12}, "av": [], "threats": [{"name": "Trojan:Win32/X"}],
           "firewall": [{"name": "Public", "enabled": False}], "uac": False,
           "hosts": [{"host": "google.com", "ip": "6.6.6.6", "suspicious": True}], "lastUpdateDays": 90}
    events = {"bsod": ["2026-11-01 10:00"], "unexpected": ["a", "b"], "appCrashes": [{"app": "game.exe", "n": 5}],
              "appHangs": [], "sysErrors": [{"src": "disk", "n": 30}], "boot": [{"ms": 120000}], "diskErrors": 4,
              "whea": 2}
    issues = diagnostics.analyze(snap, hw, sec, events, 20, 6 * 1024 ** 3, ["hung.exe"])
    titles = " | ".join(i.title for i in issues)
    for needle in ("Перегрев CPU", "ОЗУ почти заполнена", "заполнен", "состояние Warning", "USB", "BSOD",
                   "Антивирусная защита", "Брандмауэр", "hosts", "Trojan", "game.exe", "hung.exe",
                   "Медленная загрузка", "автозагрузке", "Ненужные файлы", "WHEA", "UAC", "перезагрузка"):
        assert needle in titles, needle
    assert "Off" not in titles  # отключённое пользователем устройство — не проблема
    assert issues[0].severity == diagnostics.CRITICAL
    assert diagnostics.health_score(issues) == 0
    clean = diagnostics.analyze(None, {}, {}, {}, 2, 0, [])
    assert all(i.severity in (diagnostics.OK, diagnostics.INFO, diagnostics.WARNING) for i in clean)


# ----------------------------------------------------------------- настройки
def test_settings_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_data_dir", lambda: tmp_path)
    s = settings.Settings()
    assert s["skin"] == "Afterburner Red"
    s["skin"] = "Cyber Blue"
    s["custom_colors"] = {"accent": "#123456"}
    s["osd"]["font"] = 20
    s.save()
    s2 = settings.Settings()
    assert s2["skin"] == "Cyber Blue" and s2.palette()["accent"] == "#123456" and s2["osd"]["font"] == 20
    assert s2["osd"]["opacity"] == settings.DEFAULTS["osd"]["opacity"]  # недостающие ключи дополняются
    skin = s2.export_skin()
    s2.import_skin({"colors": {"accent": "#abcdef", "evil": "x"}})
    assert s2.palette()["accent"] == "#abcdef" and "evil" not in s2["custom_colors"]
    assert skin["colors"]["accent"] == "#123456"
    (tmp_path / "settings.json").write_text("{broken", encoding="utf-8")
    assert settings.Settings()["skin"] == "Afterburner Red"


def test_all_skins_complete() -> None:
    keys = set(settings.SKINS["Afterburner Red"])
    for name, pal in settings.SKINS.items():
        assert set(pal) == keys, name


def test_program_date_format() -> None:
    assert programs._fmt_date("20240131") == "31.01.2024"
    assert programs._fmt_date(None) == ""
