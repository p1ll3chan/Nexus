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
from remote_dispatcher import RemoteDispatcher, RemotePrincipal
from remote_protocol import is_remote_type
from remote_registry import OperationContext, OperationRegistry, default_registry
from remote_store import RemoteStore, hash_secret
from storage import LocationRecord, LocationStore

log = logging.getLogger("phone-bridge.server")

HELLO_TIMEOUT = 10.0
REVOCATION_SWEEP_SECONDS = 5.0
CLOSE_REVOKED = 4001
CLOSE_SESSION_EXPIRED = 4002


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
    principal: RemotePrincipal | None = None
    remote_tasks: set[asyncio.Task[Any]] = field(default_factory=set)

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
            "auth": "device" if self.principal else "token",
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
        remote_store: RemoteStore | None = None,
        registry: OperationRegistry | None = None,
        proc_root: str = "/proc",
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
        self.remote_store: RemoteStore | None = None
        self.dispatcher: RemoteDispatcher | None = None
        self._owns_remote_store = False
        if config.remote_enabled or remote_store is not None:
            self.remote_store = remote_store or RemoteStore(config.remote_db_path)
            self._owns_remote_store = remote_store is None
        if config.remote_enabled and self.remote_store is not None:
            from remote_dispatcher import DispatcherLimits

            self.dispatcher = RemoteDispatcher(
                registry or default_registry(),
                self.remote_store,
                OperationContext(
                    device_name=config.device_name,
                    disk_paths=tuple(config.remote_disks.items()),
                    proc_root=proc_root,
                ),
                limits=DispatcherLimits(approval_ttl=config.remote_approval_ttl),
                on_approval_needed=self._prompt_approval,
            )
        self._revocation_task: asyncio.Task[None] | None = None
        self.sessions: dict[ServerConnection, ClientSession] = {}
        self._listeners: set[asyncio.Queue[dict[str, Any]]] = set()
        self._server: Server | None = None
        self._watchdog: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self.received = 0
        self.locations_received = 0

    @property
    def port(self) -> int:
        """Actual bound port (useful when configured with port 0)."""

        if self._server is not None:
            for sock in self._server.sockets:
                return int(sock.getsockname()[1])
        return self.config.port

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
            if self.dispatcher is not None:
                await self._send(
                    websocket,
                    self.dispatcher.capabilities(session.principal, self.config.device_name),
                )
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
                for task in list(session.remote_tasks):
                    task.cancel()
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

        principal: RemotePrincipal | None = None
        try:
            if hello.uses_device_credential:
                principal = self._authenticate_device(hello, address)
            else:
                if not self.config.legacy_token_allowed():
                    log.warning("legacy token auth disabled; rejected %s from %s", hello.device, address)
                    await self._send(
                        websocket,
                        protocol.error(
                            "legacy_auth_disabled",
                            "shared-token login is disabled; pair this device",
                        ),
                    )
                    return None
                self.authenticator.verify(hello.token, source=address, device=hello.device)
        except AuthError as exc:
            log.warning("authentication failed for %s from %s: %s", hello.device, address, exc)
            await self._send(websocket, protocol.error("auth_failed", "authentication failed"))
            return None

        await self._send(
            websocket,
            protocol.hello_ack(
                self.config.device_name,
                protocol.PROTOCOL_VERSION,
                auth="device" if principal else "token",
                device_id=principal.device_id if principal else "",
            ),
        )
        if principal is not None:
            log.info("device %s authenticated as %s", hello.device, principal.device_id)
        return ClientSession(
            device=hello.device,
            address=address,
            platform=hello.platform,
            principal=principal,
        )

    def _authenticate_device(self, hello: Hello, address: str) -> RemotePrincipal:
        """Verify a per-device credential.  Never falls back to the shared token."""

        self.authenticator.check_rate(address)
        record = (
            self.remote_store.authenticate(hello.device_id, hello.device_secret)
            if self.remote_store is not None
            else None
        )
        self.authenticator.record_result(address, record is not None, hello.device)
        if record is None:
            raise AuthError("invalid, unknown or revoked device credential")
        self.remote_store.touch(record.device_id)  # type: ignore[union-attr]
        return RemotePrincipal(
            device_id=record.device_id,
            name=record.name,
            secret_hash=hash_secret(hello.device_secret),
        )

    async def _read_loop(self, websocket: ServerConnection, session: ClientSession) -> None:
        async for raw in websocket:
            session.last_seen = time.time()
            try:
                data = protocol.loads(raw)
                if is_remote_type(data.get("type")):
                    self._dispatch_remote(websocket, session, data, len(raw))
                    continue
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

    def _dispatch_remote(
        self,
        websocket: ServerConnection,
        session: ClientSession,
        data: dict[str, Any],
        raw_size: int,
    ) -> None:
        if self.dispatcher is None:
            rid = data.get("request_id") if isinstance(data.get("request_id"), str) else ""
            asyncio.create_task(
                self._send(
                    websocket,
                    {
                        "type": "remote.response",
                        "v": 1,
                        "request_id": rid[:64],
                        "status": "rejected",
                        "error": {"code": "remote_disabled", "message": "Nexus Remote is disabled"},
                    },
                )
            )
            return

        async def send(frame: dict[str, Any]) -> bool:
            return await self._send(websocket, frame)

        async def on_revoked() -> None:
            await websocket.close(code=CLOSE_REVOKED, reason="device revoked")

        task = asyncio.create_task(
            self.dispatcher.handle(data, raw_size, session.principal, send, on_revoked)
        )
        session.remote_tasks.add(task)
        task.add_done_callback(session.remote_tasks.discard)
        task.add_done_callback(self._log_task_error)

    @staticmethod
    def _log_task_error(task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None and not isinstance(exc, websockets.ConnectionClosed):
            log.error("remote request task failed: %r", exc)

    async def _prompt_approval(self, record: Any, principal: RemotePrincipal) -> None:
        await self.notifier.notify(
            app="Nexus Remote",
            title="Approval required",
            text=(
                f"{principal.name} requests {record.operation}. "
                f"Run: phone-bridge remote approve {record.approval_id}"
            ),
            urgency="critical",
            icon=self.config.app_icon,
        )

    async def _revocation_loop(self) -> None:
        """Close sessions whose device was revoked/rotated or exceeded max age."""

        while not self._stopping.is_set():
            await asyncio.sleep(REVOCATION_SWEEP_SECONDS)
            if self.remote_store is None:
                continue
            for websocket, session in list(self.sessions.items()):
                principal = session.principal
                if principal is None:
                    continue
                try:
                    valid = await asyncio.to_thread(
                        self.remote_store.session_valid, principal.device_id, principal.secret_hash
                    )
                except Exception:  # pragma: no cover
                    log.exception("revocation check failed")
                    continue
                if not valid:
                    log.warning("closing session for revoked/rotated device %s", principal.device_id)
                    with contextlib.suppress(Exception):
                        await websocket.close(code=CLOSE_REVOKED, reason="device revoked")
                elif time.time() - principal.authenticated_at > self.config.remote_session_max_age:
                    log.info("session max age reached for %s", principal.device_id)
                    with contextlib.suppress(Exception):
                        await websocket.close(code=CLOSE_SESSION_EXPIRED, reason="session expired")

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
        if self.remote_store is not None:
            pruned = self.remote_store.prune_audit(self.config.audit_retention_days)
            if pruned:
                log.info("pruned %d old remote audit rows", pruned)
            self._revocation_task = asyncio.create_task(self._revocation_loop())
        if self.config.host in {"0.0.0.0", "::", ""}:
            log.warning(
                "listening on all interfaces (%s); set host to your Tailscale IP to limit exposure",
                self.config.host,
            )
        if self.config.legacy_token_allowed():
            log.info("legacy shared-token login enabled (notifications/calls/GPS only, never remote)")
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
        for task in (self._watchdog, self._revocation_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await task
        for websocket in list(self.sessions):
            await websocket.close(code=1001, reason="server shutting down")
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        await self.notifier.close()
        if self._owns_store:
            self.store.close()
        if self._owns_remote_store and self.remote_store is not None:
            self.remote_store.close()
        log.info("phone-bridge stopped")

    @staticmethod
    def _address(websocket: ServerConnection) -> str:
        remote = websocket.remote_address
        if isinstance(remote, tuple) and remote:
            return str(remote[0])
        return str(remote or "unknown")
