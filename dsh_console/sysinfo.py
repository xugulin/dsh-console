"""本机信息：CPU / 内存 / 磁盘 / 网络 / 系统。

全部读 ``/proc`` 与标准库，**不引入 psutil**——本机网络慢，少一个依赖就少一次
几十 MB 的下载（PySide6 那 76 MB 已经够受了）。

并发提示：纯读取，耗时在毫秒级，但仍由调用方放进后台线程池执行，
避免个别文件系统卡顿影响 GUI。
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


import ctypes




# --------------------------------------------------------------------- Windows
#
# Windows 没有 /proc，上面那套（cpuinfo / meminfo / mounts / net/dev）一条都读不到，
# 「本机信息」页在 Windows 上于是整页失败。这里用 **ctypes 直接调 Win32 API** 补一套：
# 不引 psutil（便携包里没这个依赖，也不该为这个加）、不启子进程（`wmic` 在新系统上
# 已经被移除，`powershell` 每次要几百毫秒——这页的目标是 50 ms 内出结果）。
#
# 一句话对应关系：
#   GetSystemTimes          → CPU 使用率（两次采样求差）
#   GlobalMemoryStatusEx    → 内存
#   GetLogicalDrives + disk_usage → 磁盘
#   GetIfTable              → 网络收发字节

_is_windows = os.name == "nt"

#: 上一次 CPU 采样（GetSystemTimes 给的是累计值，使用率必须两次求差）。
_cpu_prev: tuple[int, int, int] | None = None


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _filetime_value(ft: "_FILETIME") -> int:
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


def _win_cpu_usage() -> float:
    """CPU 使用率。第一次调用没有参照点，返回 0（下一次就准了）。"""
    global _cpu_prev
    try:
        idle, kernel, user = _FILETIME(), _FILETIME(), _FILETIME()
        if not ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        ):
            return 0.0
    except Exception:  # noqa: BLE001
        return 0.0
    cur = (_filetime_value(idle), _filetime_value(kernel), _filetime_value(user))
    prev, _cpu_prev = _cpu_prev, cur
    if prev is None:
        # 第一次没有参照点。**短暂再采一次**而不是返回 0——
        # 返回 0 的话用户第一次打开这页看到的是"CPU 0%"，像是页坏了（实测踩过）。
        # 代价是一次 120 ms 的等待，只发生在本进程的第一次采集。
        time.sleep(0.12)
        return _win_cpu_usage()
    # kernel 时间**包含** idle，所以总时间 = kernel + user，空闲 = idle
    idle_d = cur[0] - prev[0]
    total_d = (cur[1] - prev[1]) + (cur[2] - prev[2])
    if total_d <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * (1.0 - idle_d / total_d)))


def _win_cpu_info() -> "CpuInfo":
    info = CpuInfo()
    info.model = (platform.processor() or "").strip() or "未知处理器"
    info.cores_logical = os.cpu_count() or 0
    # 物理核数要问 API；拿不到就先等于逻辑核数（页面上标的是"核"，不是"线程"）
    info.cores_physical = _win_physical_cores() or info.cores_logical
    try:
        import winreg  # type: ignore[import-not-found]

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        ) as key:
            mhz = winreg.QueryValueEx(key, "~MHz")[0]
            info.mhz = float(mhz)
            name = winreg.QueryValueEx(key, "ProcessorNameString")[0]
            if name:
                info.model = str(name).strip()
    except Exception:  # noqa: BLE001
        pass
    info.usage_percent = _win_cpu_usage()
    return info


def _win_physical_cores() -> int:
    try:
        size = ctypes.c_ulong(0)
        # 先问需要多大缓冲，再取数据。关系是 None 时只算长度。
        ctypes.windll.kernel32.GetLogicalProcessorInformationEx(0, None, ctypes.byref(size))
        if size.value == 0:
            return 0
        buf = ctypes.create_string_buffer(size.value)
        if not ctypes.windll.kernel32.GetLogicalProcessorInformationEx(
            0, buf, ctypes.byref(size)
        ):
            return 0
        # 每个 RELATION_PROCESSOR_CORE 记录以 CORE 的数量算物理核
        count = 0
        offset = 0
        raw = buf.raw
        while offset + 8 <= len(raw):
            relationship = int.from_bytes(raw[offset:offset + 4], "little")
            entry_size = int.from_bytes(raw[offset + 4:offset + 8], "little")
            if entry_size <= 0:
                break
            if relationship == 0:          # RelationProcessorCore
                count += 1
            offset += entry_size
        return count
    except Exception:  # noqa: BLE001
        return 0


def _win_mem_info() -> "MemInfo":
    info = MemInfo()
    try:
        status = _MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return info
    except Exception:  # noqa: BLE001
        return info
    info.total_kb = status.ullTotalPhys / 1024
    info.available_kb = status.ullAvailPhys / 1024
    info.used_kb = info.total_kb - info.available_kb
    # 页面文件当 swap 看：口径和 Linux 的 swap 不完全一样，但意义对得上
    page_total = status.ullTotalPageFile / 1024
    page_free = status.ullAvailPageFile / 1024
    info.swap_total_kb = page_total
    info.swap_used_kb = max(0.0, page_total - page_free)
    return info


def _win_disks() -> list["DiskInfo"]:
    out: list[DiskInfo] = []
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:  # noqa: BLE001
        return out
    for i in range(26):
        if not (mask >> i) & 1:
            continue
        root = f"{chr(ord('A') + i)}:\\"
        try:
            usage = shutil.disk_usage(root)
        except OSError:
            continue                      # 空光驱之类，跳过而不是报错
        out.append(DiskInfo(mount=root, total=usage.total, used=usage.used,
                            free=usage.free, fstype=""))
    return out


def _win_net() -> list["NetIface"]:
    """网络接口收发量。

    用 ``GetIfTable``：结构是 ``MIB_IFROW`` 数组，按索引逐条读。字段都是固定宽度，
    拼出正确的结构体比走 WMI 快两个数量级。
    """
    out: list[NetIface] = []

    class _MIB_IFROW(ctypes.Structure):
        _fields_ = [
            ("wszName", ctypes.c_wchar * 256),
            ("dwIndex", ctypes.c_ulong),
            ("dwType", ctypes.c_ulong),
            ("dwMtu", ctypes.c_ulong),
            ("dwSpeed", ctypes.c_ulong),
            ("dwPhysAddrLen", ctypes.c_ulong),
            ("bPhysAddr", ctypes.c_ubyte * 8),
            ("dwAdminStatus", ctypes.c_ulong),
            ("dwOperStatus", ctypes.c_ulong),
            ("dwLastChange", ctypes.c_ulong),
            ("dwInOctets", ctypes.c_ulong),
            ("dwInUcastPkts", ctypes.c_ulong),
            ("dwInNUcastPkts", ctypes.c_ulong),
            ("dwInDiscards", ctypes.c_ulong),
            ("dwInErrors", ctypes.c_ulong),
            ("dwInUnknownProtos", ctypes.c_ulong),
            ("dwOutOctets", ctypes.c_ulong),
            ("dwOutUcastPkts", ctypes.c_ulong),
            ("dwOutNUcastPkts", ctypes.c_ulong),
            ("dwOutDiscards", ctypes.c_ulong),
            ("dwOutErrors", ctypes.c_ulong),
            ("dwOutQLen", ctypes.c_ulong),
            ("dwDescrLen", ctypes.c_ulong),
            ("bDescr", ctypes.c_ubyte * 256),
        ]

    try:
        size = ctypes.c_ulong(0)
        ctypes.windll.iphlpapi.GetIfTable(None, ctypes.byref(size), False)
        if size.value == 0:
            return out
        buf = ctypes.create_string_buffer(size.value)
        if ctypes.windll.iphlpapi.GetIfTable(buf, ctypes.byref(size), False) != 0:
            return out
        count = int.from_bytes(buf.raw[0:4], "little")
        row_size = ctypes.sizeof(_MIB_IFROW)
        base = 4
        for i in range(count):
            if base + (i + 1) * row_size > len(buf.raw):
                break
            row = _MIB_IFROW.from_buffer_copy(buf.raw[base + i * row_size:
                                                     base + (i + 1) * row_size])
            name = (row.wszName or "").strip()
            descr = bytes(row.bDescr[:row.dwDescrLen]).decode("gbk", "replace").strip()
            mac = ":".join(f"{b:02x}" for b in row.bPhysAddr[:row.dwPhysAddrLen])
            # 1 = other / 24 = software loopback：这两个不显示，噪音
            if row.dwType in (1, 24) or not name:
                continue
            out.append(NetIface(
                name=(descr or name)[:48], mac=mac,
                state="up" if row.dwOperStatus == 1 else "down",
                rx_bytes=int(row.dwInOctets), tx_bytes=int(row.dwOutOctets),
            ))
    except Exception:  # noqa: BLE001
        return out
    return out


def _win_uptime() -> float:
    """开机时长（秒）。用 GetTickCount64，最省事也最准。"""
    try:
        return ctypes.windll.kernel32.GetTickCount64() / 1000.0
    except Exception:  # noqa: BLE001
        return 0.0


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _kb_to_text(kb: float) -> str:
    for unit, div in (("TB", 1024**3), ("GB", 1024**2), ("MB", 1024)):
        if kb >= div:
            return f"{kb / div:.1f} {unit}"
    return f"{kb:.0f} KB"


def _bytes_text(n: float) -> str:
    for unit, div in (("TB", 1024**4), ("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n:.0f} B"


@dataclass(slots=True)
class CpuInfo:
    model: str = ""
    cores_physical: int = 0
    cores_logical: int = 0
    mhz: float = 0.0
    load1: float = 0.0
    load5: float = 0.0
    load15: float = 0.0
    usage_percent: float = 0.0

    @property
    def load_text(self) -> str:
        return f"{self.load1:.2f} / {self.load5:.2f} / {self.load15:.2f}"

    @property
    def cores_text(self) -> str:
        if self.cores_physical:
            return f"{self.cores_physical} 物理 / {self.cores_logical} 逻辑"
        return f"{self.cores_logical} 逻辑"


@dataclass(slots=True)
class MemInfo:
    total_kb: float = 0.0
    available_kb: float = 0.0
    used_kb: float = 0.0
    swap_total_kb: float = 0.0
    swap_used_kb: float = 0.0

    @property
    def used_percent(self) -> float:
        return (self.used_kb / self.total_kb * 100) if self.total_kb else 0.0

    @property
    def total_text(self) -> str:
        return _kb_to_text(self.total_kb)

    @property
    def used_text(self) -> str:
        return f"{_kb_to_text(self.used_kb)}（{self.used_percent:.0f}%）"

    @property
    def available_text(self) -> str:
        return _kb_to_text(self.available_kb)

    @property
    def swap_text(self) -> str:
        if not self.swap_total_kb:
            return "无 swap"
        return f"{_kb_to_text(self.swap_used_kb)} / {_kb_to_text(self.swap_total_kb)}"


@dataclass(slots=True)
class DiskInfo:
    mount: str
    total: int = 0
    used: int = 0
    free: int = 0
    fstype: str = ""

    @property
    def used_percent(self) -> float:
        return (self.used / self.total * 100) if self.total else 0.0

    @property
    def total_text(self) -> str:
        return _bytes_text(self.total)

    @property
    def used_text(self) -> str:
        return f"{_bytes_text(self.used)}（{self.used_percent:.0f}%）"

    @property
    def free_text(self) -> str:
        return _bytes_text(self.free)


@dataclass(slots=True)
class NetIface:
    name: str
    ipv4: str = ""
    mac: str = ""
    state: str = ""
    rx_bytes: int = 0
    tx_bytes: int = 0

    @property
    def rx_text(self) -> str:
        return _bytes_text(self.rx_bytes)

    @property
    def tx_text(self) -> str:
        return _bytes_text(self.tx_bytes)


@dataclass(slots=True)
class SysInfo:
    hostname: str = ""
    os_name: str = ""
    kernel: str = ""
    arch: str = ""
    python: str = ""
    uptime_seconds: int = 0
    boot_time: float = 0.0
    cpu: CpuInfo = field(default_factory=CpuInfo)
    mem: MemInfo = field(default_factory=MemInfo)
    disks: list[DiskInfo] = field(default_factory=list)
    net: list[NetIface] = field(default_factory=list)
    dsh_home: str = ""
    dsh_home_size: str = ""
    cache_size: str = ""
    desktop: str = ""

    @property
    def uptime_text(self) -> str:
        s = self.uptime_seconds
        d, rem = divmod(s, 86400)
        h, rem = divmod(rem, 3600)
        m, _ = divmod(rem, 60)
        parts = []
        if d:
            parts.append(f"{d} 天")
        if h or d:
            parts.append(f"{h} 小时")
        parts.append(f"{m} 分")
        return "".join(parts)


def _dir_size(path: Path, limit: int = 400_000) -> int:
    """粗略统计目录体积（有上限，避免在超大目录上卡住）。"""
    total = 0
    count = 0
    try:
        for root, dirs, files in os.walk(path):
            for f in files:
                try:
                    total += (Path(root) / f).stat().st_size
                except OSError:
                    continue
                count += 1
                if count > limit:
                    return total
    except OSError:
        pass
    return total


def cpu_info() -> CpuInfo:
    if _is_windows:
        return _win_cpu_info()
    info = CpuInfo()
    text = _read("/proc/cpuinfo")
    models: dict[str, int] = {}
    logical = 0                      # processor 条目数 = 逻辑核心数
    phys_ids: set[tuple[str, str]] = set()
    phys_id = core_id = ""
    for line in text.splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "processor":
            logical += 1
        elif key == "model name":
            models[value] = models.get(value, 0) + 1
        elif key == "cpu MHz":
            try:
                info.mhz = max(info.mhz, float(value))
            except ValueError:
                pass
        elif key == "physical id":
            phys_id = value
        elif key == "core id":
            core_id = value
            phys_ids.add((phys_id, core_id))
    if models:
        info.model = max(models.items(), key=lambda kv: kv[1])[0]
    # 注意：早期版本写成 len(models)，而所有核心的型号名相同 → 恒为 1，
    # 于是"占用率"被算成 load1/1 直接顶到 100%。
    info.cores_logical = logical or (os.cpu_count() or 0)
    info.cores_physical = len(phys_ids) if phys_ids else 0
    try:
        info.load1, info.load5, info.load15 = os.getloadavg()
    except OSError:
        pass
    if info.cores_logical:
        info.usage_percent = min(100.0, info.load1 / info.cores_logical * 100)
    return info


def mem_info() -> MemInfo:
    if _is_windows:
        return _win_mem_info()
    info = MemInfo()
    values: dict[str, float] = {}
    for line in _read("/proc/meminfo").splitlines():
        key, _, rest = line.partition(":")
        num = rest.strip().split()[0] if rest.strip() else "0"
        try:
            values[key.strip()] = float(num)
        except ValueError:
            continue
    info.total_kb = values.get("MemTotal", 0.0)
    info.available_kb = values.get("MemAvailable", values.get("MemFree", 0.0))
    info.used_kb = max(0.0, info.total_kb - info.available_kb)
    info.swap_total_kb = values.get("SwapTotal", 0.0)
    info.swap_used_kb = max(0.0, info.swap_total_kb - values.get("SwapFree", 0.0))
    return info


def disk_info() -> list[DiskInfo]:
    if _is_windows:
        return _win_disks()
    out: list[DiskInfo] = []
    seen: set[str] = set()
    for line in _read("/proc/mounts").splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        dev, mount, fstype = parts[0], parts[1].replace("\\040", " "), parts[2]
        if fstype in {
            "proc", "sysfs", "devtmpfs", "tmpfs", "cgroup", "cgroup2", "overlay",
            "devpts", "securityfs", "debugfs", "tracefs", "fusectl", "configfs",
            "ramfs", "autofs", "mqueue", "hugetlbfs", "binfmt_misc", "pstore",
            "efivarfs", "squashfs", "nsfs", "bpf",
        }:
            continue
        if not dev.startswith("/dev/") or mount in seen:
            continue
        try:
            usage = shutil.disk_usage(mount)
        except OSError:
            continue
        seen.add(mount)
        out.append(
            DiskInfo(
                mount=mount, total=usage.total, used=usage.used,
                free=usage.free, fstype=fstype,
            )
        )
    out.sort(key=lambda d: -d.total)
    return out


def net_info() -> list[NetIface]:
    if _is_windows:
        return _win_net()
    """活跃网卡（排除 lo 与无流量的虚拟口）。"""
    stats: dict[str, tuple[int, int]] = {}
    for line in _read("/proc/net/dev").splitlines()[2:]:
        name, _, rest = line.partition(":")
        fields = rest.split()
        if len(fields) >= 9:
            try:
                stats[name.strip()] = (int(fields[0]), int(fields[8]))
            except ValueError:
                pass

    out: list[NetIface] = []
    for name, (rx, tx) in stats.items():
        if name == "lo":
            continue
        iface = NetIface(name=name, rx_bytes=rx, tx_bytes=tx, state="up")
        try:
            addrs = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
            # 尽力取一个非回环地址即可（getaddrinfo 不区分网卡，够用）
            for a in addrs:
                ip = a[4][0]
                if not ip.startswith("127."):
                    iface.ipv4 = ip
                    break
        except OSError:
            pass
        mac_path = Path(f"/sys/class/net/{name}/address")
        if mac_path.exists():
            try:
                iface.mac = mac_path.read_text(encoding="utf-8").strip()
            except OSError:
                pass
        oper = Path(f"/sys/class/net/{name}/operstate")
        if oper.exists():
            try:
                iface.state = oper.read_text(encoding="utf-8").strip()
            except OSError:
                pass
        if iface.state != "up" and rx == 0 and tx == 0:
            continue
        out.append(iface)
    out.sort(key=lambda i: -(i.rx_bytes + i.tx_bytes))
    return out


def collect() -> SysInfo:
    """采集一份完整快照（目标：50 ms 内返回）。"""
    info = SysInfo()
    info.hostname = socket.gethostname()
    info.kernel = platform.release()
    info.arch = platform.machine()
    info.python = platform.python_version()
    info.desktop = os.environ.get("XDG_CURRENT_DESKTOP", "") or os.environ.get(
        "DESKTOP_SESSION", ""
    ) or (os.environ.get("SESSIONNAME", "") if _is_windows else "")
    try:
        import distro  # type: ignore # 不一定有

        info.os_name = f"{distro.name()} {distro.version()}"
    except Exception:
        info.os_name = _os_name()

    if _is_windows:
        up = _win_uptime()
        info.uptime_seconds = int(up)
        info.boot_time = time.time() - up
    uptime_text = [] if _is_windows else _read("/proc/uptime").split()
    if uptime_text:
        try:
            up = float(uptime_text[0])
            info.uptime_seconds = int(up)
            info.boot_time = time.time() - up
        except ValueError:
            pass

    info.cpu = cpu_info()
    info.mem = mem_info()
    info.disks = disk_info()
    info.net = net_info()

    dsh_home = Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
    info.dsh_home = str(dsh_home)
    if dsh_home.exists():
        info.dsh_home_size = _bytes_text(_dir_size(dsh_home))
    cache = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "dsh-console"
    if cache.exists():
        info.cache_size = _bytes_text(_dir_size(cache))
    return info


def _os_name() -> str:
    if _is_windows:
        try:
            return f"{platform.system()} {platform.release()} ({platform.version()})"
        except Exception:  # noqa: BLE001
            return platform.system()
    for line in _read("/etc/os-release").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return platform.system()


def dsh_service_memory() -> str:
    """harness 服务占用的内存（与仪表盘口径一致）。"""
    if _is_windows:
        return _win_service_memory()
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "show", "dsh-web", "-p", "MemoryCurrent", "--value"],
            capture_output=True, text=True, timeout=8, check=False,
        )
        value = proc.stdout.strip()
        if value.isdigit():
            return _bytes_text(int(value))
    except Exception:
        pass
    return "—"


def _win_service_memory() -> str:
    """Windows 上取 harness 进程占用的内存。

    走 ``tasklist``：便携包里没有 psutil，而这一页本来就不该为了一行数字引依赖。
    pid 从控制台的服务状态里来（便携模式是 `run/web.pid`）。
    """
    pid = 0
    try:
        from . import service

        pid = service.get_status().effective_pid or 0
    except Exception:  # noqa: BLE001
        pid = 0
    if not pid:
        return "—"
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except Exception:  # noqa: BLE001
        return "—"
    # 形如 "python.exe","1234","Console","1","123,456 K"
    for line in (proc.stdout or "").splitlines():
        parts = [p.strip().strip('"') for p in line.split(",")]
        if len(parts) < 5:
            continue
        digits = "".join(ch for ch in parts[4] if ch.isdigit())
        if digits:
            return _bytes_text(int(digits) * 1024)
    return "—"
