"""Days 12, 20-23: nexus.db migrations, device identity, grants, approvals, audit."""

import os
import sqlite3
import stat
import tempfile
import time
import unittest
from pathlib import Path

from remote_store import RemoteStore, SCHEMA_VERSION, StoreError, hash_secret, parse_credential


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.store = RemoteStore(":memory:")
        self.addCleanup(self.store.close)


class MigrationTests(unittest.TestCase):
    def test_new_file_is_private_and_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "nexus.db"
            store = RemoteStore(path)
            try:
                self.assertEqual(store.schema_version(), SCHEMA_VERSION)
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(os.stat(path.parent).st_mode), 0o700)
            finally:
                store.close()

    def test_reopen_preserves_data_and_backup_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nexus.db"
            store = RemoteStore(path)
            record, _cred = store.pair("pixel", ("remote.read",))
            backup = store.backup(str(Path(tmp) / "copy.db"))
            store.close()
            again = RemoteStore(path)
            try:
                self.assertEqual(again.device(record.device_id).name, "pixel")
                self.assertEqual(again.permissions(record.device_id), {"remote.read"})
            finally:
                again.close()
            conn = sqlite3.connect(backup)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0], 1)
            conn.close()
            self.assertEqual(stat.S_IMODE(os.stat(backup).st_mode), 0o600)

    def test_refuses_newer_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nexus.db"
            conn = sqlite3.connect(path)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 5}")
            conn.close()
            with self.assertRaises(StoreError):
                RemoteStore(path)

    def test_does_not_touch_location_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            loc = Path(tmp) / "locations.db"
            conn = sqlite3.connect(loc)
            conn.execute("CREATE TABLE locations (x)")
            conn.execute("INSERT INTO locations VALUES (1)")
            conn.commit()
            conn.close()
            before = loc.read_bytes()
            RemoteStore(Path(tmp) / "nexus.db").close()
            self.assertEqual(loc.read_bytes(), before)


class IdentityTests(StoreTestCase):
    def test_pair_returns_credential_and_stores_only_hash(self):
        record, credential = self.store.pair("Pixel 9", ("remote.read",))
        device_id, secret = parse_credential(credential)
        self.assertEqual(device_id, record.device_id)
        self.assertGreaterEqual(len(secret), 43)
        self.assertEqual(record.secret_hash, hash_secret(secret))
        dump = "\n".join(self.store._conn.iterdump())
        self.assertNotIn(secret, dump)

    def test_distinct_identities(self):
        a, ca = self.store.pair("a")
        b, cb = self.store.pair("b")
        self.assertNotEqual(a.device_id, b.device_id)
        self.assertNotEqual(ca.split(".")[1], cb.split(".")[1])

    def test_authenticate(self):
        record, credential = self.store.pair("p")
        device_id, secret = parse_credential(credential)
        self.assertIsNotNone(self.store.authenticate(device_id, secret))
        self.assertIsNone(self.store.authenticate(device_id, secret + "x"))
        self.assertIsNone(self.store.authenticate("dev_ffffffffffffffff", secret))
        self.assertIsNone(self.store.authenticate(device_id, ""))

    def test_revoke_rejects_future_auth_and_clears_grants(self):
        record, credential = self.store.pair("p", ("remote.read",))
        device_id, secret = parse_credential(credential)
        self.assertTrue(self.store.revoke(device_id))
        self.assertFalse(self.store.revoke(device_id))
        self.assertIsNone(self.store.authenticate(device_id, secret))
        self.assertEqual(self.store.permissions(device_id), set())
        self.assertFalse(self.store.session_valid(device_id, hash_secret(secret)))
        with self.assertRaises(StoreError):
            self.store.grant(device_id, "remote.read")
        with self.assertRaises(StoreError):
            self.store.rotate(device_id)

    def test_rotate_invalidates_old_secret(self):
        record, credential = self.store.pair("p")
        device_id, old = parse_credential(credential)
        new_cred = self.store.rotate(device_id)
        _id, new = parse_credential(new_cred)
        self.assertIsNone(self.store.authenticate(device_id, old))
        self.assertIsNotNone(self.store.authenticate(device_id, new))
        self.assertFalse(self.store.session_valid(device_id, hash_secret(old)))
        self.assertTrue(self.store.session_valid(device_id, hash_secret(new)))

    def test_revoke_all(self):
        for name in ("a", "b", "c"):
            self.store.pair(name)
        self.assertEqual(self.store.revoke_all(), 3)
        self.assertTrue(all(d.revoked for d in self.store.devices()))

    def test_repair_does_not_inherit_grants(self):
        old, _ = self.store.pair("phone", ("remote.read",))
        self.store.revoke(old.device_id)
        new, _ = self.store.pair("phone")
        self.assertEqual(self.store.permissions(new.device_id), set())

    def test_bad_names_and_credentials(self):
        for name in ("", " ", "<script>", "a" * 80, "../x"):
            with self.subTest(name=name):
                with self.assertRaises(StoreError):
                    self.store.pair(name)
        for cred in ("", "dev_x.y", "dev_0123456789abcdef", "dev_0123456789abcdef.short", "token"):
            with self.subTest(cred=cred):
                with self.assertRaises(StoreError):
                    parse_credential(cred)


class GrantTests(StoreTestCase):
    def test_default_deny_and_exact_strings(self):
        record, _ = self.store.pair("p")
        self.assertEqual(self.store.permissions(record.device_id), set())
        for perm in ("remote.*", "REMOTE.READ", "remote.read ", "remote", "admin"):
            with self.subTest(perm=perm):
                with self.assertRaises(StoreError):
                    self.store.grant(record.device_id, perm)
        self.store.grant(record.device_id, "remote.read")
        self.assertEqual(self.store.permissions(record.device_id), {"remote.read"})
        self.assertTrue(self.store.ungrant(record.device_id, "remote.read"))
        self.assertEqual(self.store.permissions(record.device_id), set())

    def test_scripts_permission_is_deferred(self):
        record, _ = self.store.pair("p")
        with self.assertRaises(StoreError):
            self.store.grant(record.device_id, "remote.scripts")
        with self.assertRaises(StoreError):
            self.store.pair("q", ("remote.scripts",))

    def test_unknown_permission_rows_are_ignored(self):
        record, _ = self.store.pair("p")
        self.store._conn.execute(
            "INSERT INTO grants VALUES (?,?,?)", (record.device_id, "remote.everything", time.time())
        )
        self.assertEqual(self.store.permissions(record.device_id), set())


class ApprovalTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.device, _ = self.store.pair("p")
        self.binding = dict(
            request_id="req_0000000000000001",
            device_id=self.device.device_id,
            operation="test.approve",
            params_hash="h1",
        )

    def make(self, ttl=60.0):
        return self.store.create_approval(ttl=ttl, **self.binding)

    def test_approve_then_consume_once(self):
        rec = self.make()
        self.assertEqual(self.store.decide(rec.approval_id, True), "approved")
        self.assertTrue(self.store.consume_approval(rec.approval_id, **self.binding))
        self.assertFalse(self.store.consume_approval(rec.approval_id, **self.binding))
        self.assertEqual(self.store.decide(rec.approval_id, True), "consumed")

    def test_binding_mismatch_fails(self):
        for field, value in (
            ("request_id", "req_0000000000000002"),
            ("device_id", "dev_ffffffffffffffff"),
            ("operation", "other.op"),
            ("params_hash", "h2"),
        ):
            with self.subTest(field=field):
                rec = self.make()
                self.store.decide(rec.approval_id, True)
                self.assertFalse(
                    self.store.consume_approval(rec.approval_id, **{**self.binding, field: value})
                )

    def test_expired_cannot_be_approved_or_used(self):
        rec = self.make(ttl=-1)
        self.assertEqual(self.store.decide(rec.approval_id, True), "expired")
        self.assertFalse(self.store.consume_approval(rec.approval_id, **self.binding))
        self.assertEqual(self.store.pending_approvals(), [])

    def test_denied_cannot_be_approved(self):
        rec = self.make()
        self.assertEqual(self.store.decide(rec.approval_id, False), "denied")
        self.assertEqual(self.store.decide(rec.approval_id, True), "denied")
        self.assertFalse(self.store.consume_approval(rec.approval_id, **self.binding))

    def test_revoke_and_rotate_cancel_approvals(self):
        rec = self.make()
        self.store.rotate(self.device.device_id)
        self.assertEqual(self.store.approval(rec.approval_id).state, "cancelled")
        rec2 = self.make()
        self.store.revoke(self.device.device_id)
        self.assertEqual(self.store.approval(rec2.approval_id).state, "cancelled")

    def test_unknown_approval(self):
        with self.assertRaises(StoreError):
            self.store.decide("apr_ffffffffffffffff", True)


class AuditTests(StoreTestCase):
    def test_record_list_and_prune(self):
        now = time.time()
        self.store.record_audit(
            request_id="r" * 100, device_id="dev_x", operation="system.status",
            decision="allowed", status="completed", started_at=now - 0.5, finished_at=now,
        )
        self.store.record_audit(
            request_id="old", device_id="dev_x", operation="system.status",
            decision="allowed", status="completed", finished_at=now - 100 * 86400,
        )
        rows = self.store.audit(limit=10)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(rows[0]["request_id"]) if rows[0]["request_id"] != "old" else 64, 64)
        self.assertEqual(self.store.prune_audit(90), 1)
        self.assertEqual(len(self.store.audit()), 1)
        self.assertEqual(self.store.audit()[0]["duration_ms"], 500)


if __name__ == "__main__":
    unittest.main()
