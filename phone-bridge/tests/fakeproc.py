"""Build a fake /proc tree for deterministic operation tests."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


class FakeProc:
    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="fakeproc-")
        self.root = Path(self._tmp.name)
        self.write("stat", "cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 100 0 100 800 0 0 0 0 0 0\nbtime 1700000000\n")
        self.write(
            "meminfo",
            "MemTotal:       16000000 kB\nMemFree:         2000000 kB\n"
            "MemAvailable:   10000000 kB\nSwapTotal:       2000000 kB\nSwapFree:        1500000 kB\n",
        )
        self.write("uptime", "3600.50 7200.00\n")
        self.write("loadavg", "0.50 0.40 0.30 1/200 999\n")

    def write(self, rel: str, content: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def add_process(
        self,
        pid: int,
        name: str,
        *,
        state: str = "S",
        ppid: int = 1,
        utime: int = 100,
        stime: int = 50,
        nice: int = 0,
        threads: int = 1,
        start_ticks: int = 1000,
        vsize: int = 10_000_000,
        rss_pages: int = 1000,
    ) -> None:
        fields = [
            state, ppid, pid, pid, 0, -1, 4194304, 0, 0, 0, 0,
            utime, stime, 0, 0, 20, nice, threads, 0, start_ticks, vsize, rss_pages,
        ]
        self.write(f"{pid}/stat", f"{pid} ({name}) " + " ".join(str(f) for f in fields) + "\n")
        # These files must never be read by the operations.
        self.write(f"{pid}/cmdline", "secret-cli\0--password=hunter2\0")
        self.write(f"{pid}/environ", "AWS_SECRET_ACCESS_KEY=leak\0")

    def remove_process(self, pid: int) -> None:
        base = self.root / str(pid)
        for child in base.iterdir():
            child.unlink()
        base.rmdir()

    def hide_process(self, pid: int) -> None:
        os.chmod(self.root / str(pid) / "stat", 0)

    def cleanup(self) -> None:
        for dirpath, _dirs, files in os.walk(self.root):
            for f in files:
                try:
                    os.chmod(os.path.join(dirpath, f), 0o600)
                except OSError:
                    pass
        self._tmp.cleanup()
