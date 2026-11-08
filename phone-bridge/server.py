"""Async WebSocket server that receives events from the phone."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import websockets
from websockets.asyncio.server import Server, ServerConnection, serve

import protocol
from authentication import AuthError, Authenticator
from config import Config
from discovery import TailscaleDiscovery
from notifications import NotificationService
from protocol import (
    Call,
    CallState,
    Hello,
    Locate,
    Location,
    Notification,
    Ping,
    Pong,
    ProtocolError,
)
from storage import LocationRecord, LocationStore

log = logging.getLogger("phone-bridge.server")

HELLO_TIMEOUT = 10.0


@dataclass
class ClientSession:
    device: str
    address: str
    connected_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    platform: str = "android"
    notifications: int = 0
    calls: int = 0
    locations: int = 0
    last_location: LocationRecord | None = None
    pong: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def uptime(self) -> float:
        return time.time() - self.connected_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "address": self.address,
            "connected_at": self.connected_at,
            "last_seen": self.last_seen,
            "uptime": round(self.uptime, 1),
            "platform": self.platform,
            "notifications": self.notifications,
            "calls": self.calls,
            "locations": self.locations,
            "last_location": self.last_location.to_dict() if self.last_location else None,
        }


class BridgeServer:
    def __init__(
        self,
        config: Config,
        *,
        notifier: NotificationService | None = None,
        discovery: TailscaleDiscovery | None = None,
        store: LocationStore | None = None,
    ) -> None:
        self.config = config
        self.authenticator = Authenticator(config.token)
        self.notifier = notifier or NotificationService(
            config.notify_backend,
            icon_map=config.icon_map,
            default_icon=config.default_icon,
            urgency=config.notification_urgency,
            timeout=config.notification_timeout,
            allowed_apps=config.allowed_apps,
            only_allowed_apps=config.only_allowed_apps,
            ignore_apps=config.ignore_apps,
            ignore_ongoing=config.ignore_ongoing,
            ignore_low_priority=config.ignore_low_priority,
        )
        self.discovery = discovery or TailscaleDiscovery(config.tailscale_command)
        self.store = store or LocationStore(config.db_path)
        self._owns_store = store is None
        self.sessions: dict[ServerConnection, ClientSession] = {}
        self._listeners: set[asyncio.Queue[dict[str, Any]]] = set()
        self._server: Server | None = None
        self._watchdog: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self.received = 0
        self.locations_received = 0

    @property
    def client_count(self) -> int:
        return len(self.sessions)

    def active_sessions(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self.sessions.values()]

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=128)
        self._listeners.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._listeners.discard(queue)

    def _broadcast(self, payload: dict[str, Any]) -> None:
        for queue in list(self._listeners):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(payload)

    async def request_location(self, device: str | None = None) -> int:
        frame = protocol.locate_request()
        sent = 0
        target = device.strip().lower() if device else ""
        for ws, session in list(self.sessions.items()):
            if target and session.device.strip().lower() != target:
                continue
            if await self._send(ws, frame):
                sent += 1
        return sent

    async def handler(self, websocket: ServerConnection) -> None:
        address = self._address(websocket)
        session: ClientSession | None = None
        try:
            session = await self._handshake(websocket, address)
            if session is None:
                return
            session.last_location = self.store.latest(session.device)
            self.sessions[websocket] = session
            log.info("device connected: %s (%s)", session.device, address)
            self._broadcast(
                {
                    "event": "session",
                    "state": "connected",
                    "session": session.to_dict(),
                }
            )
            watchdog = asyncio.create_task(self._heartbeat(websocket, session))
            try:
                await self._read_loop(websocket, session)
            finally:
                watchdog.cancel()
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await watchdog
        except websockets.ConnectionClosed:
            log.info("device disconnected: %s", session.device if session else address)
        except Exception:
            log.exception("unhandled error for %s", session.device if session else address)
        finally:
            if session is not None:
                self.sessions.pop(websocket, None)
                self._broadcast(
                    {
                        "event": "session",
                        "state": "disconnected",
                        "device": session.device,
                    }
                )
                log.info(
                    "device gone: %s (uptime %.0fs, %d notifications, %d calls, %d locations)",
                    session.device,
                    session.uptime,
                    session.notifications,
                    session.calls,
                    session.locations,
                )

    async def _handshake(self, websocket: ServerConnection, address: str) -> ClientSession | None:
        try:
            raw = await asyncio.wait_for(websocket.recv(), HELLO_TIMEOUT)
        except asyncio.TimeoutError:
            await self._send(websocket, protocol.error("hello_timeout", "no hello received"))
            return None
        except websockets.ConnectionClosed:
            return None

        try:
            data = protocol.loads(raw)
            if protocol.message_type(data) is not protocol.MessageType.HELLO:
                raise ProtocolError("first frame must be hello")
            hello = Hello.from_dict(data)
        except ProtocolError as exc:
            log.warning("bad hello from %s: %s", address, exc)
            await self._send(websocket, protocol.error("bad_hello", str(exc)))
            return None

        if not self.config.is_device_allowed(hello.device):
            log.warning("rejected unknown device %s from %s", hello.device, address)
            await self._send(websocket, protocol.error("device_not_allowed", "device not allowed"))
            return None

        try:
            self.authenticator.verify(hello.token, source=address, device=hello.device)
        except AuthError as exc:
            log.warning("authentication failed for %s from %s: %s", hello.device, address, exc)
            await self._send(websocket, protocol.error("auth_failed", "authentication failed"))
            return None

        await self._send(
            websocket,
            protocol.hello_ack(self.config.device_name, protocol.PROTOCOL_VERSION),
        )
        return ClientSession(
            device=hello.device,
            address=address,
            platform=hello.platform,
        )

    async def _read_loop(self, websocket: ServerConnection, session: ClientSession) -> None:
        async for raw in websocket:
            session.last_seen = time.time()
            try:
                data = protocol.loads(raw)
                event = protocol.parse_event(data)
            except ProtocolError as exc:
                log.warning("protocol error from %s: %s", session.device, exc)
                await self._send(websocket, protocol.error("protocol_error", str(exc)))
                continue

            if isinstance(event, Ping):
                await self._send(websocket, Pong(timestamp=event.timestamp).to_dict())
            elif isinstance(event, Pong):
                session.pong.set()
            elif isinstance(event, Notification):
                await self._handle_notification(websocket, session, event)
            elif isinstance(event, Call):
                await self._handle_call(websocket, session, event)
            elif isinstance(event, Location):
                await self._handle_location(websocket, session, event)
            elif isinstance(event, Locate):
                count = await self.request_location()
                await self._send(
                    websocket,
                    protocol.ack(event.id, ok=count > 0, detail=f"requested={count}"),
                )

    async def _handle_notification(
        self,
        websocket: ServerConnection,
        session: ClientSession,
        event: Notification,
    ) -> None:
        self.received += 1
        session.notifications += 1
        log.info(
            "notification from %s: [%s] %s - %s",
            session.device,
            event.app,
            event.title or "(no title)",
            event.text or "(no text)",
        )
        if not self.config.allow_notifications:
            await self._send(websocket, protocol.ack(event.id, ok=False, detail="disabled"))
            return
        if self.notifier.should_ignore(
            app=event.app,
            package=event.package,
            ongoing=event.ongoing,
            low_priority=event.low_priority,
        ):
            await self._send(websocket, protocol.ack(event.id, ok=True, detail="ignored"))
            return
        await self.notifier.notify(
            app=event.app,
            package=event.package,
            title=event.title,
            text=event.text,
            ongoing=event.ongoing,
            low_priority=event.low_priority,
        )
        self._broadcast(
            {
                "event": "notification",
                "device": session.device,
                "notification": event.to_dict(),
            }
        )
        await self._send(websocket, protocol.ack(event.id, ok=True))

    async def _handle_call(
        self,
        websocket: ServerConnection,
        session: ClientSession,
        event: Call,
    ) -> None:
        self.received += 1
        session.calls += 1
        who = event.name or event.number or "Unknown caller"
        log.info("call from %s: %s (%s)", session.device, event.state.value, who)
        if not self.config.allow_calls:
            await self._send(websocket, protocol.ack(event.id, ok=False, detail="disabled"))
            return
        titles = {
            CallState.RINGING: "\U0001f4de Incoming call",
            CallState.OFFHOOK: "\U0001f4de Call answered",
            CallState.IDLE: "\U0001f4de Call ended",
        }
        urgency = "critical" if event.state is CallState.RINGING else "normal"
        await self.notifier.notify(
            app="Phone",
            title=titles[event.state],
            text=who,
            urgency=urgency,
            icon=self.config.app_icon,
        )
        self._broadcast(
            {
                "event": "call",
                "device": session.device,
                "call": event.to_dict(),
            }
        )
        await self._send(websocket, protocol.ack(event.id, ok=True))

    async def _handle_location(
        self,
        websocket: ServerConnection,
        session: ClientSession,
        event: Location,
    ) -> None:
        self.received += 1
        self.locations_received += 1
        session.locations += 1
        batt = f"{event.battery}%" if event.battery is not None else "n/a"
        log.info(
            "location from %s: %.6f, %.6f (±%.1fm, battery=%s)",
            session.device,
            event.latitude,
            event.longitude,
            event.accuracy,
            batt,
        )
        if not self.config.allow_location:
            await self._send(websocket, protocol.ack(event.id, ok=False, detail="disabled"))
            return
        record = await asyncio.to_thread(
            self.store.record,
            session.device,
            event,
            retention_days=self.config.location_retention_days,
        )
        session.last_location = record
        self._broadcast(
            {
                "event": "location",
                "device": session.device,
                "location": record.to_dict(),
            }
        )
        await self._send(websocket, protocol.ack(event.id, ok=True))

    async def _heartbeat(self, websocket: ServerConnection, session: ClientSession) -> None:
        interval = max(5.0, self.config.heartbeat_interval)
        timeout = max(interval * 2, self.config.heartbeat_timeout)
        try:
            while True:
                session.pong.clear()
                await self._send(websocket, Ping().to_dict())
                try:
                    await asyncio.wait_for(session.pong.wait(), timeout)
                except asyncio.TimeoutError:
                    log.warning("heartbeat timeout for %s", session.device)
                    await websocket.close(code=1001, reason="heartbeat timeout")
                    return
                await asyncio.sleep(interval)
        except (websockets.ConnectionClosed, asyncio.CancelledError):
            return

    async def _watchdog_loop(self) -> None:
        while not self._stopping.is_set():
            await asyncio.sleep(max(5.0, self.config.discovery_interval))
            if not self.config.phone_name:
                continue
            try:
                online = await self.discovery.is_online(self.config.phone_name)
            except Exception as exc:  # pragma: no cover - best effort
                log.debug("watchdog discovery error: %s", exc)
                continue
            state = "online" if online else "offline"
            log.debug("phone %s is %s", self.config.phone_name, state)

    async def _send(self, websocket: ServerConnection, frame: dict[str, Any]) -> bool:
        try:
            await websocket.send(protocol.dumps(frame))
            return True
        except websockets.ConnectionClosed:
            return False

    async def start(self) -> None:
        self._server = await serve(
            self.handler,
            self.config.host,
            self.config.port,
            max_size=self.config.max_message_bytes,
            ping_interval=None,
        )
        self._watchdog = asyncio.create_task(self._watchdog_loop())
        log.info(
            "phone-bridge listening on %s:%s (backend=%s)",
            self.config.host,
            self.config.port,
            self.notifier.backend.name,
        )

    async def serve_forever(self) -> None:
        await self.start()
        assert self._server is not None
        try:
            await asyncio.Future()
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._stopping.set()
        if self._watchdog is not None:
            self._watchdog.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await self._watchdog
        for websocket in list(self.sessions):
            await websocket.close(code=1001, reason="server shutting down")
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        await self.notifier.close()
        if self._owns_store:
            self.store.close()
        log.info("phone-bridge stopped")

    @staticmethod
    def _address(websocket: ServerConnection) -> str:
        remote = websocket.remote_address
        if isinstance(remote, tuple) and remote:
            return str(remote[0])
        return str(remote or "unknown")
