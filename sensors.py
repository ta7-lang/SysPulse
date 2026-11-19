"""Сбор показателей в реальном времени: CPU, RAM, GPU, диски, сеть, температуры."""
from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
from typing import Any

import psutil

from .pdh import PdhQuery
from .winutil import CREATE_NO_WINDOW, IS_WINDOWS, powershell_cmd, reg_get

log = logging.getLogger(__name__)

try:  # NVIDIA NVML (необязательно — нужен только драйвер NVIDIA)
    import pynvml  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    pynvml = None


# ======================================================================= температуры
_TEMP_SCRIPT = r"""
while ($true) {
  $r = @{}
  $tz = Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature
  if ($tz) { $r.acpi = @($tz | ForEach-Object { [math]::Round($_.CurrentTemperature / 10 - 273.15, 1) }) }
  foreach ($ns in 'root/LibreHardwareMonitor', 'root/OpenHardwareMonitor') {
    $s = Get-CimInstance -Namespace $ns -ClassName Sensor |
         Where-Object { $_.SensorType -in 'Temperature','Fan','Power','Clock' }
    if ($s) {
      $r.src = $ns
      $r.lhm = @($s | ForEach-Object { @{ n = $_.Name; p = [string]$_.Parent; t = $_.SensorType; v = [math]::Round([double]$_.Value, 1) } })
      break
    }
  }
  [Console]::Out.WriteLine(($r | ConvertTo-Json -Compress -Depth 4))
  [Console]::Out.Flush()
  Start-Sleep -Seconds 3
}
"""


class TemperatureProvider:
    """Фоновый процесс PowerShell, который раз в 3 с отдаёт JSON с датчиками.

    Источники: ACPI Thermal Zone (часто только с правами администратора) и,
    если запущен LibreHardwareMonitor/OpenHardwareMonitor, их WMI-датчики.
    """

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self._proc: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if not IS_WINDOWS or self._thread:
            return
        self._thread = threading.Thread(target=self._loop, name="temps", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._proc = subprocess.Popen(
                    powershell_cmd(_TEMP_SCRIPT), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
                )
                assert self._proc.stdout is not None
                for raw in self._proc.stdout:
                    if self._stop.is_set():
                        break
                    line = raw.decode("utf-8", errors="replace").strip()
                    if line.startswith("{"):
                        try:
                            self.data = json.loads(line)
                        except json.JSONDecodeError:
                            pass
            except OSError:
                log.exception("Не удалось запустить провайдер температур")
                return
            finally:
                self._kill()
            self._stop.wait(10)  # перезапуск после падения

    def _kill(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.kill()
            except OSError:
                pass
        self._proc = None

    def stop(self) -> None:
        self._stop.set()
        self._kill()

    # --- разбор
    def sensors(self) -> list[dict[str, Any]]:
        lhm = self.data.get("lhm") or []
        return lhm if isinstance(lhm, list) else [lhm]

    def cpu_temp(self) -> float | None:
        best: float | None = None
        for s in self.sensors():
            if s.get("t") != "Temperature" or "cpu" not in str(s.get("p", "")).lower():
                continue
            name = str(s.get("n", "")).lower()
            val = s.get("v")
            if not isinstance(val, (int, float)) or val <= 0:
                continue
            if "package" in name or "tctl" in name or "tdie" in name:
                return float(val)
            best = max(best or 0.0, float(val))
        if best:
            return best
        acpi = self.data.get("acpi")
        values = [v for v in (acpi if isinstance(acpi, list) else [acpi]) if isinstance(v, (int, float)) and v > 0]
        return max(values) if values else None

    def gpu_temp(self) -> float | None:
        vals = [float(s["v"]) for s in self.sensors()
                if s.get("t") == "Temperature" and "gpu" in str(s.get("p", "")).lower()
                and isinstance(s.get("v"), (int, float)) and s["v"] > 0
                and "hot spot" not in str(s.get("n", "")).lower()]
        return max(vals) if vals else None

    def disk_temps(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for s in self.sensors():
            parent = str(s.get("p", "")).lower()
            if s.get("t") == "Temperature" and ("/hdd" in parent or "/nvme" in parent or "/ssd" in parent):
                out[str(s.get("p"))] = float(s.get("v") or 0)
        return out

    def cpu_power(self) -> float | None:
        for s in self.sensors():
            if s.get("t") == "Power" and "cpu" in str(s.get("p", "")).lower() and "package" in str(s.get("n", "")).lower():
                return float(s.get("v") or 0)
        return None

    def fans(self) -> list[tuple[str, float]]:
        return [(str(s.get("n")), float(s.get("v") or 0)) for s in self.sensors() if s.get("t") == "Fan"]


# ======================================================================= NVIDIA
class NvidiaGpu:
    def __init__(self) -> None:
        self.ok = False
        self.handles: list[Any] = []
        if pynvml is None:
            return
        try:
            pynvml.nvmlInit()
            self.handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(pynvml.nvmlDeviceGetCount())]
            self.ok = bool(self.handles)
        except Exception:
            self.ok = False

    def read(self) -> dict[str, Any] | None:
        if not self.ok or pynvml is None:
            return None
        h = self.handles[0]
        res: dict[str, Any] = {"vendor": "NVIDIA"}

        def safe(fn: Any, *a: Any) -> Any:
            try:
                return fn(*a)
            except Exception:
                return None

        name = safe(pynvml.nvmlDeviceGetName, h)
        res["name"] = name.decode() if isinstance(name, bytes) else name
        util = safe(pynvml.nvmlDeviceGetUtilizationRates, h)
        res["util"] = float(util.gpu) if util is not None else None
        mem = safe(pynvml.nvmlDeviceGetMemoryInfo, h)
        if mem is not None:
            res["mem_used"], res["mem_total"] = float(mem.used), float(mem.total)
        res["temp"] = safe(pynvml.nvmlDeviceGetTemperature, h, pynvml.NVML_TEMPERATURE_GPU)
        res["clock"] = safe(pynvml.nvmlDeviceGetClockInfo, h, pynvml.NVML_CLOCK_GRAPHICS)
        res["mem_clock"] = safe(pynvml.nvmlDeviceGetClockInfo, h, pynvml.NVML_CLOCK_MEM)
        power = safe(pynvml.nvmlDeviceGetPowerUsage, h)
        res["power"] = power / 1000.0 if power is not None else None
        res["fan"] = safe(pynvml.nvmlDeviceGetFanSpeed, h)
        return res

    def close(self) -> None:
        if self.ok and pynvml is not None:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass


# ======================================================================= основной сэмплер
_GPU_ENGINE_RE = re.compile(r"luid_(0x[0-9a-f]+_0x[0-9a-f]+).*engtype_(\w+)", re.I)
_GPU_LUID_RE = re.compile(r"luid_(0x[0-9a-f]+_0x[0-9a-f]+)", re.I)


def aggregate_gpu_engines(values: dict[str, float]) -> float | None:
    """Загрузка GPU как в Диспетчере задач: сумма по процессам для каждого движка,
    затем максимум по движкам и адаптерам."""
    per_engine: dict[tuple[str, str], float] = {}
    for inst, val in values.items():
        m = _GPU_ENGINE_RE.search(inst)
        if not m:
            continue
        k = (m.group(1).lower(), m.group(2))
        per_engine[k] = per_engine.get(k, 0.0) + val
    if not per_engine:
        return None
    return min(100.0, max(per_engine.values()))


def base_cpu_mhz() -> float | None:
    mhz = reg_get("HKLM", r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "~MHz")
    if isinstance(mhz, int) and mhz > 0:
        return float(mhz)
    freq = psutil.cpu_freq()
    return float(freq.max or freq.current) if freq else None


class Sampler:
    """Вызывайте sample() периодически (например, раз в секунду) из рабочего потока."""

    def __init__(self) -> None:
        self.temps = TemperatureProvider()
        self.temps.start()
        self.nvidia = NvidiaGpu()
        self.pdh = PdhQuery()
        self._pdh_built = 0.0
        self._build_pdh()
        self._base_mhz = base_cpu_mhz()
        self._last_disk = psutil.disk_io_counters()
        self._last_net = psutil.net_io_counters()
        self._last_t = time.monotonic()
        self._vram_total = self._detect_vram_total()
        psutil.cpu_percent(percpu=True)
        psutil.cpu_percent()

    def _build_pdh(self) -> None:
        if not self.pdh.available:
            return
        self.pdh.add("cpu_perf", r"\Processor Information(_Total)\% Processor Performance")
        self.pdh.add("gpu_engine", r"\GPU Engine(*)\Utilization Percentage")
        self.pdh.add("gpu_dedicated", r"\GPU Adapter Memory(*)\Dedicated Usage")
        self.pdh.add("disk_idle", r"\PhysicalDisk(_Total)\% Idle Time")
        self.pdh.add("threads", r"\System\Threads")
        self.pdh.add("handles", r"\Process(_Total)\Handle Count")
        self.pdh.collect()
        self._pdh_built = time.monotonic()

    @staticmethod
    def _detect_vram_total() -> float | None:
        if not IS_WINDOWS:
            return None
        from .winutil import reg_subkeys
        cls = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        best = None
        for sub in reg_subkeys("HKLM", cls):
            if not sub.isdigit():
                continue
            val = reg_get("HKLM", f"{cls}\\{sub}", "HardwareInformation.qwMemorySize")
            if isinstance(val, int) and val > 0:
                best = max(best or 0, val)
            elif isinstance(val, bytes) and len(val) >= 8:
                best = max(best or 0, int.from_bytes(val[:8], "little"))
        return float(best) if best else None

    def sample(self) -> dict[str, Any]:
        now = time.monotonic()
        dt = max(now - self._last_t, 1e-3)
        self._last_t = now

        # перестраиваем PDH раз в минуту — набор экземпляров GPU Engine меняется
        if self.pdh.available and now - self._pdh_built > 60:
            self.pdh.close()
            self.pdh = PdhQuery()
            self._build_pdh()
        if self.pdh.available:
            self.pdh.collect()

        snap: dict[str, Any] = {"ts": time.time()}

        # --- CPU
        per_core = psutil.cpu_percent(percpu=True)
        total = sum(per_core) / len(per_core) if per_core else psutil.cpu_percent()
        freq = None
        perf = self.pdh.value("cpu_perf")
        if perf is not None and self._base_mhz:
            freq = self._base_mhz * perf / 100.0
        else:
            f = psutil.cpu_freq()
            freq = f.current if f else None
        snap["cpu"] = {
            "total": total, "per_core": per_core, "freq": freq,
            "temp": self.temps.cpu_temp(), "power": self.temps.cpu_power(),
        }

        # --- память
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        snap["ram"] = {"used": vm.total - vm.available, "total": vm.total, "percent": vm.percent,
                       "available": vm.available}
        snap["swap"] = {"used": sw.used, "total": sw.total, "percent": sw.percent}

        # --- GPU
        gpu: dict[str, Any] | None = self.nvidia.read()
        if gpu is None and self.pdh.available:
            util = aggregate_gpu_engines(self.pdh.array("gpu_engine"))
            ded = self.pdh.array("gpu_dedicated")
            used = max(ded.values()) if ded else None
            if util is not None or used is not None:
                gpu = {"util": util, "mem_used": used, "mem_total": self._vram_total}
        if gpu is not None:
            if gpu.get("temp") is None:
                gpu["temp"] = self.temps.gpu_temp()
            gpu.setdefault("mem_total", self._vram_total)
        snap["gpu"] = gpu

        # --- диски
        disk = psutil.disk_io_counters()
        disk_info: dict[str, float | None] = {"read_bps": 0.0, "write_bps": 0.0}
        if disk and self._last_disk:
            disk_info["read_bps"] = max(disk.read_bytes - self._last_disk.read_bytes, 0) / dt
            disk_info["write_bps"] = max(disk.write_bytes - self._last_disk.write_bytes, 0) / dt
        idle = self.pdh.value("disk_idle")
        disk_info["active"] = None if idle is None else max(0.0, min(100.0, 100.0 - idle))
        snap["disk"] = disk_info
        self._last_disk = disk

        # --- сеть
        net = psutil.net_io_counters()
        if net and self._last_net:
            snap["net"] = {
                "up_bps": max(net.bytes_sent - self._last_net.bytes_sent, 0) / dt,
                "down_bps": max(net.bytes_recv - self._last_net.bytes_recv, 0) / dt,
            }
        else:
            snap["net"] = {"up_bps": 0.0, "down_bps": 0.0}
        self._last_net = net

        # --- система
        snap["procs"] = len(psutil.pids())
        th = self.pdh.value("threads")
        hd = self.pdh.value("handles")
        snap["threads"] = int(th) if th is not None else None
        snap["handles"] = int(hd) if hd is not None else None
        snap["uptime"] = time.time() - psutil.boot_time()
        bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        snap["battery"] = None if bat is None else {
            "percent": bat.percent, "plugged": bat.power_plugged,
            "secsleft": bat.secsleft if isinstance(bat.secsleft, int) and bat.secsleft > 0 else None,
        }
        snap["fans"] = self.temps.fans()
        snap["temp_source"] = self.temps.data.get("src") or ("ACPI" if self.temps.data.get("acpi") else None)
        return snap

    def close(self) -> None:
        self.temps.stop()
        self.nvidia.close()
        self.pdh.close()
