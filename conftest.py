from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def fake_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Подменяет winreg на реализацию в памяти и включает «режим Windows» в модулях."""
    from tests import fake_winreg as fw

    fw.reset()
    from syspulse.core import optimizer, programs, registry, startup, winutil

    monkeypatch.setitem(sys.modules, "winreg", fw)
    monkeypatch.setattr(winutil, "winreg", fw, raising=False)
    monkeypatch.setattr(winutil, "HIVES", {"HKLM": fw.HKEY_LOCAL_MACHINE, "HKCU": fw.HKEY_CURRENT_USER,
                                           "HKCR": fw.HKEY_CLASSES_ROOT, "HKU": fw.HKEY_USERS})
    for mod in (winutil, startup, programs, registry, optimizer):
        monkeypatch.setattr(mod, "IS_WINDOWS", True, raising=False)
    monkeypatch.setattr(winutil, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(startup, "backups_dir", lambda: tmp_path)
    monkeypatch.setattr(registry, "backups_dir", lambda: tmp_path)
    return fw
