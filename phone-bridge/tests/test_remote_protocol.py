"""Day 6: versioned remote message schemas + shared fixtures."""

import json
import unittest
from pathlib import Path

import protocol
import remote_protocol as rp
from remote_protocol import RemoteProtocolError, RemoteRequest, Status

FIXTURES = Path(__file__).resolve().parents[2] / "protocol" / "fixtures" / "remote"


def load_fixtures(direction: str) -> list[dict]:
    out = []
    for path in sorted(FIXTURES.glob("*.json")):
        data = json.loads(path.read_text())
        if data["direction"] == direction:
            out.append(data)
    return out


def valid(**overrides):
    frame = {
        "type": "remote.request",
        "v": 1,
        "request_id": "abcdef0123456789",
        "operation": "system.status",
        "params": {},
        "ts": 1795440000.0,
    }
    frame.update(overrides)
    return frame


class RemoteRequestSchemaTests(unittest.TestCase):
    def test_fixtures_exist(self):
        self.assertGreaterEqual(len(load_fixtures("android_to_linux")), 5)
        self.assertGreaterEqual(len(load_fixtures("linux_to_android")), 5)

    def test_inbound_fixtures(self):
        for fx in load_fixtures("android_to_linux"):
            with self.subTest(fx["name"]):
                raw = json.dumps(fx["frame"])
                if fx["expect"] == "valid":
                    req = RemoteRequest.parse(fx["frame"], len(raw))
                    self.assertEqual(req.to_dict(), fx["frame"])
                else:
                    with self.assertRaises(RemoteProtocolError) as ctx:
                        RemoteRequest.parse(fx["frame"], len(raw))
                    self.assertEqual(ctx.exception.code, fx["error_code"])

    def test_rejects_unknown_and_missing_version(self):
        for v in (0, 2, "1", None, True, 1.5):
            with self.subTest(v=v):
                with self.assertRaises(RemoteProtocolError):
                    RemoteRequest.parse(valid(v=v))

    def test_rejects_bad_identifiers(self):
        for rid in ("", "short", "x" * 65, "has space 123456", "a/b/c/d/e/f/g/h", 12345678):
            with self.subTest(rid=rid):
                with self.assertRaises(RemoteProtocolError):
                    RemoteRequest.parse(valid(request_id=rid))
        for op in ("", "status", "System.Status", "sys.status;id", "a.b.c.d.e", "x" * 80 + ".y", 7):
            with self.subTest(op=op):
                with self.assertRaises(RemoteProtocolError):
                    RemoteRequest.parse(valid(operation=op))

    def test_rejects_oversized_and_deep_payloads(self):
        with self.assertRaises(RemoteProtocolError) as ctx:
            RemoteRequest.parse(valid(), raw_size=rp.MAX_REMOTE_FRAME_BYTES + 1)
        self.assertEqual(ctx.exception.code, "payload_too_large")
        with self.assertRaises(RemoteProtocolError):
            RemoteRequest.parse(valid(params={"k": "x" * (rp.MAX_PARAMS_BYTES + 10)}))
        with self.assertRaises(RemoteProtocolError):
            RemoteRequest.parse(valid(params={"a": {"b": {"c": {"d": 1}}}}))
        with self.assertRaises(RemoteProtocolError):
            RemoteRequest.parse(valid(params={"n": float("nan")}))

    def test_rejects_bad_ts_and_device_id(self):
        for ts in (None, "now", 0, -5, float("inf"), True):
            with self.subTest(ts=ts):
                with self.assertRaises(RemoteProtocolError):
                    RemoteRequest.parse(valid(ts=ts))
        with self.assertRaises(RemoteProtocolError):
            RemoteRequest.parse(valid(device_id="root"))
        req = RemoteRequest.parse(valid(device_id="dev_0123456789abcdef"))
        self.assertEqual(req.device_id, "dev_0123456789abcdef")

    def test_request_id_survives_on_error_when_valid(self):
        with self.assertRaises(RemoteProtocolError) as ctx:
            RemoteRequest.parse(valid(params=[1]))
        self.assertEqual(ctx.exception.request_id, "abcdef0123456789")

    def test_params_hash_is_canonical(self):
        self.assertEqual(rp.params_hash({"a": 1, "b": 2}), rp.params_hash({"b": 2, "a": 1}))
        self.assertNotEqual(rp.params_hash({"a": 1}), rp.params_hash({"a": 2}))


class OutboundFrameTests(unittest.TestCase):
    def _shape(self, name: str) -> dict:
        return json.loads((FIXTURES / f"{name}.json").read_text())["frame"]

    def test_response_shapes_match_fixtures(self):
        ok = rp.RemoteResponse("a1b2c3d4e5f60718", Status.COMPLETED, "system.status", result={})
        self.assertEqual(set(ok.to_dict()), set(self._shape("response_completed_status")))
        bad = rp.rejected("a1b2c3d4e5f60718", "permission_denied", "nope", "system.status")
        self.assertEqual(set(bad.to_dict()), set(self._shape("response_rejected_permission")))
        self.assertEqual(bad.to_dict()["error"]["code"], "permission_denied")

    def test_event_and_approval_shapes(self):
        ev = rp.event("a1b2c3d4e5f60718", rp.EventState.ACCEPTED, operation="system.status")
        self.assertEqual(set(ev), set(self._shape("event_accepted")))
        req = rp.approval_required("a1b2c3d4e5f60718", "apr_0123456789abcdef", "test.approve", 1.0)
        self.assertEqual(set(req), set(self._shape("approval_required")))
        res = rp.approval_result("a1b2c3d4e5f60718", "apr_0123456789abcdef", rp.ApprovalOutcome.DENIED)
        self.assertEqual(set(res), set(self._shape("approval_result")))

    def test_capabilities_shapes(self):
        cap = rp.capabilities(available=True, server="x", device_id="dev_0123456789abcdef",
                              permissions=["remote.read"], operations=[])
        self.assertEqual(set(cap), set(self._shape("capabilities_available")))
        legacy = rp.capabilities(available=False, server="x", reason="legacy_auth")
        self.assertEqual(set(legacy), set(self._shape("capabilities_legacy")))

    def test_error_message_is_bounded(self):
        resp = rp.rejected("a1b2c3d4e5f60718", "x", "m" * 5000)
        self.assertLessEqual(len(resp.to_dict()["error"]["message"]), rp.MAX_ERROR_MESSAGE)


class LegacyCompatibilityTests(unittest.TestCase):
    def test_legacy_hello_still_requires_token(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.Hello.from_dict({"type": "hello", "device": "p"})
        hello = protocol.Hello.from_dict({"type": "hello", "device": "p", "token": "t"})
        self.assertFalse(hello.uses_device_credential)

    def test_device_hello_needs_secret(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.Hello.from_dict({"type": "hello", "device": "p", "device_id": "dev_0123456789abcdef"})
        hello = protocol.Hello.from_dict(
            {"type": "hello", "device": "p", "device_id": "dev_0123456789abcdef", "device_secret": "s" * 43}
        )
        self.assertTrue(hello.uses_device_credential)
        self.assertEqual(hello.token, "")
        self.assertIn("device_secret", hello.to_dict())

    def test_remote_types_are_not_legacy_events(self):
        self.assertTrue(rp.is_remote_type("remote.request"))
        self.assertFalse(rp.is_remote_type("notification"))
        with self.assertRaises(protocol.ProtocolError):
            protocol.parse_event({"type": "remote.request"})


if __name__ == "__main__":
    unittest.main()
