"""Desktop notification delivery.

Three backends are supported:

``dbus``
    Native ``org.freedesktop.Notifications`` D-Bus calls.  This is the
    preferred backend on a normal desktop session.
``notify-send``
    Shells out to libnotify's ``notify-send`` helper.
``console``
    Prints a rendered notification to stdout.  Useful for headless systems,
    tests and debugging.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from abc import ABC, abstractmethod
from typing import Iterable

from config import DEFAULT_ALLOWED_APPS

log = logging.getLogger("phone-bridge.notifications")

URGENCY_LEVELS = {"low": 0, "normal": 1, "critical": 2}
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def is_app_allowed(
    app: str,
    package: str = "",
    allowed_apps: Iterable[str] = DEFAULT_ALLOWED_APPS,
) -> bool:
    """Return True if *app* or *package* matches the social/communication/email allowlist."""

    app_norm = app.strip().lower()
    pkg_norm = package.strip().lower()
    app_tokens = {tok for tok in _TOKEN_SPLIT.split(app_norm) if tok}
    pkg_tokens = {tok for tok in pkg_norm.split(".") if tok}

    for raw in allowed_apps:
        candidate = raw.strip().lower()
        if not candidate:
            continue
        if candidate == app_norm or (pkg_norm and candidate == pkg_norm):
            return True
        if "." in candidate:
            if pkg_norm and (pkg_norm == candidate or pkg_norm.startswith(candidate + ".")):
                return True
            continue
        if len(candidate) <= 2:
            if app_norm == candidate:
                return True
            continue
        if " " in candidate or "-" in candidate:
            if candidate in app_norm:
                return True
        if candidate in app_tokens or candidate in pkg_tokens:
            return True
    return False


class Notifier(ABC):
    name = "abstract"

    @abstractmethod
    async def send(
        self,
        app: str,
        title: str,
        text: str,
        *,
        icon: str = "",
        urgency: str = "normal",
        timeout: int = 8000,
        replaces_id: int = 0,
    ) -> int:
        """Display a notification and return its server side id (0 if unknown)."""

    async def close(self) -> None:  # pragma: no cover - optional hook
        return None


class NullNotifier(Notifier):
    name = "none"

    async def send(
        self,
        app: str,
        title: str,
        text: str,
        *,
        icon: str = "",
        urgency: str = "normal",
        timeout: int = 8000,
        replaces_id: int = 0,
    ) -> int:
        return 0


class ConsoleNotifier(Notifier):
    name = "console"

    async def send(
        self,
        app: str,
        title: str,
        text: str,
        *,
        icon: str = "",
        urgency: str = "normal",
        timeout: int = 8000,
        replaces_id: int = 0,
    ) -> int:
        width = max(len(app), len(title), len(text), 20)
        border = "+" + "-" * (width + 2) + "+"
        lines = [
            border,
            f"| {app.ljust(width)} |",
            f"| {title.ljust(width)} |",
            f"| {text.ljust(width)} |",
            border,
        ]
        print("\n".join(lines), flush=True)
        return 0


class NotifySendNotifier(Notifier):
    name = "notify-send"

    def __init__(self, binary: str = "notify-send") -> None:
        self.binary = binary

    @classmethod
    def available(cls) -> bool:
        return shutil.which("notify-send") is not None

    async def send(
        self,
        app: str,
        title: str,
        text: str,
        *,
        icon: str = "",
        urgency: str = "normal",
        timeout: int = 8000,
        replaces_id: int = 0,
    ) -> int:
        args = [self.binary, "-a", app, "-t", str(timeout), "-u", urgency]
        if icon:
            args += ["-i", icon]
        args += [title or app, text]
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                log.warning("notify-send failed: %s", (stderr or b"").decode().strip())
            return 0
        except FileNotFoundError:
            log.error("notify-send not found")
            return 0


class DbusNotifier(Notifier):
    name = "dbus"

    def __init__(self) -> None:
        self._loop = asyncio.get_event_loop()
        self._next_id = 1
        self._lock = asyncio.Lock()

    @staticmethod
    def available() -> bool:
        try:
            import dbus  # noqa: F401
        except ImportError:
            return False
        try:
            import dbus

            bus = dbus.SessionBus()
            bus.get_object(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
            )
            return True
        except Exception:
            return False

    def _send_sync(
        self,
        app: str,
        title: str,
        text: str,
        icon: str,
        urgency: str,
        timeout: int,
        replaces_id: int,
    ) -> int:
        import dbus

        bus = dbus.SessionBus()
        obj = bus.get_object(
            "org.freedesktop.Notifications", "/org/freedesktop/Notifications"
        )
        iface = dbus.Interface(obj, "org.freedesktop.Notifications")
        hints = {
            "urgency": dbus.Byte(URGENCY_LEVELS.get(urgency, 1)),
            "desktop-entry": dbus.String(app),
        }
        notification_id = iface.Notify(
            dbus.String(app),
            dbus.UInt32(replaces_id),
            dbus.String(icon or ""),
            dbus.String(title or app),
            dbus.String(text),
            dbus.Array([], signature="s"),
            dbus.Dictionary(hints, signature="sv"),
            dbus.Int32(timeout),
        )
        return int(notification_id)

    async def send(
        self,
        app: str,
        title: str,
        text: str,
        *,
        icon: str = "",
        urgency: str = "normal",
        timeout: int = 8000,
        replaces_id: int = 0,
    ) -> int:
        async with self._lock:
            try:
                return await asyncio.to_thread(
                    self._send_sync, app, title, text, icon, urgency, timeout, replaces_id
                )
            except Exception as exc:  # dbus errors are not very descriptive
                log.warning("D-Bus notification failed: %s", exc)
                return 0


def _icon_for(app: str, icon_map: dict[str, str], default: str) -> str:
    lowered = app.strip().lower()
    if lowered in icon_map:
        return icon_map[lowered]
    for key, value in icon_map.items():
        if key and key in lowered:
            return value
    return default


class NotificationService:
    """Resolve a backend and apply configured filtering/icon mapping."""

    def __init__(
        self,
        backend: str = "auto",
        *,
        icon_map: dict[str, str] | None = None,
        default_icon: str = "phone",
        urgency: str = "normal",
        timeout: int = 8000,
        allowed_apps: tuple[str, ...] = DEFAULT_ALLOWED_APPS,
        only_allowed_apps: bool = True,
        ignore_apps: tuple[str, ...] = (),
        ignore_ongoing: bool = True,
        ignore_low_priority: bool = True,
    ) -> None:
        self.backend = self._resolve(backend)
        self.icon_map = {k.lower(): v for k, v in (icon_map or {}).items()}
        self.default_icon = default_icon
        self.urgency = urgency
        self.timeout = timeout
        self.allowed_apps = {a.strip().lower() for a in allowed_apps if a.strip()}
        self.only_allowed_apps = only_allowed_apps
        self.ignore_apps = {a.strip().lower() for a in ignore_apps if a.strip()}
        self.ignore_ongoing = ignore_ongoing
        self.ignore_low_priority = ignore_low_priority
        self.sent = 0

    @staticmethod
    def _resolve(backend: str) -> Notifier:
        if backend == "dbus":
            return DbusNotifier()
        if backend == "notify-send":
            return NotifySendNotifier()
        if backend == "console":
            return ConsoleNotifier()
        if backend == "none":
            return NullNotifier()
        if DbusNotifier.available():
            return DbusNotifier()
        if NotifySendNotifier.available():
            return NotifySendNotifier()
        return ConsoleNotifier()

    def is_app_allowed(self, app: str, package: str = "") -> bool:
        return is_app_allowed(app, package, self.allowed_apps)

    def should_ignore(
        self,
        *,
        app: str,
        package: str = "",
        ongoing: bool = False,
        low_priority: bool = False,
    ) -> bool:
        app_norm = app.strip().lower()
        pkg_norm = package.strip().lower()
        if app_norm in self.ignore_apps or (pkg_norm and pkg_norm in self.ignore_apps):
            return True
        if ongoing and self.ignore_ongoing:
            return True
        if low_priority and self.ignore_low_priority:
            return True
        if self.only_allowed_apps and self.allowed_apps:
            if not self.is_app_allowed(app, package):
                return True
        return False

    async def notify(
        self,
        *,
        app: str,
        title: str,
        text: str,
        package: str = "",
        ongoing: bool = False,
        low_priority: bool = False,
        urgency: str | None = None,
        icon: str | None = None,
    ) -> int | None:
        if self.should_ignore(
            app=app,
            package=package,
            ongoing=ongoing,
            low_priority=low_priority,
        ):
            log.debug("ignoring notification from %s (%s)", app, package or "no-pkg")
            return None
        icon = icon or _icon_for(app, self.icon_map, self.default_icon)
        notification_id = await self.backend.send(
            app,
            title,
            text,
            icon=icon,
            urgency=urgency or self.urgency,
            timeout=self.timeout,
        )
        self.sent += 1
        return notification_id

    async def close(self) -> None:
        await self.backend.close()
