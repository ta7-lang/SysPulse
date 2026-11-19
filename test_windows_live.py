"""Интеграционные тесты на реальной Windows (выполняются в GitHub Actions на windows-latest)."""
from __future__ import annotations

import os
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="только Windows")


def test_pdh_counters() -> None:
    from syspulse.core.pdh import PdhQuery
    q = PdhQuery()
    assert q.available
    assert q.add("cpu", r"\Processor(_Total)\% Processor Time")
    assert q.add("procs", r"\Process(*)\ID Process")
    q.collect()
    time.sleep(0.5)
    q.collect()
    v = q.value("cpu")
    assert v is not None and 0 <= v <= 100
    assert len(q.array("procs")) > 5
    q.close()


def test_sampler_on_windows() -> None:
    from syspulse.core.sensors import Sampler
    s = Sampler()
    try:
        s.sample()
        time.sleep(1.1)
        snap = s.sample()
    finally:
        s.close()
    assert snap["threads"] and snap["handles"]
    assert snap["cpu"]["freq"]


def test_hardware_info() -> None:
    from syspulse.core.hardware import collect_hardware
    info = collect_hardware()
    assert info["os"].get("caption")
    assert info["cpu"] and info["cpu"][0].get("name")
    assert info["partitions"]


def test_security_status() -> None:
    from syspulse.core.security import check_signatures, security_status
    st = security_status()
    assert isinstance(st["firewall"], list)
    notepad = os.path.join(os.environ["WINDIR"], "System32", "notepad.exe")
    sig = check_signatures([notepad])
    assert notepad in sig and sig[notepad][0] in ("Подписан", "Недоверенная подпись", "Без подписи")


def test_events_and_startup() -> None:
    from syspulse.core import diagnostics, programs, services, startup
    ev = diagnostics.event_summary()
    assert isinstance(ev, dict)
    items = startup.list_items(include_tasks=True)
    assert isinstance(items, list)
    assert programs.list_programs()
    assert services.list_services()


def test_window_enumeration_and_processes() -> None:
    from syspulse.core import processes
    visible, hung = processes.window_pids()
    assert isinstance(visible, set) and isinstance(hung, set)
    rows = processes.ProcessTracker().snapshot()
    assert any(r["kind"] == "Системный" for r in rows)


def test_power_plans_and_tweak_state() -> None:
    from syspulse.core import optimizer
    assert optimizer.power_plans()
    for t in optimizer.TWEAKS:
        assert t.state() in (True, False)


def test_recycle_bin_query() -> None:
    from syspulse.core.cleaner import recycle_bin_size
    size, count = recycle_bin_size()
    assert size >= 0 and count >= 0
