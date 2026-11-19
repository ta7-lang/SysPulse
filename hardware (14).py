"""Статическая информация о компонентах ПК и их состоянии (WMI/CIM через PowerShell)."""
from __future__ import annotations

import platform
from typing import Any

import psutil

from .winutil import IS_WINDOWS, as_list, ps_json

_HW_SCRIPT = r"""
$o = [ordered]@{}
$os = Get-CimInstance Win32_OperatingSystem
$o.os = @{ caption = $os.Caption; version = $os.Version; build = $os.BuildNumber; arch = $os.OSArchitecture;
           install = if ($os.InstallDate) { $os.InstallDate.ToString('yyyy-MM-dd') } else { '' };
           boot = if ($os.LastBootUpTime) { $os.LastBootUpTime.ToString('yyyy-MM-dd HH:mm') } else { '' };
           user = $env:USERNAME; host = $env:COMPUTERNAME }
$o.cpu = @(Get-CimInstance Win32_Processor | ForEach-Object { @{ name = $_.Name.Trim(); cores = $_.NumberOfCores;
           threads = $_.NumberOfLogicalProcessors; mhz = $_.MaxClockSpeed; l2 = $_.L2CacheSize; l3 = $_.L3CacheSize;
           socket = $_.SocketDesignation; virt = $_.VirtualizationFirmwareEnabled } })
$bb = Get-CimInstance Win32_BaseBoard
$o.board = @{ vendor = $bb.Manufacturer; product = $bb.Product }
$bios = Get-CimInstance Win32_BIOS
$o.bios = @{ version = $bios.SMBIOSBIOSVersion; vendor = $bios.Manufacturer;
             date = if ($bios.ReleaseDate) { $bios.ReleaseDate.ToString('yyyy-MM-dd') } else { '' } }
$o.ram = @(Get-CimInstance Win32_PhysicalMemory | ForEach-Object { @{ slot = $_.DeviceLocator; size = [double]$_.Capacity;
           speed = $_.ConfiguredClockSpeed; maxspeed = $_.Speed; vendor = $_.Manufacturer; part = ([string]$_.PartNumber).Trim() } })
$o.gpu = @(Get-CimInstance Win32_VideoController | ForEach-Object { @{ name = $_.Name; driver = $_.DriverVersion;
           driverdate = if ($_.DriverDate) { $_.DriverDate.ToString('yyyy-MM-dd') } else { '' };
           res = "$($_.CurrentHorizontalResolution)x$($_.CurrentVerticalResolution) @ $($_.CurrentRefreshRate) Гц";
           status = $_.Status; err = $_.ConfigManagerErrorCode } })
$o.disks = @(Get-PhysicalDisk | ForEach-Object {
    $rc = $_ | Get-StorageReliabilityCounter
    @{ name = $_.FriendlyName; media = [string]$_.MediaType; bus = [string]$_.BusType; size = [double]$_.Size;
       health = [string]$_.HealthStatus; op = ($_.OperationalStatus -join ', ');
       temp = $rc.Temperature; tempmax = $rc.TemperatureMax; wear = $rc.Wear; hours = $rc.PowerOnHours;
       rerr = $rc.ReadErrorsUncorrected; werr = $rc.WriteErrorsUncorrected } })
$fp = Get-CimInstance -Namespace root/wmi -ClassName MSStorageDriver_FailurePredictStatus
$o.smartfail = @($fp | Where-Object { $_.PredictFailure } | ForEach-Object { $_.InstanceName })
$o.net = @(Get-NetAdapter | Where-Object { $_.Status -eq 'Up' } | ForEach-Object { @{ name = $_.Name;
           desc = $_.InterfaceDescription; speed = $_.LinkSpeed; mac = $_.MacAddress } })
$o.badDevices = @(Get-CimInstance Win32_PnPEntity | Where-Object { $_.ConfigManagerErrorCode -ne 0 } |
           ForEach-Object { @{ name = $_.Name; code = $_.ConfigManagerErrorCode; cls = $_.PNPClass } })
$bs = Get-CimInstance -Namespace root/wmi -ClassName BatteryStaticData
$bf = Get-CimInstance -Namespace root/wmi -ClassName BatteryFullChargedCapacity
if ($bs -and $bf) { $o.battery = @{ design = [double]($bs | Select-Object -First 1).DesignedCapacity;
                                    full = [double]($bf | Select-Object -First 1).FullChargedCapacity } }
$o
"""

# коды ошибок диспетчера устройств
DEVICE_ERRORS = {
    1: "Устройство настроено неправильно",
    3: "Драйвер повреждён или не хватает ресурсов",
    10: "Устройство не может запуститься",
    12: "Недостаточно свободных ресурсов",
    14: "Требуется перезагрузка",
    18: "Нужно переустановить драйвер",
    19: "Повреждена конфигурация в реестре",
    21: "Устройство удаляется",
    22: "Устройство отключено",
    24: "Устройство отсутствует или работает неправильно",
    28: "Драйвер не установлен",
    31: "Устройство работает неправильно (драйвер не загружен)",
    32: "Служба драйвера отключена",
    37: "Ошибка инициализации драйвера",
    39: "Драйвер повреждён или отсутствует",
    43: "Устройство сообщило о сбое (код 43)",
    45: "Устройство не подключено",
    52: "Не удалось проверить цифровую подпись драйвера",
}


def collect_hardware() -> dict[str, Any]:
    """Возвращает словарь с информацией о системе. Работает 2–10 секунд."""
    info: dict[str, Any] = {}
    if IS_WINDOWS:
        data = ps_json(_HW_SCRIPT, timeout=90, depth=5)
        if isinstance(data, dict):
            info = data
    # то, что psutil знает на любой платформе
    info.setdefault("os", {"caption": platform.platform(), "version": platform.version(),
                           "arch": platform.machine(), "host": platform.node()})
    if not info.get("cpu"):
        info["cpu"] = [{"name": platform.processor() or "CPU", "cores": psutil.cpu_count(logical=False),
                        "threads": psutil.cpu_count(), "mhz": (psutil.cpu_freq().max if psutil.cpu_freq() else None)}]
    for key in ("cpu", "ram", "gpu", "disks", "net", "badDevices", "smartfail"):
        info[key] = as_list(info.get(key))
    info["partitions"] = []
    for p in psutil.disk_partitions(all=False):
        if "cdrom" in p.opts or not p.fstype:
            continue
        try:
            u = psutil.disk_usage(p.mountpoint)
        except OSError:
            continue
        info["partitions"].append({"mount": p.mountpoint, "fs": p.fstype, "total": u.total,
                                   "used": u.used, "free": u.free, "percent": u.percent})
    return info


def battery_wear(info: dict[str, Any]) -> float | None:
    b = info.get("battery")
    if not isinstance(b, dict):
        return None
    design, full = b.get("design") or 0, b.get("full") or 0
    if design <= 0 or full <= 0:
        return None
    return max(0.0, 100.0 * (1 - full / design))
