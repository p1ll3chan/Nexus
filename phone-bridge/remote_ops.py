"""Read-only Linux diagnostics for Nexus Remote.

All operations read ``/proc`` and ``statvfs`` directly.  None of them spawn a
process, read command lines, environments, open files, or working
directories, or return usernames / other users' UIDs.
"""

from __future__ import annotations

import json
import os
import platform
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from remote_registry import OperationContext, OperationError

CPU_SAMPLE_SECONDS = 0.25
MAX_NAME_LEN = 64

_STATE_NAMES = {
    "R": "running",
    "S": "sleeping",
    "D": "disk_sleep",
    "Z": "zombie",
    "T": "stopped",
    "t": "tracing_stop",
    "I": "idle",
    "X": "dead",
    "P": "parked",
    "W": "waking",
}


def _clk_tck() -> int:
    try:
        return int(os.sysconf("SC_CLK_TCK")) or 100
    except (ValueError, OSError, AttributeError):
        return 100


def _page_size() -> int:
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) or 4096
    except (ValueError, OSError, AttributeError):
        return 4096


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _sanitize_name(raw: str) -> str:
    cleaned = "".join(ch if ch.isprintable() else "?" for ch in raw)
    return cleaned[:MAX_NAME_LEN]


# --------------------------------------------------------------------------- system


def _cpu_times(root: Path) -> tuple[int, int] | None:
    try:
        for line in _read(root / "stat").splitlines():
            if line.startswith("cpu "):
                values = [int(v) for v in line.split()[1:]]
                idle = values[3] + (values[4] if len(values) > 4 else 0)
                return idle, sum(values[:8])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _boot_time(root: Path) -> float | None:
    try:
        for line in _read(root / "stat").splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _meminfo(root: Path) -> dict[str, int]:
    info: dict[str, int] = {}
    try:
        for line in _read(root / "meminfo").splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                info[key.strip()] = int(parts[0]) * 1024  # kB -> bytes
    except OSError:
        pass
    return info


def _cpu_percent(root: Path, interval: float) -> float | None:
    first = _cpu_times(root)
    if first is None:
        return None
    time.sleep(interval)
    second = _cpu_times(root)
    if second is None:
        return None
    idle = second[0] - first[0]
    total = second[1] - first[1]
    if total <= 0:
        return None
    return round(max(0.0, min(100.0, 100.0 * (1.0 - idle / total))), 1)


def system_status(params: dict[str, Any], ctx: OperationContext) -> dict[str, Any]:
    root = Path(ctx.proc_root)
    unavailable: list[str] = []

    cpu_pct = _cpu_percent(root, CPU_SAMPLE_SECONDS)
    if cpu_pct is None:
        unavailable.append("cpu.percent")
    try:
        load = [round(x, 2) for x in os.getloadavg()] if ctx.proc_root == "/proc" else None
    except OSError:
        load = None
    if load is None:
        try:
            load = [float(x) for x in _read(root / "loadavg").split()[:3]]
        except (OSError, ValueError):
            load = None
            unavailable.append("cpu.load")

    mem = _meminfo(root)
    memory: dict[str, Any] | None = None
    if "MemTotal" in mem:
        total = mem["MemTotal"]
        available = mem.get("MemAvailable", mem.get("MemFree", 0))
        used = max(0, total - available)
        memory = {
            "total_bytes": total,
            "used_bytes": used,
            "available_bytes": available,
            "percent": round(100.0 * used / total, 1) if total else None,
            "swap_total_bytes": mem.get("SwapTotal"),
            "swap_used_bytes": (
                mem["SwapTotal"] - mem.get("SwapFree", 0) if "SwapTotal" in mem else None
            ),
        }
    else:
        unavailable.append("memory")

    disks: list[dict[str, Any]] = []
    for label, raw_path in ctx.disk_paths:
        try:
            st = os.statvfs(os.path.expanduser(raw_path))
        except OSError:
            unavailable.append(f"disk.{label}")
            continue
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = max(0, total - st.f_bfree * st.f_frsize)
        disks.append(
            {
                "label": label,
                "total_bytes": total,
                "used_bytes": used,
                "free_bytes": free,
                "percent": round(100.0 * used / total, 1) if total else None,
            }
        )

    uptime: float | None = None
    try:
        uptime = float(_read(root / "uptime").split()[0])
    except (OSError, ValueError, IndexError):
        unavailable.append("uptime")

    return {
        "host": ctx.device_name,
        "os": {"system": platform.system(), "kernel": platform.release()},
        "cpu": {"percent": cpu_pct, "count": os.cpu_count(), "load": load},
        "memory": memory,
        "disks": disks,
        "uptime_seconds": uptime,
        "collected_at": time.time(),
        "unavailable": unavailable,
    }


# --------------------------------------------------------------------------- processes


@dataclass
class ProcInfo:
    pid: int
    ppid: int
    name: str
    state: str
    own: bool
    threads: int
    nice: int
    rss_bytes: int
    vms_bytes: int
    cpu_time: float
    start_ticks: int
    start_time: float | None
    runtime: float | None

    def summary(self, mem_total: int) -> dict[str, Any]:
        avg_cpu = (
            round(100.0 * self.cpu_time / self.runtime, 1)
            if self.runtime and self.runtime > 0
            else None
        )
        return {
            "pid": self.pid,
            "name": self.name,
            "state": self.state,
            "own": self.own,
            "rss_bytes": self.rss_bytes,
            "mem_percent": round(100.0 * self.rss_bytes / mem_total, 2) if mem_total else None,
            "cpu_time_seconds": round(self.cpu_time, 2),
            "avg_cpu_percent": avg_cpu,
            "threads": self.threads,
            "start_time": self.start_time,
            "start_ticks": self.start_ticks,
            "runtime_seconds": round(self.runtime, 1) if self.runtime is not None else None,
        }

    def detail(self, mem_total: int) -> dict[str, Any]:
        data = self.summary(mem_total)
        data.update({"ppid": self.ppid, "nice": self.nice, "vms_bytes": self.vms_bytes})
        return data


def _parse_stat(raw: str) -> tuple[str, list[str]]:
    open_idx = raw.find("(")
    close_idx = raw.rfind(")")
    if open_idx < 0 or close_idx < open_idx:
        raise ValueError("malformed stat")
    return raw[open_idx + 1 : close_idx], raw[close_idx + 2 :].split()


def _read_proc(root: Path, pid: int, *, now: float, boot: float | None) -> ProcInfo:
    """Raise FileNotFoundError/ProcessLookupError if gone, PermissionError if hidden."""

    base = root / str(pid)
    raw = _read(base / "stat")
    comm, fields = _parse_stat(raw)
    if len(fields) < 22:
        raise ValueError("short stat")
    tck = _clk_tck()
    start_ticks = int(fields[19])
    start_time = boot + start_ticks / tck if boot is not None else None
    runtime = max(0.0, now - start_time) if start_time is not None else None
    try:
        own = os.stat(base).st_uid == os.getuid()
    except FileNotFoundError:
        raise
    except OSError:
        own = False
    return ProcInfo(
        pid=pid,
        ppid=int(fields[1]),
        name=_sanitize_name(comm),
        state=_STATE_NAMES.get(fields[0], "unknown"),
        own=own,
        threads=int(fields[17]),
        nice=int(fields[16]),
        rss_bytes=max(0, int(fields[21])) * _page_size(),
        vms_bytes=int(fields[20]),
        cpu_time=(int(fields[11]) + int(fields[12])) / tck,
        start_ticks=start_ticks,
        start_time=round(start_time, 2) if start_time is not None else None,
        runtime=runtime,
    )


_SORT_KEYS = {
    "memory": lambda p: (p.rss_bytes, p.pid),
    "cpu_time": lambda p: (p.cpu_time, p.pid),
    "pid": lambda p: (p.pid,),
    "name": lambda p: (p.name.lower(), p.pid),
}


def _fit(items: list[dict[str, Any]], envelope: dict[str, Any], cap: int) -> bool:
    """Drop trailing items until the encoded payload fits ``cap``.  Returns truncated."""

    truncated = False
    budget = int(cap * 0.95)
    while items and len(json.dumps({**envelope, "items": items}, separators=(",", ":"))) > budget:
        items.pop()
        truncated = True
    return truncated


def process_list(params: dict[str, Any], ctx: OperationContext) -> dict[str, Any]:
    root = Path(ctx.proc_root)
    now = time.time()
    boot = _boot_time(root)
    mem_total = _meminfo(root).get("MemTotal", 0)
    procs: list[ProcInfo] = []
    inaccessible = 0
    try:
        entries = os.listdir(root)
    except OSError as exc:
        raise OperationError("unavailable", "process table is not readable") from exc
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            info = _read_proc(root, int(entry), now=now, boot=boot)
        except (FileNotFoundError, ProcessLookupError):
            continue  # exited while scanning
        except PermissionError:
            inaccessible += 1
            continue
        except (OSError, ValueError, IndexError):
            inaccessible += 1
            continue
        if params.get("own_only") and not info.own:
            continue
        procs.append(info)

    procs.sort(key=_SORT_KEYS[params["sort_by"]], reverse=params["order"] == "desc")
    offset, limit = params["offset"], params["limit"]
    page = procs[offset : offset + limit]
    items = [p.summary(mem_total) for p in page]
    envelope = {
        "total": len(procs),
        "offset": offset,
        "limit": limit,
        "sort_by": params["sort_by"],
        "order": params["order"],
        "inaccessible": inaccessible,
        "collected_at": now,
        "truncated": False,
        "returned": len(items),
    }
    envelope["truncated"] = _fit(items, envelope, ctx.max_output_bytes)
    envelope["returned"] = len(items)
    envelope["items"] = items
    return envelope


def process_inspect(params: dict[str, Any], ctx: OperationContext) -> dict[str, Any]:
    root = Path(ctx.proc_root)
    pid = params["pid"]
    now = time.time()
    boot = _boot_time(root)
    try:
        info = _read_proc(root, pid, now=now, boot=boot)
    except (FileNotFoundError, ProcessLookupError) as exc:
        raise OperationError("not_found", f"process {pid} does not exist") from exc
    except PermissionError as exc:
        raise OperationError("permission_denied", f"process {pid} is not accessible") from exc
    except (OSError, ValueError, IndexError) as exc:
        raise OperationError("unavailable", f"process {pid} could not be read") from exc

    expected = params.get("start_ticks")
    if expected is not None and expected != info.start_ticks:
        raise OperationError("process_changed", f"PID {pid} now belongs to a different process")

    # Re-read to detect PID reuse during inspection.
    try:
        again = _read_proc(root, pid, now=now, boot=boot)
    except (FileNotFoundError, ProcessLookupError) as exc:
        raise OperationError("not_found", f"process {pid} exited during inspection") from exc
    except (OSError, ValueError, IndexError) as exc:
        raise OperationError("unavailable", f"process {pid} could not be read") from exc
    if again.start_ticks != info.start_ticks:
        raise OperationError("process_changed", f"PID {pid} was reused during inspection")

    detail = info.detail(_meminfo(root).get("MemTotal", 0))
    detail["collected_at"] = now
    return detail
