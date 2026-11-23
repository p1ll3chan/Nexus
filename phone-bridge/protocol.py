"""Wire protocol shared between the phone and the laptop.

Every frame is a JSON object with at least a ``type`` field.  The helpers in
this module parse untrusted input into typed dataclasses and serialise frames
back to JSON.
"""

from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

PROTOCOL_VERSION = 1
MAX_APP_LEN = 128
MAX_TITLE_LEN = 512
MAX_TEXT_LEN = 4096
MAX_DEVICE_LEN = 128
MAX_ID_LEN = 128
MAX_TOKEN_LEN = 512
MAX_PROVIDER_LEN = 32


class ProtocolError(ValueError):
    """Raised when a frame violates the protocol."""


class MessageType(str, Enum):
    HELLO = "hello"
    HELLO_ACK = "hello_ack"
    NOTIFICATION = "notification"
    CALL = "call"
    LOCATION = "location"
    LOCATE = "locate"
    PING = "ping"
    PONG = "pong"
    ACK = "ack"
    ERROR = "error"


class CallState(str, Enum):
    RINGING = "ringing"
    OFFHOOK = "offhook"
    IDLE = "idle"

    @classmethod
    def from_android(cls, raw: str | int | None) -> "CallState":
        if raw is None:
            return cls.IDLE
        if isinstance(raw, int):
            return {1: cls.RINGING, 2: cls.OFFHOOK, 0: cls.IDLE}.get(raw, cls.IDLE)
        value = str(raw).strip().lower()
        mapping = {
            "ringing": cls.RINGING,
            "incoming": cls.RINGING,
            "offhook": cls.OFFHOOK,
            "answered": cls.OFFHOOK,
            "active": cls.OFFHOOK,
            "idle": cls.IDLE,
            "ended": cls.IDLE,
            "disconnected": cls.IDLE,
            "missed": cls.IDLE,
        }
        if value not in mapping:
            raise ProtocolError(f"unknown call state: {raw!r}")
        return mapping[value]


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return uuid.uuid4().hex


def _require_str(data: dict[str, Any], key: str, max_len: int, *, required: bool = True) -> str:
    value = data.get(key, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ProtocolError(f"field {key!r} must be a string")
    value = value.strip()
    if required and not value:
        raise ProtocolError(f"field {key!r} is required")
    if len(value) > max_len:
        value = value[:max_len]
    return value


def _optional_bool(data: dict[str, Any], key: str, default: bool = False) -> bool:
    value = data.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _optional_int(data: dict[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _require_float(
    data: dict[str, Any],
    keys: tuple[str, ...],
    *,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float:
    raw: Any = None
    found_key = keys[0]
    for key in keys:
        if key in data and data[key] is not None:
            raw = data[key]
            found_key = key
            break
    if raw is None or isinstance(raw, bool):
        raise ProtocolError(f"field {keys[0]!r} is required")
    try:
        num = float(raw)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"field {found_key!r} must be a number") from exc
    if math.isnan(num) or math.isinf(num):
        raise ProtocolError(f"field {found_key!r} must be finite")
    if min_value is not None and num < min_value:
        raise ProtocolError(f"field {found_key!r} out of range: {num}")
    if max_value is not None and num > max_value:
        raise ProtocolError(f"field {found_key!r} out of range: {num}")
    return num


def _optional_float(
    data: dict[str, Any],
    key: str,
    default: float | None = None,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float | None:
    raw = data.get(key)
    if raw is None or raw == "" or isinstance(raw, bool):
        return default
    try:
        num = float(raw)
    except (TypeError, ValueError):
        return default
    if math.isnan(num) or math.isinf(num):
        return default
    if min_value is not None and num < min_value:
        return min_value
    if max_value is not None and num > max_value:
        return max_value
    return num


def _optional_battery(data: dict[str, Any]) -> int | None:
    raw = data.get("battery")
    if raw is None:
        raw = data.get("battery_level")
    if raw is None or raw == "" or isinstance(raw, bool):
        return None
    try:
        val = int(round(float(raw)))
    except (TypeError, ValueError):
        return None
    if val < 0:
        return None
    return min(100, val)


def dumps(frame: dict[str, Any]) -> str:
    return json.dumps(frame, ensure_ascii=False, separators=(",", ":"))


def loads(raw: str | bytes) -> dict[str, Any]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ProtocolError("frame must be a JSON object")
    if "type" not in data:
        raise ProtocolError("frame is missing 'type'")
    if not isinstance(data["type"], str):
        raise ProtocolError("'type' must be a string")
    return data


def message_type(data: dict[str, Any]) -> MessageType:
    raw = str(data.get("type", "")).strip().lower()
    try:
        return MessageType(raw)
    except ValueError as exc:
        raise ProtocolError(f"unknown message type: {raw!r}") from exc


@dataclass
class Hello:
    device: str
    token: str = ""
    version: int = PROTOCOL_VERSION
    platform: str = "android"
    # Nexus per-device credential (preferred).  When present the shared token
    # is not used and a failed device credential never falls back to it.
    device_id: str = ""
    device_secret: str = ""

    @property
    def uses_device_credential(self) -> bool:
        return bool(self.device_id)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Hello":
        device_id = _require_str(data, "device_id", MAX_ID_LEN, required=False)
        device_secret = _require_str(data, "device_secret", MAX_TOKEN_LEN, required=False)
        if device_id and not device_secret:
            raise ProtocolError("field 'device_secret' is required with 'device_id'")
        return cls(
            device=_require_str(data, "device", MAX_DEVICE_LEN),
            token=_require_str(data, "token", MAX_TOKEN_LEN, required=not device_id),
            version=_optional_int(data, "version", PROTOCOL_VERSION),
            platform=_require_str(data, "platform", 32, required=False) or "android",
            device_id=device_id,
            device_secret=device_secret,
        )

    def to_dict(self) -> dict[str, Any]:
        frame: dict[str, Any] = {
            "type": MessageType.HELLO.value,
            "device": self.device,
            "token": self.token,
            "version": self.version,
            "platform": self.platform,
        }
        if self.device_id:
            frame["device_id"] = self.device_id
            frame["device_secret"] = self.device_secret
        return frame


@dataclass
class Notification:
    app: str
    title: str
    text: str
    id: str = field(default_factory=_new_id)
    package: str = ""
    ongoing: bool = False
    low_priority: bool = False
    timestamp: float = field(default_factory=_now)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Notification":
        app = _require_str(data, "app", MAX_APP_LEN) or "Phone"
        package = _require_str(data, "package", MAX_APP_LEN, required=False)
        return cls(
            app=app,
            title=_require_str(data, "title", MAX_TITLE_LEN, required=False),
            text=_require_str(data, "text", MAX_TEXT_LEN, required=False),
            id=_require_str(data, "id", MAX_ID_LEN, required=False) or _new_id(),
            package=package,
            ongoing=_optional_bool(data, "ongoing"),
            low_priority=_optional_bool(data, "low_priority"),
            timestamp=float(data.get("timestamp") or _now()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": MessageType.NOTIFICATION.value,
            "id": self.id,
            "app": self.app,
            "package": self.package,
            "title": self.title,
            "text": self.text,
            "ongoing": self.ongoing,
            "low_priority": self.low_priority,
            "timestamp": self.timestamp,
        }


@dataclass
class Call:
    state: CallState
    name: str = ""
    number: str = ""
    id: str = field(default_factory=_new_id)
    timestamp: float = field(default_factory=_now)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Call":
        raw_state = data.get("state", data.get("event"))
        return cls(
            state=CallState.from_android(raw_state),
            name=_require_str(data, "name", MAX_TITLE_LEN, required=False),
            number=_require_str(data, "number", MAX_TITLE_LEN, required=False),
            id=_require_str(data, "id", MAX_ID_LEN, required=False) or _new_id(),
            timestamp=float(data.get("timestamp") or _now()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": MessageType.CALL.value,
            "id": self.id,
            "state": self.state.value,
            "name": self.name,
            "number": self.number,
            "timestamp": self.timestamp,
        }


@dataclass
class Location:
    latitude: float
    longitude: float
    accuracy: float = 0.0
    battery: int | None = None
    charging: bool = False
    altitude: float | None = None
    speed: float | None = None
    bearing: float | None = None
    provider: str = "gps"
    id: str = field(default_factory=_new_id)
    timestamp: float = field(default_factory=_now)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Location":
        latitude = _require_float(
            data,
            ("latitude", "lat"),
            min_value=-90.0,
            max_value=90.0,
        )
        longitude = _require_float(
            data,
            ("longitude", "lon", "lng"),
            min_value=-180.0,
            max_value=180.0,
        )
        accuracy = _optional_float(data, "accuracy", 0.0, min_value=0.0) or 0.0
        altitude = _optional_float(data, "altitude", None)
        speed = _optional_float(data, "speed", None, min_value=0.0)
        bearing = _optional_float(data, "bearing", None, min_value=0.0, max_value=360.0)
        provider = _require_str(data, "provider", MAX_PROVIDER_LEN, required=False) or "gps"
        raw_ts = _optional_float(data, "timestamp", None, min_value=0.0)
        timestamp = raw_ts if raw_ts and raw_ts > 0 else _now()
        return cls(
            latitude=latitude,
            longitude=longitude,
            accuracy=accuracy,
            battery=_optional_battery(data),
            charging=_optional_bool(data, "charging"),
            altitude=altitude,
            speed=speed,
            bearing=bearing,
            provider=provider,
            id=_require_str(data, "id", MAX_ID_LEN, required=False) or _new_id(),
            timestamp=timestamp,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": MessageType.LOCATION.value,
            "id": self.id,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "accuracy": self.accuracy,
            "battery": self.battery,
            "charging": self.charging,
            "provider": self.provider,
            "timestamp": self.timestamp,
        }
        if self.altitude is not None:
            payload["altitude"] = self.altitude
        if self.speed is not None:
            payload["speed"] = self.speed
        if self.bearing is not None:
            payload["bearing"] = self.bearing
        return payload


@dataclass
class Locate:
    id: str = field(default_factory=_new_id)
    timestamp: float = field(default_factory=_now)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Locate":
        return cls(
            id=_require_str(data, "id", MAX_ID_LEN, required=False) or _new_id(),
            timestamp=float(data.get("timestamp") or _now()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": MessageType.LOCATE.value,
            "id": self.id,
            "timestamp": self.timestamp,
        }


@dataclass
class Ping:
    timestamp: float = field(default_factory=_now)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Ping":
        return cls(timestamp=float(data.get("timestamp") or _now()))

    def to_dict(self) -> dict[str, Any]:
        return {"type": MessageType.PING.value, "timestamp": self.timestamp}


@dataclass
class Pong:
    timestamp: float = field(default_factory=_now)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Pong":
        return cls(timestamp=float(data.get("timestamp") or _now()))

    def to_dict(self) -> dict[str, Any]:
        return {"type": MessageType.PONG.value, "timestamp": self.timestamp}


def hello_ack(
    server: str,
    version: int = PROTOCOL_VERSION,
    *,
    auth: str = "token",
    device_id: str = "",
) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "type": MessageType.HELLO_ACK.value,
        "server": server,
        "version": version,
        "auth": auth,
        "timestamp": _now(),
    }
    if device_id:
        frame["device_id"] = device_id
    return frame


def ack(ref: str = "", ok: bool = True, detail: str = "") -> dict[str, Any]:
    return {
        "type": MessageType.ACK.value,
        "ref": ref,
        "ok": ok,
        "detail": detail,
        "timestamp": _now(),
    }


def locate_request(request_id: str = "") -> dict[str, Any]:
    return Locate(id=request_id or _new_id()).to_dict()


def error(code: str, message: str) -> dict[str, Any]:
    return {
        "type": MessageType.ERROR.value,
        "code": code,
        "message": message,
        "timestamp": _now(),
    }


def parse_event(
    data: dict[str, Any],
) -> Notification | Call | Location | Locate | Ping | Pong | Hello:
    kind = message_type(data)
    if kind is MessageType.HELLO:
        return Hello.from_dict(data)
    if kind is MessageType.NOTIFICATION:
        return Notification.from_dict(data)
    if kind is MessageType.CALL:
        return Call.from_dict(data)
    if kind is MessageType.LOCATION:
        return Location.from_dict(data)
    if kind is MessageType.LOCATE:
        return Locate.from_dict(data)
    if kind is MessageType.PING:
        return Ping.from_dict(data)
    if kind is MessageType.PONG:
        return Pong.from_dict(data)
    raise ProtocolError(f"unsupported inbound message type: {kind.value}")
