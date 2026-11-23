"""Versioned wire schemas for Nexus Remote (protocol ``v`` = 1).

Remote frames are parsed far more strictly than the legacy PhoneBridge frames:
unknown versions, unknown fields, oversized payloads, malformed identifiers and
non-object params are all rejected.  Nothing in this module executes anything.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

REMOTE_VERSION = 1
MAX_REMOTE_FRAME_BYTES = 16 * 1024
MAX_PARAMS_BYTES = 4 * 1024
MAX_PARAMS_DEPTH = 3
MAX_ERROR_MESSAGE = 256

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
OPERATION_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}(\.[a-z][a-z0-9_]{0,31}){1,3}$")
DEVICE_ID_RE = re.compile(r"^dev_[0-9a-f]{16}$")
APPROVAL_ID_RE = re.compile(r"^apr_[0-9a-f]{16}$")


class RemoteType(str, Enum):
    CAPABILITIES = "remote.capabilities"
    REQUEST = "remote.request"
    RESPONSE = "remote.response"
    EVENT = "remote.event"
    APPROVAL_REQUIRED = "remote.approval_required"
    APPROVAL_RESULT = "remote.approval_result"


INBOUND_REMOTE_TYPES = frozenset({RemoteType.REQUEST.value})
ALL_REMOTE_TYPES = frozenset(t.value for t in RemoteType)


class Status(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class EventState(str, Enum):
    ACCEPTED = "accepted"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"


class ApprovalOutcome(str, Enum):
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class RemoteProtocolError(ValueError):
    """Raised for an invalid remote frame.  ``request_id`` is set when known."""

    def __init__(self, message: str, request_id: str = "", code: str = "invalid_request") -> None:
        super().__init__(message)
        self.request_id = request_id
        self.code = code


def is_remote_type(raw_type: Any) -> bool:
    return isinstance(raw_type, str) and raw_type.startswith("remote.")


def _depth(value: Any, level: int = 0) -> int:
    if isinstance(value, dict):
        return max([level + 1] + [_depth(v, level + 1) for v in value.values()])
    if isinstance(value, list):
        return max([level + 1] + [_depth(v, level + 1) for v in value])
    return level


def _finite(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite(v) for v in value.values())
    if isinstance(value, list):
        return all(_finite(v) for v in value)
    return True


def canonical_params(params: dict[str, Any]) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def params_hash(params: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_params(params).encode("ascii")).hexdigest()


@dataclass(frozen=True)
class RemoteRequest:
    request_id: str
    operation: str
    params: dict[str, Any]
    ts: float
    device_id: str = ""

    _FIELDS = frozenset({"type", "v", "request_id", "operation", "params", "ts", "device_id"})

    @classmethod
    def parse(cls, data: dict[str, Any], raw_size: int = 0) -> "RemoteRequest":
        rid = data.get("request_id")
        safe_rid = rid if isinstance(rid, str) and REQUEST_ID_RE.match(rid) else ""
        if raw_size > MAX_REMOTE_FRAME_BYTES:
            raise RemoteProtocolError("frame too large", safe_rid, "payload_too_large")
        if data.get("type") != RemoteType.REQUEST.value:
            raise RemoteProtocolError("not a remote.request", safe_rid)
        version = data.get("v")
        if isinstance(version, bool) or not isinstance(version, int):
            raise RemoteProtocolError("missing or invalid version 'v'", safe_rid)
        if version != REMOTE_VERSION:
            raise RemoteProtocolError(
                f"unsupported remote protocol version {version}", safe_rid, "unsupported_version"
            )
        unknown = set(data) - cls._FIELDS
        if unknown:
            raise RemoteProtocolError(f"unknown fields: {', '.join(sorted(unknown))}", safe_rid)
        if not safe_rid:
            raise RemoteProtocolError("missing or invalid request_id")
        op = data.get("operation")
        if not isinstance(op, str) or not OPERATION_RE.match(op):
            raise RemoteProtocolError("missing or invalid operation", safe_rid)
        params = data.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise RemoteProtocolError("params must be an object", safe_rid)
        if _depth(params) > MAX_PARAMS_DEPTH:
            raise RemoteProtocolError("params nested too deeply", safe_rid)
        if not _finite(params):
            raise RemoteProtocolError("params must not contain NaN/Infinity", safe_rid)
        if len(canonical_params(params)) > MAX_PARAMS_BYTES:
            raise RemoteProtocolError("params too large", safe_rid, "payload_too_large")
        ts = data.get("ts")
        if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(ts) or ts <= 0:
            raise RemoteProtocolError("missing or invalid ts", safe_rid)
        device_id = data.get("device_id", "")
        if device_id not in ("", None) and (
            not isinstance(device_id, str) or not DEVICE_ID_RE.match(device_id)
        ):
            raise RemoteProtocolError("invalid device_id", safe_rid)
        return cls(
            request_id=safe_rid,
            operation=op,
            params=params,
            ts=float(ts),
            device_id=device_id or "",
        )

    def to_dict(self) -> dict[str, Any]:
        frame: dict[str, Any] = {
            "type": RemoteType.REQUEST.value,
            "v": REMOTE_VERSION,
            "request_id": self.request_id,
            "operation": self.operation,
            "params": self.params,
            "ts": self.ts,
        }
        if self.device_id:
            frame["device_id"] = self.device_id
        return frame


@dataclass
class RemoteResponse:
    request_id: str
    status: Status
    operation: str = ""
    result: Any = None
    error_code: str = ""
    error_message: str = ""
    truncated: bool = False
    started_at: float | None = None
    finished_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        frame: dict[str, Any] = {
            "type": RemoteType.RESPONSE.value,
            "v": REMOTE_VERSION,
            "request_id": self.request_id,
            "operation": self.operation,
            "status": self.status.value,
            "truncated": self.truncated,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
        if self.status is Status.COMPLETED:
            frame["result"] = self.result
        else:
            frame["error"] = {
                "code": self.error_code or "error",
                "message": (self.error_message or self.error_code)[:MAX_ERROR_MESSAGE],
            }
        return frame


def rejected(request_id: str, code: str, message: str, operation: str = "") -> RemoteResponse:
    return RemoteResponse(
        request_id=request_id,
        status=Status.REJECTED,
        operation=operation,
        error_code=code,
        error_message=message,
    )


def event(request_id: str, state: EventState, **extra: Any) -> dict[str, Any]:
    frame = {
        "type": RemoteType.EVENT.value,
        "v": REMOTE_VERSION,
        "request_id": request_id,
        "state": state.value,
        "ts": time.time(),
    }
    frame.update(extra)
    return frame


def approval_required(request_id: str, approval_id: str, operation: str, expires_at: float) -> dict[str, Any]:
    return {
        "type": RemoteType.APPROVAL_REQUIRED.value,
        "v": REMOTE_VERSION,
        "request_id": request_id,
        "approval_id": approval_id,
        "operation": operation,
        "expires_at": expires_at,
        "approve_on": "linux",
    }


def approval_result(request_id: str, approval_id: str, outcome: ApprovalOutcome) -> dict[str, Any]:
    return {
        "type": RemoteType.APPROVAL_RESULT.value,
        "v": REMOTE_VERSION,
        "request_id": request_id,
        "approval_id": approval_id,
        "outcome": outcome.value,
    }


def capabilities(
    *,
    available: bool,
    device_id: str = "",
    permissions: list[str] | None = None,
    operations: list[dict[str, Any]] | None = None,
    reason: str = "",
    server: str = "",
) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "type": RemoteType.CAPABILITIES.value,
        "v": REMOTE_VERSION,
        "available": available,
        "server": server,
        "device_id": device_id,
        "permissions": sorted(permissions or []),
        "operations": operations or [],
    }
    if reason:
        frame["reason"] = reason
    return frame
