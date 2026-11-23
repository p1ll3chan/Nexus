"""Persistent state for Nexus Remote: device identities, grants, approvals, audit.

Stored in its own SQLite database (``nexus.db``) so remote features can never
damage the GPS history in ``locations.db``.

Security notes
--------------
* Device secrets are 256-bit random values; only SHA-256(secret) is stored.
  Verification uses a constant-time comparison.
* The database file is created with mode 0600 inside a 0700 directory.
* Audit rows hold metadata only: never parameter values, results, notification
  content or coordinates.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from remote_registry import GRANTABLE_PERMISSIONS, KNOWN_PERMISSIONS

SCHEMA_VERSION = 1
DEVICE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
CREDENTIAL_RE = re.compile(r"^(dev_[0-9a-f]{16})\.([A-Za-z0-9_-]{32,128})$")
DEFAULT_APPROVAL_TTL = 60.0

_MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE devices (
        device_id    TEXT PRIMARY KEY,
        name         TEXT NOT NULL,
        secret_hash  TEXT NOT NULL,
        created_at   REAL NOT NULL,
        rotated_at   REAL,
        revoked_at   REAL,
        last_seen_at REAL
    );
    CREATE TABLE grants (
        device_id  TEXT NOT NULL REFERENCES devices(device_id),
        permission TEXT NOT NULL,
        granted_at REAL NOT NULL,
        PRIMARY KEY (device_id, permission)
    );
    CREATE TABLE approvals (
        approval_id TEXT PRIMARY KEY,
        request_id  TEXT NOT NULL,
        device_id   TEXT NOT NULL,
        operation   TEXT NOT NULL,
        params_hash TEXT NOT NULL,
        created_at  REAL NOT NULL,
        expires_at  REAL NOT NULL,
        state       TEXT NOT NULL,
        decided_at  REAL
    );
    CREATE INDEX approvals_state ON approvals(state, expires_at);
    CREATE TABLE audit (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id     TEXT NOT NULL,
        device_id      TEXT NOT NULL,
        operation      TEXT NOT NULL,
        params_hash    TEXT NOT NULL DEFAULT '',
        decision       TEXT NOT NULL,
        reason         TEXT NOT NULL DEFAULT '',
        approval_state TEXT NOT NULL DEFAULT 'not_required',
        status         TEXT NOT NULL,
        error_code     TEXT NOT NULL DEFAULT '',
        truncated      INTEGER NOT NULL DEFAULT 0,
        started_at     REAL,
        finished_at    REAL NOT NULL,
        duration_ms    INTEGER
    );
    CREATE INDEX audit_device_time ON audit(device_id, finished_at);
    """,
}


class StoreError(ValueError):
    pass


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def parse_credential(credential: str) -> tuple[str, str]:
    match = CREDENTIAL_RE.match(credential.strip())
    if not match:
        raise StoreError("credential must look like dev_<16 hex>.<secret>")
    return match.group(1), match.group(2)


@dataclass(frozen=True)
class DeviceRecord:
    device_id: str
    name: str
    secret_hash: str
    created_at: float
    rotated_at: float | None
    revoked_at: float | None
    last_seen_at: float | None

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "created_at": self.created_at,
            "rotated_at": self.rotated_at,
            "revoked_at": self.revoked_at,
            "last_seen_at": self.last_seen_at,
        }


@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: str
    request_id: str
    device_id: str
    operation: str
    params_hash: str
    created_at: float
    expires_at: float
    state: str
    decided_at: float | None


class RemoteStore:
    def __init__(self, path: str | os.PathLike[str] = ":memory:") -> None:
        raw = str(path)
        self.path = raw
        if raw != ":memory:":
            target = Path(raw).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(target.parent, 0o700)
            except OSError:
                pass
            if not target.exists():
                fd = os.open(target, os.O_CREAT | os.O_WRONLY, 0o600)
                os.close(fd)
            else:
                try:
                    os.chmod(target, 0o600)
                except OSError:
                    pass
            self.path = str(target)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
        self.migrate()

    # ----------------------------------------------------------------- schema

    def schema_version(self) -> int:
        with self._lock:
            return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self, *, backup: bool = True) -> int:
        current = self.schema_version()
        if current > SCHEMA_VERSION:
            raise StoreError(
                f"nexus.db schema v{current} is newer than this build (v{SCHEMA_VERSION})"
            )
        if current == SCHEMA_VERSION:
            return current
        if backup and current > 0 and self.path != ":memory:":
            self.backup(f"{self.path}.bak-v{current}-{int(time.time())}")
        with self._lock:
            for version in range(current + 1, SCHEMA_VERSION + 1):
                with self._conn:
                    self._conn.executescript(_MIGRATIONS[version])
                    self._conn.execute(f"PRAGMA user_version={version}")
        return SCHEMA_VERSION

    def backup(self, destination: str) -> str:
        dest = Path(destination).expanduser()
        with self._lock:
            target = sqlite3.connect(str(dest))
            try:
                self._conn.backup(target)
            finally:
                target.close()
        os.chmod(dest, 0o600)
        return str(dest)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ----------------------------------------------------------------- devices

    def pair(self, name: str, grants: tuple[str, ...] = ()) -> tuple[DeviceRecord, str]:
        name = name.strip()
        if not DEVICE_NAME_RE.match(name):
            raise StoreError("device name must be 1-64 chars: letters, digits, space . _ -")
        for perm in grants:
            self._check_grantable(perm)
        device_id = "dev_" + secrets.token_hex(8)
        secret = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO devices(device_id, name, secret_hash, created_at) VALUES (?,?,?,?)",
                (device_id, name, hash_secret(secret), now),
            )
            for perm in grants:
                self._conn.execute(
                    "INSERT OR IGNORE INTO grants(device_id, permission, granted_at) VALUES (?,?,?)",
                    (device_id, perm, now),
                )
        record = self.device(device_id)
        assert record is not None
        return record, f"{device_id}.{secret}"

    def device(self, device_id: str) -> DeviceRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM devices WHERE device_id=?", (device_id,)
            ).fetchone()
        return DeviceRecord(**dict(row)) if row else None

    def devices(self) -> list[DeviceRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM devices ORDER BY created_at").fetchall()
        return [DeviceRecord(**dict(r)) for r in rows]

    def authenticate(self, device_id: str, secret: str) -> DeviceRecord | None:
        """Return the device when ``secret`` is valid and the device is not revoked."""

        record = self.device(device_id) if isinstance(device_id, str) else None
        candidate = hash_secret(secret or "")
        # Always compare, even for unknown devices, to keep timing uniform.
        expected = record.secret_hash if record else hash_secret(secrets.token_hex(16))
        ok = hmac.compare_digest(candidate, expected)
        if record is None or not ok or record.revoked:
            return None
        return record

    def session_valid(self, device_id: str, secret_hash: str) -> bool:
        record = self.device(device_id)
        return (
            record is not None
            and not record.revoked
            and hmac.compare_digest(record.secret_hash, secret_hash)
        )

    def touch(self, device_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE devices SET last_seen_at=? WHERE device_id=?", (time.time(), device_id)
            )

    def revoke(self, device_id: str) -> bool:
        now = time.time()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE devices SET revoked_at=? WHERE device_id=? AND revoked_at IS NULL",
                (now, device_id),
            )
            self._conn.execute("DELETE FROM grants WHERE device_id=?", (device_id,))
            self._conn.execute(
                "UPDATE approvals SET state='cancelled', decided_at=? "
                "WHERE device_id=? AND state IN ('pending','approved')",
                (now, device_id),
            )
        return cur.rowcount > 0

    def revoke_all(self) -> int:
        count = 0
        for record in self.devices():
            if not record.revoked and self.revoke(record.device_id):
                count += 1
        return count

    def rotate(self, device_id: str) -> str:
        record = self.device(device_id)
        if record is None:
            raise StoreError(f"unknown device {device_id}")
        if record.revoked:
            raise StoreError(f"device {device_id} is revoked; pair a new device instead")
        secret = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE devices SET secret_hash=?, rotated_at=? WHERE device_id=?",
                (hash_secret(secret), now, device_id),
            )
            self._conn.execute(
                "UPDATE approvals SET state='cancelled', decided_at=? "
                "WHERE device_id=? AND state IN ('pending','approved')",
                (now, device_id),
            )
        return f"{device_id}.{secret}"

    # ----------------------------------------------------------------- grants

    @staticmethod
    def _check_grantable(permission: str) -> None:
        if permission not in KNOWN_PERMISSIONS:
            raise StoreError(f"unknown permission {permission!r}")
        if permission not in GRANTABLE_PERMISSIONS:
            raise StoreError(f"{permission} is deferred in Nexus Remote 0.1 and cannot be granted")

    def grant(self, device_id: str, permission: str) -> None:
        self._check_grantable(permission)
        record = self.device(device_id)
        if record is None:
            raise StoreError(f"unknown device {device_id}")
        if record.revoked:
            raise StoreError(f"device {device_id} is revoked")
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO grants(device_id, permission, granted_at) VALUES (?,?,?)",
                (device_id, permission, time.time()),
            )

    def ungrant(self, device_id: str, permission: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM grants WHERE device_id=? AND permission=?", (device_id, permission)
            )
        return cur.rowcount > 0

    def permissions(self, device_id: str) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT g.permission FROM grants g JOIN devices d USING(device_id) "
                "WHERE g.device_id=? AND d.revoked_at IS NULL",
                (device_id,),
            ).fetchall()
        # Only exact, currently-known strings are honoured.
        return {r[0] for r in rows if r[0] in GRANTABLE_PERMISSIONS}

    # ----------------------------------------------------------------- approvals

    def create_approval(
        self,
        *,
        request_id: str,
        device_id: str,
        operation: str,
        params_hash: str,
        ttl: float = DEFAULT_APPROVAL_TTL,
    ) -> ApprovalRecord:
        now = time.time()
        approval_id = "apr_" + secrets.token_hex(8)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO approvals(approval_id, request_id, device_id, operation, params_hash,"
                " created_at, expires_at, state) VALUES (?,?,?,?,?,?,?, 'pending')",
                (approval_id, request_id, device_id, operation, params_hash, now, now + ttl),
            )
        record = self.approval(approval_id)
        assert record is not None
        return record

    def approval(self, approval_id: str) -> ApprovalRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM approvals WHERE approval_id=?", (approval_id,)
            ).fetchone()
        return ApprovalRecord(**dict(row)) if row else None

    def pending_approvals(self) -> list[ApprovalRecord]:
        self.expire_approvals()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM approvals WHERE state='pending' ORDER BY created_at"
            ).fetchall()
        return [ApprovalRecord(**dict(r)) for r in rows]

    def decide(self, approval_id: str, approve: bool) -> str:
        """Approve/deny a *pending, unexpired* approval.  Returns the resulting state."""

        now = time.time()
        new_state = "approved" if approve else "denied"
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE approvals SET state=?, decided_at=? "
                "WHERE approval_id=? AND state='pending' AND expires_at>?",
                (new_state, now, approval_id, now),
            )
        if cur.rowcount == 1:
            return new_state
        record = self.approval(approval_id)
        if record is None:
            raise StoreError(f"unknown approval {approval_id}")
        if record.state == "pending":
            self.expire_approvals()
            return "expired"
        return record.state

    def consume_approval(
        self,
        approval_id: str,
        *,
        request_id: str,
        device_id: str,
        operation: str,
        params_hash: str,
    ) -> bool:
        """Atomically mark an approved approval as used.  Single-use, binding-checked."""

        now = time.time()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE approvals SET state='consumed' WHERE approval_id=? AND state='approved'"
                " AND request_id=? AND device_id=? AND operation=? AND params_hash=?"
                " AND expires_at>?",
                (approval_id, request_id, device_id, operation, params_hash, now),
            )
        return cur.rowcount == 1

    def expire_approvals(self) -> int:
        now = time.time()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE approvals SET state='expired' WHERE state IN ('pending','approved')"
                " AND expires_at<=?",
                (now,),
            )
        return cur.rowcount

    def cancel_approval(self, approval_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE approvals SET state='cancelled', decided_at=? "
                "WHERE approval_id=? AND state IN ('pending','approved')",
                (time.time(), approval_id),
            )

    # ----------------------------------------------------------------- audit

    def record_audit(
        self,
        *,
        request_id: str,
        device_id: str,
        operation: str,
        decision: str,
        status: str,
        params_hash: str = "",
        reason: str = "",
        approval_state: str = "not_required",
        error_code: str = "",
        truncated: bool = False,
        started_at: float | None = None,
        finished_at: float | None = None,
    ) -> None:
        finished = finished_at or time.time()
        duration = int((finished - started_at) * 1000) if started_at else None
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO audit(request_id, device_id, operation, params_hash, decision, reason,"
                " approval_state, status, error_code, truncated, started_at, finished_at,"
                " duration_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    request_id[:64],
                    device_id[:32],
                    operation[:64],
                    params_hash,
                    decision,
                    reason[:64],
                    approval_state,
                    status,
                    error_code[:64],
                    int(truncated),
                    started_at,
                    finished,
                    duration,
                ),
            )

    def audit(self, *, limit: int = 50, device_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM audit"
        args: list[Any] = []
        if device_id:
            query += " WHERE device_id=?"
            args.append(device_id)
        query += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, min(int(limit), 10_000)))
        with self._lock:
            rows = self._conn.execute(query, args).fetchall()
        return [dict(r) for r in rows]

    def prune_audit(self, retention_days: int) -> int:
        if retention_days <= 0:
            return 0
        cutoff = time.time() - retention_days * 86400
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM audit WHERE finished_at<?", (cutoff,))
        return cur.rowcount
