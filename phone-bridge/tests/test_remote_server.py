"""Days 11, 21-23, 25: end-to-end Remote over a real WebSocket server.

These double as the security test suite: every test name maps to a row in
docs/security/NEXUS_SECURITY_TEST_REPORT.md.
"""

from __future__ import annotations

import asyncio
import json
import time
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from unittest import mock

import server as server_module
from client import BridgeClient, ClientError
from config import Config
from remote_dispatcher import DispatcherLimits
from remote_registry import Operation, OperationRegistry, Param, Risk, default_registry
from remote_store import RemoteStore
from server import BridgeServer
from storage import LocationStore
from tests.fakeproc import FakeProc
from web import MapWebServer


def _ok(params, ctx):
    return {"ok": True, "echo": params}


def _slow(params, ctx):
    time.sleep(0.6)
    return {"ok": True}


def _big(params, ctx):
    return {"blob": "x" * 5000}


def _crash(params, ctx):
    raise RuntimeError("boom with secret detail")


def test_registry() -> OperationRegistry:
    ops = default_registry().all()
    ops += [
        Operation("test.approve", "remote.execute", Risk.R2, "needs approval", _ok,
                  params={"n": Param(int, minimum=0, maximum=10)}),
        Operation("test.slow", "remote.read", Risk.R1, "slow", _slow, timeout=0.2),
        Operation("test.big", "remote.read", Risk.R1, "big", _big, max_output_bytes=1024),
        Operation("test.crash", "remote.read", Risk.R1, "crash", _crash),
    ]
    return OperationRegistry(ops)


class RemoteServerTestCase(unittest.IsolatedAsyncioTestCase):
    limits = DispatcherLimits(approval_poll=0.05, approval_ttl=2.0)
    config_overrides: dict = {}

    async def asyncSetUp(self):
        self.proc = FakeProc()
        self.proc.add_process(42, "firefox")
        self.addCleanup(self.proc.cleanup)
        self.config = Config(
            host="127.0.0.1",
            port=0,
            token="legacy-token-abc",
            notify_backend="none",
            map_host="127.0.0.1",
            map_port=0,
            **self.config_overrides,
        )
        self.store = LocationStore(":memory:")
        self.rstore = RemoteStore(":memory:")
        self.server = BridgeServer(
            self.config,
            store=self.store,
            remote_store=self.rstore,
            registry=test_registry(),
            proc_root=str(self.proc.root),
        )
        self.server.dispatcher.limits = replace(self.limits)
        await self.server.start()
        self.client_config = replace(self.config, port=self.server.port)
        sweep = mock.patch.object(server_module, "REVOCATION_SWEEP_SECONDS", 0.1)
        sweep.start()
        self.addCleanup(sweep.stop)

    async def asyncTearDown(self):
        await self.server.stop()
        self.store.close()
        self.rstore.close()

    def pair(self, *grants: str) -> tuple[str, str]:
        record, credential = self.rstore.pair("pixel-9", grants)
        return record.device_id, credential

    def client(self, credential: str = "", device: str = "pixel-9") -> BridgeClient:
        return BridgeClient(self.client_config, device=device, credential=credential)


class HappyPathTests(RemoteServerTestCase):
    async def test_paired_device_gets_capabilities_and_status(self):
        device_id, cred = self.pair("remote.read")
        async with self.client(cred) as c:
            self.assertEqual(c.hello_ack["auth"], "device")
            self.assertEqual(c.hello_ack["device_id"], device_id)
            caps = await c.next_frame("remote.capabilities")
            self.assertTrue(caps["available"])
            self.assertEqual(caps["permissions"], ["remote.read"])
            op_ids = [op["id"] for op in caps["operations"]]
            self.assertIn("system.status", op_ids)
            self.assertNotIn("test.approve", op_ids)  # needs remote.execute

            resp, events = await c.remote("system.status")
            self.assertEqual(resp["status"], "completed", resp)
            self.assertEqual(resp["result"]["host"], self.config.device_name)
            self.assertEqual([e["state"] for e in events], ["accepted", "running"])

            listing, _ = await c.remote("process.list", {"limit": 5})
            self.assertEqual(listing["result"]["items"][0]["name"], "firefox")
            detail, _ = await c.remote("process.inspect", {"pid": 42})
            self.assertEqual(detail["result"]["pid"], 42)
        self.assertGreaterEqual(len(self.rstore.audit()), 3)

    async def test_legacy_messages_still_route_with_device_auth(self):
        _id, cred = self.pair("remote.read")
        async with self.client(cred) as c:
            await c.notify("WhatsApp", "Alice", "hi", package="com.whatsapp")
            await c.location(37.0, -122.0, battery=50)
            resp, _ = await c.remote("system.status")
            self.assertEqual(resp["status"], "completed")
        await asyncio.sleep(0.1)
        self.assertEqual(self.server.notifier.sent, 1)
        self.assertEqual(self.store.count("pixel-9"), 1)

    async def test_slow_request_does_not_block_heartbeat_or_legacy(self):
        _id, cred = self.pair("remote.read")
        async with self.client(cred) as c:
            await c.send_raw({
                "type": "remote.request", "v": 1, "request_id": "slow000000000001",
                "operation": "test.slow", "params": {}, "ts": time.time(),
            })
            await c.notify("WhatsApp", "Bob", "while slow", package="com.whatsapp")
            resp, _ = await c.wait_response("slow000000000001")
            self.assertEqual(resp["error"]["code"], "timeout")
        await asyncio.sleep(0.05)
        self.assertEqual(self.server.notifier.sent, 1)


class AuthenticationSecurityTests(RemoteServerTestCase):
    async def test_legacy_token_session_never_gets_remote(self):
        async with self.client() as c:
            self.assertEqual(c.hello_ack["auth"], "token")
            caps = await c.next_frame("remote.capabilities")
            self.assertFalse(caps["available"])
            resp, _ = await c.remote("system.status")
            self.assertEqual(resp["status"], "rejected")
            self.assertEqual(resp["error"]["code"], "not_authorized")
            await c.notify("WhatsApp", "Alice", "legacy still works", package="com.whatsapp")
        await asyncio.sleep(0.05)
        self.assertEqual(self.server.notifier.sent, 1)

    async def test_bad_device_credential_never_falls_back_to_token(self):
        device_id, cred = self.pair("remote.read")
        forged = f"{device_id}.{'A' * 43}"
        with self.assertRaises(ClientError):
            async with self.client(forged):
                pass
        # Even with the correct legacy token alongside, a bad device credential fails.
        import protocol
        from websockets.asyncio.client import connect

        async with connect(f"ws://127.0.0.1:{self.server.port}") as ws:
            await ws.send(protocol.dumps({
                "type": "hello", "device": "x", "token": self.config.token,
                "device_id": device_id, "device_secret": "A" * 43,
            }))
            reply = json.loads(await ws.recv())
            self.assertEqual(reply["code"], "auth_failed")

    async def test_unknown_device_rejected(self):
        with self.assertRaises(ClientError):
            async with self.client("dev_ffffffffffffffff." + "B" * 43):
                pass

    async def test_auth_failures_are_rate_limited(self):
        device_id, cred = self.pair()
        for _ in range(self.server.authenticator.max_attempts):
            with self.assertRaises(ClientError):
                async with self.client(f"{device_id}.{'C' * 43}"):
                    pass
        with self.assertRaises(ClientError):  # correct credential now locked out too
            async with self.client(cred):
                pass

    async def test_revoked_device_cannot_connect_and_live_session_is_closed(self):
        device_id, cred = self.pair("remote.read")
        async with self.client(cred) as c:
            ok, _ = await c.remote("system.status")
            self.assertEqual(ok["status"], "completed")
            self.rstore.revoke(device_id)
            resp, _ = await c.remote("system.status")
            self.assertIn(resp["error"]["code"], {"device_revoked", "permission_denied"})
            await asyncio.sleep(0.3)
            self.assertEqual(self.server.client_count, 0)
        with self.assertRaises(ClientError):
            async with self.client(cred):
                pass

    async def test_idle_revoked_session_closed_by_sweep(self):
        device_id, cred = self.pair("remote.read")
        async with self.client(cred) as c:
            await c.next_frame("remote.capabilities")
            self.assertEqual(self.server.client_count, 1)
            self.rstore.revoke(device_id)
            await asyncio.sleep(0.4)
            self.assertEqual(self.server.client_count, 0)

    async def test_rotation_invalidates_old_credential_and_session(self):
        device_id, cred = self.pair("remote.read")
        async with self.client(cred) as c:
            await c.next_frame("remote.capabilities")
            new_cred = self.rstore.rotate(device_id)
            await asyncio.sleep(0.4)
            self.assertEqual(self.server.client_count, 0)
        with self.assertRaises(ClientError):
            async with self.client(cred):
                pass
        async with self.client(new_cred) as c:
            resp, _ = await c.remote("system.status")
            self.assertEqual(resp["status"], "completed")


class LegacyDisabledTests(RemoteServerTestCase):
    config_overrides = {"legacy_token_expires": "2001-01-01"}

    async def test_expired_legacy_token_rejected_but_device_works(self):
        with self.assertRaises(ClientError):
            async with self.client():
                pass
        _id, cred = self.pair("remote.read")
        async with self.client(cred) as c:
            resp, _ = await c.remote("system.status")
            self.assertEqual(resp["status"], "completed")


class SessionAgeTests(RemoteServerTestCase):
    config_overrides = {"remote_session_max_age": 0.05}

    async def test_session_closed_after_max_age(self):
        _id, cred = self.pair("remote.read")
        async with self.client(cred) as c:
            await c.next_frame("remote.capabilities")
            await asyncio.sleep(0.4)
            self.assertEqual(self.server.client_count, 0)


class AuthorizationSecurityTests(RemoteServerTestCase):
    async def test_default_deny_without_grant(self):
        _id, cred = self.pair()
        async with self.client(cred) as c:
            caps = await c.next_frame("remote.capabilities")
            self.assertTrue(caps["available"])
            self.assertEqual(caps["operations"], [])
            resp, _ = await c.remote("system.status")
            self.assertEqual(resp["error"]["code"], "permission_denied")

    async def test_read_grant_cannot_invoke_execute_operation(self):
        _id, cred = self.pair("remote.read")
        async with self.client(cred) as c:
            resp, events = await c.remote("test.approve", {"n": 1})
            self.assertEqual(resp["error"]["code"], "permission_denied")
            self.assertEqual(events, [])  # rejected before approval/execution
        self.assertEqual(self.rstore.pending_approvals(), [])

    async def test_permission_revoked_mid_session_takes_effect_immediately(self):
        device_id, cred = self.pair("remote.read")
        async with self.client(cred) as c:
            self.assertEqual((await c.remote("system.status"))[0]["status"], "completed")
            self.rstore.ungrant(device_id, "remote.read")
            resp, _ = await c.remote("system.status")
            self.assertEqual(resp["error"]["code"], "permission_denied")

    async def test_device_id_spoofing_rejected(self):
        _a, cred = self.pair("remote.read")
        other, _ = self.rstore.pair("other", ("remote.read",))
        async with self.client(cred) as c:
            resp, _ = await c.remote("system.status", extra={"device_id": other.device_id})
            self.assertEqual(resp["error"]["code"], "invalid_request")


class InputSecurityTests(RemoteServerTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        _id, self.cred = self.pair("remote.read", "remote.execute")

    async def test_unknown_operation_and_injection_attempts(self):
        async with self.client(self.cred) as c:
            for op in ("shell.exec", "process.kill", "system.status;id"):
                resp, _ = await c.remote(op)
                self.assertEqual(resp["status"], "rejected", op)
                self.assertIn(resp["error"]["code"], {"unknown_operation", "invalid_request"})
            resp, _ = await c.remote("process.list", {"sort_by": "memory; rm -rf ~"})
            self.assertEqual(resp["error"]["code"], "invalid_params")
            resp, _ = await c.remote("process.inspect", {"pid": "1 || id"})
            self.assertEqual(resp["error"]["code"], "invalid_params")
            resp, _ = await c.remote("process.list", {"command": "id"})
            self.assertEqual(resp["error"]["code"], "invalid_params")

    async def test_malformed_versions_and_fields(self):
        async with self.client(self.cred) as c:
            base = {"type": "remote.request", "v": 1, "request_id": "mal0000000000001",
                    "operation": "system.status", "params": {}, "ts": time.time()}
            await c.send_raw({**base, "v": 99})
            resp, _ = await c.wait_response("mal0000000000001")
            self.assertEqual(resp["error"]["code"], "unsupported_version")
            await c.send_raw({**base, "request_id": "mal0000000000002", "cmd": "id"})
            resp, _ = await c.wait_response("mal0000000000002")
            self.assertEqual(resp["error"]["code"], "invalid_request")
            await c.send_raw({**base, "type": "remote.response", "request_id": "mal0000000000003"})
            resp, _ = await c.wait_response("mal0000000000003")
            self.assertEqual(resp["error"]["code"], "invalid_request")
            # Garbage must not kill the connection.
            await c.send_raw("{not json")
            ok, _ = await c.remote("system.status")
            self.assertEqual(ok["status"], "completed")

    async def test_oversized_frame(self):
        async with self.client(self.cred) as c:
            await c.send_raw({"type": "remote.request", "v": 1, "request_id": "big0000000000001",
                              "operation": "system.status", "params": {"pad": "x" * 20000},
                              "ts": time.time()})
            resp, _ = await c.wait_response("big0000000000001")
            self.assertEqual(resp["error"]["code"], "payload_too_large")

    async def test_replay_and_stale_requests(self):
        async with self.client(self.cred) as c:
            first, _ = await c.remote("system.status", request_id="replay0000000001")
            self.assertEqual(first["status"], "completed")
            again, _ = await c.remote("system.status", request_id="replay0000000001")
            self.assertEqual(again["error"]["code"], "duplicate_request")
            stale, _ = await c.remote("system.status", ts=time.time() - 3600)
            self.assertEqual(stale["status"], "expired")
            future, _ = await c.remote("system.status", ts=time.time() + 3600)
            self.assertEqual(future["status"], "expired")

    async def test_errors_do_not_leak_internal_details(self):
        async with self.client(self.cred) as c:
            resp, _ = await c.remote("test.crash")
            self.assertEqual(resp["error"]["code"], "operation_error")
            self.assertNotIn("secret", json.dumps(resp))
            ok, _ = await c.remote("system.status")
            self.assertEqual(ok["status"], "completed")


class LimitsTests(RemoteServerTestCase):
    limits = DispatcherLimits(rate_limit=3, rate_window=60, per_device_concurrency=1,
                              approval_poll=0.05, approval_ttl=2.0)

    async def asyncSetUp(self):
        await super().asyncSetUp()
        _id, self.cred = self.pair("remote.read")

    async def test_rate_limit(self):
        async with self.client(self.cred) as c:
            codes = []
            for _ in range(4):
                resp, _ = await c.remote("process.inspect", {"pid": 42})
                codes.append(resp.get("error", {}).get("code", resp["status"]))
            self.assertEqual(codes, ["completed"] * 3 + ["rate_limited"])

    async def test_concurrency_exhaustion(self):
        async with self.client(self.cred) as c:
            now = time.time()
            for rid in ("conc000000000001", "conc000000000002"):
                await c.send_raw({"type": "remote.request", "v": 1, "request_id": rid,
                                  "operation": "test.slow", "params": {}, "ts": now})
            second, _ = await c.wait_response("conc000000000002")
            self.assertEqual(second["error"]["code"], "busy")
            first, _ = await c.wait_response("conc000000000001")
            self.assertEqual(first["error"]["code"], "timeout")
            # Slot is held until the worker thread really finishes.
            await asyncio.sleep(0.7)
            self.assertEqual(self.server.dispatcher.inflight(), 0)


class ExecutionLimitsTests(RemoteServerTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        _id, self.cred = self.pair("remote.read")

    async def test_timeout_and_output_cap(self):
        async with self.client(self.cred) as c:
            slow, _ = await c.remote("test.slow")
            self.assertEqual((slow["status"], slow["error"]["code"]), ("failed", "timeout"))
            big, _ = await c.remote("test.big")
            self.assertEqual(big["error"]["code"], "output_too_large")
            self.assertLess(len(json.dumps(big)), 1024)

    async def test_disconnect_cancels_and_releases(self):
        c = self.client(self.cred)
        await c.__aenter__()
        await c.send_raw({"type": "remote.request", "v": 1, "request_id": "disc000000000001",
                          "operation": "test.slow", "params": {}, "ts": time.time()})
        await asyncio.sleep(0.05)
        await c.close()
        await asyncio.sleep(0.9)
        self.assertEqual(self.server.dispatcher.inflight(), 0)
        self.assertEqual(self.server.client_count, 0)

    async def test_audit_has_metadata_only(self):
        async with self.client(self.cred) as c:
            await c.remote("process.inspect", {"pid": 42})
            await c.remote("process.inspect", {"pid": 4242})
        rows = self.rstore.audit()
        text = json.dumps(rows)
        self.assertNotIn("firefox", text)
        self.assertNotIn("4242", text)
        self.assertEqual({r["decision"] for r in rows}, {"allowed"})
        self.assertEqual({r["status"] for r in rows}, {"completed", "failed"})
        self.assertTrue(all(len(r["params_hash"]) == 64 for r in rows))


class ApprovalTests(RemoteServerTestCase):
    limits = DispatcherLimits(approval_poll=0.05, approval_ttl=0.5)

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.device_id, self.cred = self.pair("remote.read", "remote.execute")

    async def _approve_when_pending(self, approve: bool = True) -> str:
        for _ in range(100):
            pending = self.rstore.pending_approvals()
            if pending:
                self.rstore.decide(pending[0].approval_id, approve)
                return pending[0].approval_id
            await asyncio.sleep(0.02)
        self.fail("no approval appeared")

    async def test_approved_request_runs_once(self):
        async with self.client(self.cred) as c:
            approver = asyncio.create_task(self._approve_when_pending(True))
            resp, events = await c.remote("test.approve", {"n": 3})
            approval_id = await approver
            self.assertEqual(resp["status"], "completed", resp)
            types = [e["type"] for e in events]
            self.assertIn("remote.approval_required", types)
            self.assertIn("remote.approval_result", types)
            self.assertEqual(self.rstore.approval(approval_id).state, "consumed")
        row = self.rstore.audit(limit=1)[0]
        self.assertEqual(row["approval_state"], "approved")

    async def test_denied(self):
        async with self.client(self.cred) as c:
            approver = asyncio.create_task(self._approve_when_pending(False))
            resp, _ = await c.remote("test.approve", {"n": 1})
            await approver
            self.assertEqual(resp["error"]["code"], "approval_denied")

    async def test_expired_fails_closed(self):
        async with self.client(self.cred) as c:
            resp, _ = await c.remote("test.approve", {"n": 1})
            self.assertEqual(resp["error"]["code"], "approval_expired")

    async def test_approval_is_bound_to_its_own_request_and_params(self):
        async with self.client(self.cred) as c:
            # Approve request A, then send B with different params: B gets its own
            # approval and is not satisfied by A's.
            approver = asyncio.create_task(self._approve_when_pending(True))
            a, _ = await c.remote("test.approve", {"n": 1})
            a_id = await approver
            self.assertEqual(a["status"], "completed")
            b, events = await c.remote("test.approve", {"n": 2})
            self.assertEqual(b["error"]["code"], "approval_expired")
            b_ids = {e.get("approval_id") for e in events if e.get("approval_id")}
            self.assertNotIn(a_id, b_ids)

    async def test_replayed_approval_cannot_be_reused(self):
        async with self.client(self.cred) as c:
            approver = asyncio.create_task(self._approve_when_pending(True))
            resp, _ = await c.remote("test.approve", {"n": 1}, request_id="appr000000000001")
            approval_id = await approver
            self.assertEqual(resp["status"], "completed")
            self.assertEqual(self.rstore.decide(approval_id, True), "consumed")
            again, _ = await c.remote("test.approve", {"n": 1}, request_id="appr000000000001")
            self.assertEqual(again["error"]["code"], "duplicate_request")


class DashboardIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_dashboard_exposes_no_remote_routes_and_locate_is_post_only(self):
        config = Config(host="127.0.0.1", port=0, token="t-123456", notify_backend="none",
                        map_host="127.0.0.1", map_port=0, remote_enabled=False)
        store = LocationStore(":memory:")
        web = MapWebServer(config, store)
        await web.start()
        try:
            for path in ("/api/remote", "/api/remote/request", "/api/devices", "/api/locate"):
                with self.subTest(path=path):
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        await asyncio.to_thread(urllib.request.urlopen, f"{web.url}{path}", None, 5)
                    self.assertEqual(ctx.exception.code, 404)
            html = await asyncio.to_thread(
                lambda: urllib.request.urlopen(f"{web.url}/", timeout=5).read().decode()
            )
            self.assertIn("function esc(", html)
            # Every HTML sink (innerHTML / bindPopup) escapes the device name.
            self.assertNotIn("<b>${pt.device}", html)
            self.assertNotIn("<b>${focus.device}", html)
            self.assertNotIn('history-meta">${pt.device}', html)
            self.assertIn("<b>${esc(pt.device)}</b>", html)
            self.assertIn('history-meta">${esc(pt.device)}', html)
        finally:
            await web.stop()
            store.close()


class RemoteDisabledTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_disabled_rejects_cleanly(self):
        config = Config(host="127.0.0.1", port=0, token="legacy-token-abc", notify_backend="none",
                        remote_enabled=False)
        store = LocationStore(":memory:")
        srv = BridgeServer(config, store=store)
        await srv.start()
        try:
            self.assertIsNone(srv.dispatcher)
            async with BridgeClient(replace(config, port=srv.port)) as c:
                resp, _ = await c.remote("system.status")
                self.assertEqual(resp["error"]["code"], "remote_disabled")
        finally:
            await srv.stop()
            store.close()


if __name__ == "__main__":
    unittest.main()
