"""Phone-side WebSocket client.

Although the real phone is an Android app, this module is useful in three ways:

* as a reference implementation of the connection/authentication handshake;
* as a test client (``phone-bridge simulate``) that emulates a phone; and
* as a small CLI to send one-off notifications from a shell.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

import websockets
from websockets.asyncio.client import ClientConnection, connect

import protocol
from config import Config
from protocol import Call, CallState, Locate, Location, Notification, Ping, ProtocolError

log = logging.getLogger("phone-bridge.client")


class ClientError(RuntimeError):
    pass


@dataclass
class BridgeClient:
    config: Config
    device: str = "test-phone"

    def __init__(self, config: Config, device: str = "test-phone") -> None:
        self.config = config
        self.device = device

    @property
    def uri(self) -> str:
        host = self.config.host
        if host in {"0.0.0.0", "::", ""}:
            host = "127.0.0.1"
        return f"ws://{host}:{self.config.port}"

    async def __aenter__(self) -> "BridgeClient":
        self._ws = await self._connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def _connect(self) -> ClientConnection:
        try:
            ws = await connect(self.uri, max_size=self.config.max_message_bytes)
        except OSError as exc:
            raise ClientError(f"cannot connect to {self.uri}: {exc}") from exc
        hello = protocol.Hello(device=self.device, token=self.config.token)
        await ws.send(protocol.dumps(hello.to_dict()))
        try:
            raw = await asyncio.wait_for(ws.recv(), 10)
        except asyncio.TimeoutError as exc:
            await ws.close()
            raise ClientError("no hello_ack from server") from exc
        data = protocol.loads(raw)
        if protocol.message_type(data) is protocol.MessageType.ERROR:
            await ws.close()
            raise ClientError(f"server rejected connection: {data.get('message')}")
        if protocol.message_type(data) is not protocol.MessageType.HELLO_ACK:
            await ws.close()
            raise ClientError(f"unexpected handshake frame: {data.get('type')}")
        return ws

    async def close(self) -> None:
        ws = getattr(self, "_ws", None)
        if ws is not None:
            await ws.close()
            self._ws = None

    async def send(self, event: Notification | Call | Location | Locate | Ping) -> dict[str, Any]:
        ws = getattr(self, "_ws", None)
        if ws is None:
            raise ClientError("client is not connected")
        await ws.send(protocol.dumps(event.to_dict()))
        return {"sent": event.to_dict().get("id", ""), "type": event.to_dict()["type"]}

    async def notify(
        self,
        app: str,
        title: str = "",
        text: str = "",
        *,
        package: str = "",
        ongoing: bool = False,
        low_priority: bool = False,
    ) -> str:
        event = Notification(
            app=app,
            title=title,
            text=text,
            package=package,
            ongoing=ongoing,
            low_priority=low_priority,
        )
        await self.send(event)
        return event.id

    async def call(self, state: CallState, name: str = "", number: str = "") -> str:
        event = Call(state=state, name=name, number=number)
        await self.send(event)
        return event.id

    async def location(
        self,
        latitude: float,
        longitude: float,
        *,
        accuracy: float = 8.0,
        battery: int | None = 85,
        charging: bool = False,
        altitude: float | None = None,
        speed: float | None = None,
        bearing: float | None = None,
        provider: str = "gps",
    ) -> str:
        event = Location(
            latitude=latitude,
            longitude=longitude,
            accuracy=accuracy,
            battery=battery,
            charging=charging,
            altitude=altitude,
            speed=speed,
            bearing=bearing,
            provider=provider,
        )
        await self.send(event)
        return event.id

    async def locate(self) -> str:
        event = Locate()
        await self.send(event)
        return event.id

    async def ack_loop(self) -> AsyncIterator[dict[str, Any]]:
        ws = getattr(self, "_ws", None)
        if ws is None:
            raise ClientError("client is not connected")
        async for raw in ws:
            try:
                yield protocol.loads(raw)
            except ProtocolError:
                continue


async def simulate(config: Config, device: str = "test-phone") -> None:
    """Connect and emit a scripted sequence of notifications, calls, and GPS fixes."""

    route = [
        (37.77493, -122.41942, 6.5, 92, 1.4, 48.0),
        (37.77535, -122.41875, 5.8, 91, 1.5, 52.0),
        (37.77582, -122.41802, 5.2, 91, 1.6, 54.0),
        (37.77634, -122.41725, 4.9, 90, 1.5, 49.0),
        (37.77688, -122.41650, 4.5, 89, 1.3, 45.0),
    ]

    async with BridgeClient(config, device=device) as client:
        log.info("connected to %s as %s", client.uri, device)
        await asyncio.sleep(0.15)
        await client.notify(
            "WhatsApp",
            "John",
            "Hey, are you free?",
            package="com.whatsapp",
        )
        await asyncio.sleep(0.25)
        await client.notify(
            "ShoppingApp",
            "Flash Sale",
            "50% off toaster ovens!",
            package="com.example.shopping",
        )
        await asyncio.sleep(0.25)
        for lat, lon, acc, batt, spd, brg in route:
            await client.location(
                lat,
                lon,
                accuracy=acc,
                battery=batt,
                charging=False,
                speed=spd,
                bearing=brg,
                provider="gps",
            )
            await asyncio.sleep(0.25)
        await client.call(CallState.RINGING, name="John", number="+1 555 0100")
        await asyncio.sleep(0.35)
        await client.call(CallState.OFFHOOK, name="John", number="+1 555 0100")
        await asyncio.sleep(0.35)
        await client.call(CallState.IDLE, name="John", number="+1 555 0100")
        await asyncio.sleep(0.25)
        await client.notify(
            "Gmail",
            "Team Lead",
            "Design review notes attached",
            package="com.google.android.gm",
        )
        await asyncio.sleep(0.25)

        async def drain() -> None:
            async for frame in client.ack_loop():
                log.info("server: %s", frame)

        try:
            await asyncio.wait_for(drain(), timeout=1.5)
        except asyncio.TimeoutError:
            pass


async def ping(config: Config, device: str = "test-phone") -> float:
    """Measure round-trip time to the server."""

    async with BridgeClient(config, device=device) as client:
        started = time.perf_counter()
        await client.send(Ping())
        async for frame in client.ack_loop():
            if frame.get("type") == protocol.MessageType.PONG.value:
                return (time.perf_counter() - started) * 1000
        raise ClientError("no pong received")
