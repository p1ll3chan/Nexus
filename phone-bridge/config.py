"""Configuration loading for phone-bridge.

Configuration is resolved in the following order (later wins):
    1. Built-in defaults
    2. A TOML or JSON config file (``--config`` / ``PHONE_BRIDGE_CONFIG``)
    3. Environment variables prefixed with ``PHONE_BRIDGE_``
    4. Explicit CLI overrides passed to :func:`load_config`
"""

from __future__ import annotations

import datetime as dt
import json
import os
import secrets
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATHS = (
    Path("config.toml"),
    Path.home() / ".config" / "phone-bridge" / "config.toml",
)

DEFAULT_DB_PATH = str(Path.home() / ".local" / "share" / "phone-bridge" / "locations.db")
DEFAULT_REMOTE_DB_PATH = str(Path.home() / ".local" / "share" / "phone-bridge" / "nexus.db")

# Shipped placeholder tokens that must never authenticate (Day 3/4 finding G2).
PLACEHOLDER_TOKENS = frozenset({"change-me", "changeme", "change_me", "your-token-here", "<token>"})

ENV_PREFIX = "PHONE_BRIDGE_"

DEFAULT_ALLOWED_APPS: tuple[str, ...] = (
    # Messaging & communication
    "whatsapp",
    "com.whatsapp",
    "com.whatsapp.w4b",
    "telegram",
    "org.telegram.messenger",
    "org.thunderdog.challegram",
    "nekogram",
    "signal",
    "org.thoughtcrime.securesms",
    "discord",
    "com.discord",
    "slack",
    "com.slack",
    "messages",
    "google messages",
    "sms",
    "mms",
    "com.google.android.apps.messaging",
    "com.android.mms",
    "com.samsung.android.messaging",
    "messenger",
    "com.facebook.orca",
    "com.facebook.mlite",
    "teams",
    "microsoft teams",
    "com.microsoft.teams",
    "google chat",
    "google meet",
    "meet",
    "com.google.android.apps.dynamite",
    "com.google.android.apps.meetings",
    "zoom",
    "us.zoom.videomeetings",
    "skype",
    "com.skype.raider",
    "viber",
    "com.viber.voip",
    "line",
    "jp.naver.line.android",
    "wechat",
    "com.tencent.mm",
    "element",
    "matrix",
    "im.vector.app",
    "threema",
    "ch.threema.app",
    "session",
    "network.loki.messenger",
    "briar",
    "org.briarproject.briar.android",
    "mattermost",
    "com.mattermost.rn",
    "zulip",
    "com.zulipmobile",
    # Email
    "gmail",
    "com.google.android.gm",
    "outlook",
    "com.microsoft.office.outlook",
    "proton mail",
    "protonmail",
    "ch.protonmail.android",
    "thunderbird",
    "k-9 mail",
    "k9",
    "net.thunderbird.android",
    "com.fsck.k9",
    "tuta",
    "tutanota",
    "de.tutao.tutanota",
    "yahoo mail",
    "com.yahoo.mobile.client.android.mail",
    "spark",
    "com.readdle.spark",
    "fairemail",
    "eu.faircode.email",
    "bluemail",
    "me.bluemail.mail",
    "fastmail",
    "com.fastmail.app",
    "email",
    "mail",
    "com.samsung.android.email.provider",
    "com.android.email",
    # Social media
    "instagram",
    "com.instagram.android",
    "threads",
    "com.instagram.barcelona",
    "twitter",
    "x",
    "com.twitter.android",
    "reddit",
    "com.reddit.frontpage",
    "linkedin",
    "com.linkedin.android",
    "facebook",
    "com.facebook.katana",
    "com.facebook.lite",
    "snapchat",
    "com.snapchat.android",
    "mastodon",
    "org.joinmastodon.android",
    "bluesky",
    "xyz.blueskyweb.app",
    # Phone / Calls / Bridge
    "phone",
    "dialer",
    "calls",
    "com.google.android.dialer",
    "com.android.dialer",
    "com.samsung.android.incallui",
    "phonebridge",
    "com.phonebridge.app",
)


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    host: str = "0.0.0.0"
    port: int = 8765
    token: str = ""
    device_name: str = "arch-laptop"
    phone_name: str = ""
    tailscale_command: str = "tailscale"
    discovery_interval: float = 30.0

    notify_backend: str = "auto"
    notification_timeout: int = 8000
    notification_urgency: str = "normal"
    app_icon: str = "phone"
    default_icon: str = "phone"
    icon_map: dict[str, str] = field(
        default_factory=lambda: {
            "whatsapp": "whatsapp",
            "telegram": "telegram",
            "signal": "signal-desktop",
            "discord": "discord",
            "messages": "org.gnome.Messages",
            "messenger": "facebook-messenger",
            "phone": "org.gnome.Calls",
            "gmail": "org.gnome.Geary",
            "outlook": "org.gnome.Geary",
            "proton mail": "org.gnome.Geary",
            "thunderbird": "thunderbird",
            "slack": "slack",
            "teams": "teams-for-linux",
            "instagram": "instagram",
            "threads": "instagram",
            "twitter": "twitter",
            "reddit": "reddit",
            "linkedin": "linkedin",
            "facebook": "facebook",
            "snapchat": "snapchat",
        }
    )

    allowed_apps: tuple[str, ...] = DEFAULT_ALLOWED_APPS
    only_allowed_apps: bool = True
    ignore_apps: tuple[str, ...] = ()
    ignore_ongoing: bool = True
    ignore_low_priority: bool = True
    allow_calls: bool = True
    allow_notifications: bool = True
    allow_location: bool = True
    enabled_devices: tuple[str, ...] = ()

    db_path: str = DEFAULT_DB_PATH
    location_retention_days: int = 30
    map_enabled: bool = True
    map_host: str = "127.0.0.1"
    map_port: int = 8766

    heartbeat_interval: float = 25.0
    heartbeat_timeout: float = 70.0
    max_message_bytes: int = 262_144
    log_level: str = "INFO"

    # Nexus Remote
    remote_enabled: bool = True
    remote_db_path: str = DEFAULT_REMOTE_DB_PATH
    remote_disks: dict[str, str] = field(default_factory=lambda: {"root": "/", "home": "~"})
    remote_session_max_age: float = 43_200.0
    remote_approval_ttl: float = 60.0
    audit_retention_days: int = 90
    # Legacy shared-token auth keeps notifications/calls/GPS working for unpaired
    # phones.  It can never be used for remote.* operations.  Set an ISO date
    # (YYYY-MM-DD) in legacy_token_expires to make it time-limited.
    legacy_token_auth: bool = True
    legacy_token_expires: str = ""

    @property
    def uri(self) -> str:
        return f"ws://{self.host}:{self.port}"

    @property
    def map_url(self) -> str:
        host = "127.0.0.1" if self.map_host in {"0.0.0.0", "::", ""} else self.map_host
        return f"http://{host}:{self.map_port}"

    def to_public_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        if data.get("token"):
            data["token"] = "****"
        return data

    def legacy_token_allowed(self, today: "dt.date | None" = None) -> bool:
        if not self.legacy_token_auth:
            return False
        if not self.legacy_token_expires:
            return True
        today = today or dt.date.today()
        return today <= dt.date.fromisoformat(self.legacy_token_expires)

    def is_app_ignored(self, app: str, package: str = "") -> bool:
        lowered = {a.strip().lower() for a in self.ignore_apps if a.strip()}
        return app.strip().lower() in lowered or (
            bool(package) and package.strip().lower() in lowered
        )

    def is_device_allowed(self, device: str) -> bool:
        if not self.enabled_devices:
            return True
        allowed = {d.strip().lower() for d in self.enabled_devices if d.strip()}
        return device.strip().lower() in allowed


_TOML_KEYS = {
    "host",
    "port",
    "token",
    "device_name",
    "phone_name",
    "tailscale_command",
    "discovery_interval",
    "notify_backend",
    "notification_timeout",
    "notification_urgency",
    "app_icon",
    "default_icon",
    "icon_map",
    "allowed_apps",
    "only_allowed_apps",
    "ignore_apps",
    "ignore_ongoing",
    "ignore_low_priority",
    "allow_calls",
    "allow_notifications",
    "allow_location",
    "enabled_devices",
    "db_path",
    "location_retention_days",
    "map_enabled",
    "map_host",
    "map_port",
    "heartbeat_interval",
    "heartbeat_timeout",
    "max_message_bytes",
    "log_level",
    "remote_enabled",
    "remote_db_path",
    "remote_disks",
    "remote_session_max_age",
    "remote_approval_ttl",
    "audit_retention_days",
    "legacy_token_auth",
    "legacy_token_expires",
}


def _read_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    raw = path.read_bytes()
    if path.suffix.lower() == ".json":
        data = json.loads(raw.decode("utf-8"))
    else:
        data = tomllib.loads(raw.decode("utf-8"))
    flat: dict[str, Any] = {}
    for key, value in data.items():
        if key in _TOML_KEYS:
            flat[key] = value
        elif isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if sub_key in _TOML_KEYS:
                    flat[sub_key] = sub_value
    return flat


def _coerce(name: str, value: Any) -> Any:
    if name in {
        "port",
        "notification_timeout",
        "max_message_bytes",
        "location_retention_days",
        "map_port",
        "audit_retention_days",
    }:
        return int(value)
    if name in {
        "discovery_interval",
        "heartbeat_interval",
        "heartbeat_timeout",
        "remote_session_max_age",
        "remote_approval_ttl",
    }:
        return float(value)
    if name in {
        "only_allowed_apps",
        "ignore_ongoing",
        "ignore_low_priority",
        "allow_calls",
        "allow_notifications",
        "allow_location",
        "map_enabled",
        "remote_enabled",
        "legacy_token_auth",
    }:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if name in {"allowed_apps", "ignore_apps", "enabled_devices", "allowed_devices"}:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return tuple(value)
    if name in {"icon_map", "remote_disks"}:
        if isinstance(value, str):
            value = dict(
                part.split("=", 1) for part in value.split(",") if "=" in part
            )
        return {str(k).strip(): str(v).strip() for k, v in dict(value).items()}
    return value


def _from_env(env: dict[str, str]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for name in _TOML_KEYS:
        key = ENV_PREFIX + name.upper()
        if key in env:
            data[name] = _coerce(name, env[key])
    if "allowed_devices" in data:
        data["enabled_devices"] = data.pop("allowed_devices")
    return data


def load_config(
    path: str | os.PathLike[str] | None = None,
    overrides: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> Config:
    """Load and validate the effective configuration."""

    env = dict(os.environ if env is None else env)
    data: dict[str, Any] = {}

    chosen: Path | None = None
    if path is not None:
        chosen = Path(path).expanduser()
    elif env.get(ENV_PREFIX + "CONFIG"):
        chosen = Path(env[ENV_PREFIX + "CONFIG"]).expanduser()
    else:
        for candidate in DEFAULT_CONFIG_PATHS:
            if candidate.exists():
                chosen = candidate
                break

    if chosen is not None:
        data.update(_read_config_file(chosen))

    data.update(_from_env(env))

    if overrides:
        for key, value in overrides.items():
            if value is not None:
                data[key] = value

    if "allowed_devices" in data:
        data["enabled_devices"] = data.pop("allowed_devices")

    for list_key in ("allowed_apps", "ignore_apps", "enabled_devices"):
        if list_key in data and isinstance(data[list_key], list):
            data[list_key] = tuple(str(x).strip() for x in data[list_key] if str(x).strip())

    unknown = set(data) - _TOML_KEYS
    if unknown:
        raise ConfigError(f"unknown configuration keys: {', '.join(sorted(unknown))}")

    for dict_key in ("icon_map", "remote_disks"):
        if dict_key in data:
            data[dict_key] = _coerce(dict_key, data[dict_key])

    config = Config(**data)

    if config.token.strip().lower() in PLACEHOLDER_TOKENS:
        raise ConfigError(
            "token is still the shipped placeholder; generate one with `phone-bridge token` "
            "or pair devices with `phone-bridge remote pair`"
        )

    if not config.token:
        config = replace(config, token=generate_token())

    if not 1 <= config.port <= 65535:
        raise ConfigError(f"port out of range: {config.port}")

    if not 1 <= config.map_port <= 65535:
        raise ConfigError(f"map_port out of range: {config.map_port}")

    if config.notify_backend not in {"auto", "dbus", "notify-send", "console", "none"}:
        raise ConfigError(f"invalid notify_backend: {config.notify_backend}")

    if config.notification_urgency not in {"low", "normal", "critical"}:
        raise ConfigError(f"invalid notification_urgency: {config.notification_urgency}")

    if config.legacy_token_expires:
        try:
            dt.date.fromisoformat(config.legacy_token_expires)
        except ValueError as exc:
            raise ConfigError("legacy_token_expires must be YYYY-MM-DD") from exc

    for label in config.remote_disks:
        if not label.replace("_", "").replace("-", "").isalnum() or len(label) > 32:
            raise ConfigError(f"invalid remote_disks label: {label!r}")

    if not 10.0 <= config.remote_approval_ttl <= 600.0:
        raise ConfigError("remote_approval_ttl must be between 10 and 600 seconds")

    return config


def generate_token(length: int = 32) -> str:
    """Generate a URL-safe random shared secret."""

    return secrets.token_urlsafe(length)


def write_default_config(path: str | os.PathLike[str], config: Config | None = None) -> Path:
    """Write a starter configuration file and return its path."""

    config = config or Config(token=generate_token())
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# phone-bridge configuration",
        f'host = "{config.host}"',
        f"port = {config.port}",
        f'token = "{config.token}"',
        f'device_name = "{config.device_name}"',
        f'phone_name = "{config.phone_name}"',
        f'notify_backend = "{config.notify_backend}"',
        f"notification_timeout = {config.notification_timeout}",
        f'notification_urgency = "{config.notification_urgency}"',
        f'default_icon = "{config.default_icon}"',
        "# Only forward notifications from social media, messaging, and email apps.",
        f"only_allowed_apps = {str(config.only_allowed_apps).lower()}",
        f"allowed_apps = {json.dumps(list(config.allowed_apps))}",
        f"ignore_apps = {json.dumps(list(config.ignore_apps))}",
        f"ignore_ongoing = {str(config.ignore_ongoing).lower()}",
        f"ignore_low_priority = {str(config.ignore_low_priority).lower()}",
        f"allow_calls = {str(config.allow_calls).lower()}",
        f"allow_notifications = {str(config.allow_notifications).lower()}",
        f"allow_location = {str(config.allow_location).lower()}",
        f"enabled_devices = {json.dumps(list(config.enabled_devices))}",
        f'db_path = "{config.db_path}"',
        f"location_retention_days = {config.location_retention_days}",
        f"map_enabled = {str(config.map_enabled).lower()}",
        f'map_host = "{config.map_host}"',
        f"map_port = {config.map_port}",
        f"heartbeat_interval = {config.heartbeat_interval}",
        f"heartbeat_timeout = {config.heartbeat_timeout}",
        f'log_level = "{config.log_level}"',
        "",
        "# Nexus Remote (read-only Linux diagnostics for paired devices)",
        f"remote_enabled = {str(config.remote_enabled).lower()}",
        f'remote_db_path = "{config.remote_db_path}"',
        f"legacy_token_auth = {str(config.legacy_token_auth).lower()}",
        f'legacy_token_expires = "{config.legacy_token_expires}"',
        f"audit_retention_days = {config.audit_retention_days}",
        "",
        "[remote_disks]",
        *[f'{k} = "{v}"' for k, v in config.remote_disks.items()],
        "",
    ]
    fd = os.open(target, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    os.chmod(target, 0o600)
    return target
