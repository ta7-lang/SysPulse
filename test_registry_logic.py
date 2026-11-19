"""Тесты кода, работающего с реестром, на имитации winreg."""
from __future__ import annotations

import json
from pathlib import Path

from syspulse.core import optimizer, programs, registry, startup, winutil

RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"


def test_reg_helpers(fake_registry) -> None:
    winutil.reg_set("HKCU", r"Software\Test\Sub", "Name", "value")
    winutil.reg_set("HKCU", r"Software\Test\Sub", "Num", 5)
    assert winutil.reg_get("HKCU", r"Software\Test\Sub", "Name") == "value"
    assert winutil.reg_get("HKCU", r"Software\Test\Sub", "Missing", "d") == "d"
    assert {n for n, _v, _t in winutil.reg_values("HKCU", r"Software\Test\Sub")} == {"Name", "Num"}
    winutil.reg_set("HKCU", r"Software\Test\Sub\Deep\Deeper", "x", 1)
    assert winutil.reg_subkeys("HKCU", r"Software\Test") == ["Sub"]
    assert winutil.reg_delete_tree("HKCU", r"Software\Test\Sub")
    assert winutil.reg_subkeys("HKCU", r"Software\Test") == []
    assert winutil.reg_delete_value("HKCU", r"Software\Test", "nope")


def test_startup_disable_enable_delete_restore(fake_registry, tmp_path: Path, monkeypatch) -> None:
    exe = tmp_path / "app.exe"
    exe.write_text("x")
    winutil.reg_set("HKCU", RUN, "MyApp", f'"{exe}" --tray')
    winutil.reg_set("HKCU", RUN, "Ghost", r'"C:\Gone\ghost.exe"')
    monkeypatch.setenv("APPDATA", str(tmp_path / "nope"))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "nope2"))
    items = {i.name: i for i in startup.list_items(include_tasks=False)}
    assert items["MyApp"].enabled and items["MyApp"].exists
    assert items["MyApp"].path == str(exe)
    assert not items["Ghost"].exists

    assert startup.set_enabled(items["MyApp"], False) == ""
    raw = winutil.reg_get("HKCU", startup.APPROVED + r"\Run", "MyApp")
    assert isinstance(raw, bytes) and raw[0] == 3
    assert not {i.name: i for i in startup.list_items(False)}["MyApp"].enabled
    assert startup.set_enabled(items["MyApp"], True) == ""
    assert {i.name: i for i in startup.list_items(False)}["MyApp"].enabled

    assert startup.delete_item(items["MyApp"]) == ""
    assert "MyApp" not in {i.name for i in startup.list_items(False)}
    backup = next(tmp_path.glob("startup_*.json"))
    assert json.loads(backup.read_text(encoding="utf-8"))["reg_value"].endswith("--tray")
    assert startup.restore_backup(backup) == ""
    assert "MyApp" in {i.name for i in startup.list_items(False)}


def test_startup_folder_items(fake_registry, tmp_path: Path, monkeypatch) -> None:
    folder = tmp_path / "appdata" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    folder.mkdir(parents=True)
    (folder / "tool.bat").write_text("echo")
    (folder / "desktop.ini").write_text("")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(startup, "_startup_folders", lambda: [
        ("HKCU", str(folder), "Папка автозагрузки (пользователь)", startup.APPROVED + r"\StartupFolder")])
    items = [i for i in startup.list_items(False) if i.kind == "folder"]
    assert [i.name for i in items] == ["tool.bat"]
    assert startup.set_enabled(items[0], False) == ""
    assert not [i for i in startup.list_items(False) if i.kind == "folder"][0].enabled
    assert startup.delete_item(items[0]) == ""
    assert not (folder / "tool.bat").exists()
    backup = next(tmp_path.glob("startup_*.json"))
    assert startup.restore_backup(backup) == ""
    assert (folder / "tool.bat").exists()


def test_programs_listing(fake_registry, tmp_path: Path) -> None:
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    winutil.reg_set("HKLM", base + r"\App1", "DisplayName", "Alpha")
    winutil.reg_set("HKLM", base + r"\App1", "EstimatedSize", 2048)
    winutil.reg_set("HKLM", base + r"\App1", "InstallDate", "20250102")
    winutil.reg_set("HKLM", base + r"\App1", "InstallLocation", str(tmp_path))
    winutil.reg_set("HKLM", base + r"\Upd", "DisplayName", "Update KB1")
    winutil.reg_set("HKLM", base + r"\Upd", "ParentKeyName", "Office")
    winutil.reg_set("HKLM", base + r"\Sys", "DisplayName", "Hidden")
    winutil.reg_set("HKLM", base + r"\Sys", "SystemComponent", 1)
    winutil.reg_set("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Uninstall\B", "DisplayName", "Beta")
    winutil.reg_set("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Uninstall\B", "InstallLocation", "/no/such")
    rows = programs.list_programs()
    assert [r["name"] for r in rows] == ["Alpha", "Beta"]
    assert rows[0]["size"] == 2048 * 1024 and rows[0]["date"] == "02.01.2025" and not rows[0]["broken"]
    assert rows[1]["broken"] and rows[1]["scope"] == "Пользователь"


def test_registry_scan_and_fix(fake_registry, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(registry, "_local_drives", lambda: {"C"})
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion"
    winutil.reg_set("HKLM", base + r"\App Paths\gone.exe", "", r"C:\Gone\gone.exe")
    winutil.reg_set("HKLM", base + r"\SharedDLLs", r"C:\Gone\lib.dll", 1)
    winutil.reg_set("HKLM", base + r"\SharedDLLs", r"Z:\Removable\lib.dll", 1)  # диск не локальный — не трогаем
    winutil.reg_set("HKLM", base + r"\Uninstall\Old", "DisplayName", "Old App")
    winutil.reg_set("HKLM", base + r"\Uninstall\Old", "UninstallString", r'"C:\Old\unins000.exe" /S')
    winutil.reg_set("HKLM", base + r"\Uninstall\Msi", "DisplayName", "MSI App")
    winutil.reg_set("HKLM", base + r"\Uninstall\Msi", "UninstallString", "MsiExec.exe /X{1234}")
    winutil.reg_set("HKCU", r"Software\Classes\Local Settings\Software\Microsoft\Windows\Shell\MuiCache",
                    r"C:\Gone\x.exe.FriendlyAppName", "X")
    issues = registry.scan()
    found = {(i.category, i.value) for i in issues}
    assert ("Пути приложений (App Paths)", None) in found
    assert ("Общие библиотеки (SharedDLLs)", r"C:\Gone\lib.dll") in found
    assert not any(i.value == r"Z:\Removable\lib.dll" for i in issues)
    assert any("Old App" in i.description for i in issues)
    assert not any("MSI App" in i.description for i in issues)
    assert any(i.category.startswith("Кэш MUI") for i in issues)

    monkeypatch.setattr(registry, "reg_export", lambda hive, key, target: target.write_text("REGEDIT4") or True)
    fixed, errors, folder = registry.fix(issues)
    assert fixed == len(issues) and not errors and any(Path(folder).glob("*.reg"))
    assert registry.scan() == []


def test_registry_fix_skips_without_backup(fake_registry, monkeypatch) -> None:
    monkeypatch.setattr(registry, "_local_drives", lambda: {"C"})
    winutil.reg_set("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\SharedDLLs", r"C:\Gone\a.dll", 1)
    monkeypatch.setattr(registry, "reg_export", lambda *a: False)
    fixed, errors, _ = registry.fix(registry.scan())
    assert fixed == 0 and errors
    assert registry.scan()  # запись осталась


def test_tweaks_apply_and_revert(fake_registry) -> None:
    for tweak in optimizer.TWEAKS:
        tweak.apply_hook = None  # без вызова powercfg
        assert tweak.state() is False, tweak.id
        assert tweak.apply(True) == "", tweak.id
        assert tweak.state() is True, tweak.id
        assert tweak.apply(False) == "", tweak.id
        assert tweak.state() is False, tweak.id
