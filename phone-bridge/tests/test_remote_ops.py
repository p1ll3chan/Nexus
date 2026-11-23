"""Days 7-10: operation registry, system.status, process.list, process.inspect."""

import json
import os
import unittest
from unittest import mock

import remote_ops
from remote_registry import (
    Operation,
    OperationContext,
    OperationError,
    OperationRegistry,
    Param,
    ParamError,
    RegistryError,
    Risk,
    default_registry,
)
from tests.fakeproc import FakeProc


def noop(params, ctx):
    return {}


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = default_registry()

    def test_fixed_read_only_operations(self):
        ids = [op.id for op in self.registry.all()]
        self.assertEqual(ids, ["process.inspect", "process.list", "system.status"])
        for op in self.registry.all():
            self.assertEqual(op.permission, "remote.read")
            self.assertEqual(op.risk, Risk.R1)
            self.assertFalse(op.requires_approval)
            self.assertLessEqual(op.timeout, 5)

    def test_unknown_operation_fails_closed(self):
        for op_id in ("shell.exec", "process.kill", "system.status ", "SYSTEM.STATUS", ""):
            with self.subTest(op_id=op_id):
                with self.assertRaises(RegistryError) as ctx:
                    self.registry.get(op_id)
                self.assertEqual(ctx.exception.code, "unknown_operation")

    def test_disabled_operation_rejected(self):
        reg = OperationRegistry(
            [Operation("x.y", "remote.read", Risk.R1, "d", noop, enabled=False)]
        )
        with self.assertRaises(RegistryError) as ctx:
            reg.get("x.y")
        self.assertEqual(ctx.exception.code, "operation_disabled")

    def test_governance_rules(self):
        bad = [
            Operation("a.b", "remote.read", Risk.R4, "forbidden", noop),
            Operation("a.b", "remote.admin", Risk.R1, "unknown perm", noop),
            Operation("a.b", "remote.execute", Risk.R1, "read must use remote.read", noop),
            Operation("a.b", "remote.read", Risk.R2, "mutation via read", noop),
            Operation("a.b", "remote.read", Risk.R1, "free string", noop, params={"cmd": Param(str)}),
            Operation("a.b", "remote.read", Risk.R1, "list arg", noop, params={"argv": Param(list)}),
            Operation("a.b", "remote.read", Risk.R1, "timeout", noop, timeout=999),
        ]
        for op in bad:
            with self.subTest(op.description):
                with self.assertRaises(ValueError):
                    OperationRegistry([op])
        with self.assertRaises(ValueError):
            OperationRegistry([Operation("a.b", "remote.read", Risk.R1, "d", noop)] * 2)

    def test_param_validation(self):
        op = self.registry.get("process.list")
        self.assertEqual(
            op.validate_params({}),
            {"limit": 50, "offset": 0, "sort_by": "memory", "order": "desc", "own_only": False},
        )
        for bad in (
            {"limit": 0},
            {"limit": 201},
            {"limit": "10"},
            {"limit": True},
            {"limit": 1.5},
            {"sort_by": "cmdline"},
            {"order": "random"},
            {"own_only": "yes"},
            {"extra": 1},
            {"offset": -1},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ParamError):
                    op.validate_params(bad)
        inspect = self.registry.get("process.inspect")
        with self.assertRaises(ParamError):
            inspect.validate_params({})
        for pid in (0, -1, 4_194_305, "1", None):
            with self.subTest(pid=pid):
                with self.assertRaises(ParamError):
                    inspect.validate_params({"pid": pid} if pid is not None else {"pid": None})

    def test_describe_is_json_serialisable(self):
        json.dumps([op.describe() for op in self.registry.all()])


class OpsTestCase(unittest.TestCase):
    def setUp(self):
        self.proc = FakeProc()
        self.ctx = OperationContext(
            device_name="test-host",
            disk_paths=(("root", "/"), ("missing", "/definitely/not/here")),
            proc_root=str(self.proc.root),
            max_output_bytes=64 * 1024,
        )
        self.registry = default_registry()
        patcher = mock.patch.object(remote_ops, "CPU_SAMPLE_SECONDS", 0.0)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.proc.cleanup)

    def run_op(self, op_id, params=None, ctx=None):
        op = self.registry.get(op_id)
        return op.handler(op.validate_params(params or {}), ctx or self.ctx)


class SystemStatusTests(OpsTestCase):
    def test_status_fields(self):
        out = self.run_op("system.status")
        self.assertEqual(out["host"], "test-host")
        self.assertEqual(out["memory"]["total_bytes"], 16_000_000 * 1024)
        self.assertAlmostEqual(out["memory"]["percent"], 37.5)
        self.assertEqual(out["memory"]["swap_used_bytes"], 500_000 * 1024)
        self.assertEqual(out["uptime_seconds"], 3600.5)
        self.assertEqual(out["cpu"]["load"], [0.5, 0.4, 0.3])
        self.assertEqual([d["label"] for d in out["disks"]], ["root"])
        self.assertIn("disk.missing", out["unavailable"])
        # cpu sampled twice from an unchanged file -> undefined -> reported unavailable
        self.assertIsNone(out["cpu"]["percent"])
        self.assertIn("cpu.percent", out["unavailable"])

    def test_cpu_percent_from_delta(self):
        values = iter([(800, 1000), (850, 1100)])
        with mock.patch.object(remote_ops, "_cpu_times", lambda root: next(values)):
            out = self.run_op("system.status")
        self.assertEqual(out["cpu"]["percent"], 50.0)

    def test_missing_proc_sources_degrade_gracefully(self):
        for name in ("meminfo", "uptime", "loadavg", "stat"):
            (self.proc.root / name).unlink()
        out = self.run_op("system.status")
        self.assertIsNone(out["memory"])
        self.assertIsNone(out["uptime_seconds"])
        for key in ("memory", "uptime", "cpu.percent", "cpu.load"):
            self.assertIn(key, out["unavailable"])

    def test_no_sensitive_fields(self):
        text = json.dumps(self.run_op("system.status"))
        for needle in (os.path.expanduser("~"), "/definitely", "hunter2"):
            self.assertNotIn(needle, text)


class ProcessListTests(OpsTestCase):
    def setUp(self):
        super().setUp()
        self.proc.add_process(1, "systemd", rss_pages=500, utime=10, start_ticks=5)
        self.proc.add_process(42, "firefox", rss_pages=90000, utime=5000, threads=80)
        self.proc.add_process(77, "evil) (name", rss_pages=10, state="Z")
        self.proc.add_process(99, "bash\x1b[31m", rss_pages=2000)

    def test_lists_and_sorts(self):
        out = self.run_op("process.list", {"sort_by": "memory"})
        self.assertEqual(out["total"], 4)
        self.assertEqual([i["pid"] for i in out["items"]], [42, 99, 1, 77])
        asc = self.run_op("process.list", {"sort_by": "pid", "order": "asc"})
        self.assertEqual([i["pid"] for i in asc["items"]], [1, 42, 77, 99])
        names = self.run_op("process.list", {"sort_by": "name", "order": "asc"})
        self.assertEqual(names["items"][0]["name"], "bash?[31m")

    def test_parses_tricky_comm_and_states(self):
        out = self.run_op("process.list", {"sort_by": "pid", "order": "asc"})
        evil = next(i for i in out["items"] if i["pid"] == 77)
        self.assertEqual(evil["name"], "evil) (name")
        self.assertEqual(evil["state"], "zombie")
        firefox = next(i for i in out["items"] if i["pid"] == 42)
        self.assertEqual(firefox["threads"], 80)
        self.assertEqual(firefox["start_time"], 1700000000 + 1000 / remote_ops._clk_tck())

    def test_pagination(self):
        page = self.run_op("process.list", {"sort_by": "pid", "order": "asc", "limit": 2, "offset": 2})
        self.assertEqual([i["pid"] for i in page["items"]], [77, 99])
        self.assertEqual(page["returned"], 2)
        empty = self.run_op("process.list", {"offset": 50})
        self.assertEqual(empty["items"], [])

    def test_empty_process_table(self):
        for pid in (1, 42, 77, 99):
            self.proc.remove_process(pid)
        out = self.run_op("process.list")
        self.assertEqual((out["total"], out["items"], out["truncated"]), (0, [], False))

    @unittest.skipIf(os.geteuid() == 0, "root can read mode-000 files")
    def test_access_denied_processes_are_counted_not_fatal(self):
        self.proc.hide_process(42)
        out = self.run_op("process.list")
        self.assertEqual(out["inaccessible"], 1)
        self.assertNotIn(42, [i["pid"] for i in out["items"]])

    def test_output_cap_truncates(self):
        for pid in range(1000, 1200):
            self.proc.add_process(pid, f"worker-{pid}")
        ctx = OperationContext(proc_root=str(self.proc.root), max_output_bytes=3000)
        out = self.run_op("process.list", {"limit": 200}, ctx)
        self.assertTrue(out["truncated"])
        self.assertLess(out["returned"], 200)
        self.assertLessEqual(len(json.dumps(out, separators=(",", ":"))), 3000)

    def test_never_exposes_cmdline_or_environment(self):
        text = json.dumps(self.run_op("process.list"))
        for needle in ("hunter2", "secret-cli", "AWS_SECRET", "cmdline", "environ", "uid"):
            self.assertNotIn(needle, text)

    def test_no_subprocess_is_spawned(self):
        with mock.patch("subprocess.Popen", side_effect=AssertionError("spawned")), mock.patch(
            "os.system", side_effect=AssertionError("spawned")
        ):
            self.run_op("process.list")
            self.run_op("system.status")
            self.run_op("process.inspect", {"pid": 42})


class ProcessInspectTests(OpsTestCase):
    def setUp(self):
        super().setUp()
        self.proc.add_process(42, "firefox", ppid=7, nice=5, start_ticks=12345, vsize=999)

    def test_normal_output(self):
        out = self.run_op("process.inspect", {"pid": 42})
        self.assertEqual(out["pid"], 42)
        self.assertEqual(out["ppid"], 7)
        self.assertEqual(out["nice"], 5)
        self.assertEqual(out["vms_bytes"], 999)
        self.assertEqual(out["start_ticks"], 12345)
        self.assertNotIn("hunter2", json.dumps(out))

    def test_nonexistent_pid(self):
        with self.assertRaises(OperationError) as ctx:
            self.run_op("process.inspect", {"pid": 4242})
        self.assertEqual(ctx.exception.code, "not_found")

    @unittest.skipIf(os.geteuid() == 0, "root can read mode-000 files")
    def test_permission_denied(self):
        self.proc.hide_process(42)
        with self.assertRaises(OperationError) as ctx:
            self.run_op("process.inspect", {"pid": 42})
        self.assertEqual(ctx.exception.code, "permission_denied")

    def test_pid_reuse_detected_by_start_ticks(self):
        with self.assertRaises(OperationError) as ctx:
            self.run_op("process.inspect", {"pid": 42, "start_ticks": 1})
        self.assertEqual(ctx.exception.code, "process_changed")
        ok = self.run_op("process.inspect", {"pid": 42, "start_ticks": 12345})
        self.assertEqual(ok["pid"], 42)

    def test_pid_reuse_during_inspection(self):
        original = remote_ops._read_proc
        calls = {"n": 0}

        def flaky(root, pid, *, now, boot):
            calls["n"] += 1
            if calls["n"] == 2:
                self.proc.add_process(42, "imposter", start_ticks=99999)
            return original(root, pid, now=now, boot=boot)

        with mock.patch.object(remote_ops, "_read_proc", flaky):
            with self.assertRaises(OperationError) as ctx:
                self.run_op("process.inspect", {"pid": 42})
        self.assertEqual(ctx.exception.code, "process_changed")

    def test_process_exits_during_inspection(self):
        original = remote_ops._read_proc
        calls = {"n": 0}

        def vanish(root, pid, *, now, boot):
            calls["n"] += 1
            if calls["n"] == 2:
                self.proc.remove_process(42)
            return original(root, pid, now=now, boot=boot)

        with mock.patch.object(remote_ops, "_read_proc", vanish):
            with self.assertRaises(OperationError) as ctx:
                self.run_op("process.inspect", {"pid": 42})
        self.assertEqual(ctx.exception.code, "not_found")


if __name__ == "__main__":
    unittest.main()
