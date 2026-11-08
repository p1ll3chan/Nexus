"""Tailscale peer discovery.

Tailscale handles the actual networking; this module only asks the local
``tailscale`` CLI whether a peer is online and what its tailnet address is, so
the server can log useful information and expose presence over an API.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import shutil
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("phone-bridge.discovery")


class DiscoveryError(RuntimeError):
    """Raised when tailscale status cannot be queried."""


@dataclass
class Peer:
    hostname: str
    dns_name: str
    os: str
    online: bool
    tailscale_ips: tuple[str, ...]
    last_seen: str = ""

    @property
    def addresses(self) -> tuple[str, ...]:
        return self.tailscale_ips

    def matches(self, name: str) -> bool:
        needle = name.strip().lower().rstrip(".")
        if not needle:
            return False
        candidates = {
            self.hostname.lower(),
            self.dns_name.lower().rstrip("."),
            self.dns_name.lower().split(".")[0],
            *[ip.lower() for ip in self.tailscale_ips],
        }
        return needle in candidates


@dataclass
class Status:
    self_peer: Peer | None
    peers: list[Peer]

    def find(self, name: str) -> Peer | None:
        for peer in self.peers:
            if peer.matches(name):
                return peer
        return None

    def online_peers(self) -> list[Peer]:
        return [p for p in self.peers if p.online]


class TailscaleDiscovery:
    def __init__(self, command: str = "tailscale", timeout: float = 5.0) -> None:
        self.command = command
        self.timeout = timeout

    @staticmethod
    def available(command: str = "tailscale") -> bool:
        return shutil.which(command) is not None

    async def status(self) -> Status:
        if not self.available(self.command):
            raise DiscoveryError(f"{self.command!r} not found in PATH")
        try:
            proc = await asyncio.create_subprocess_exec(
                self.command,
                "status",
                "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise DiscoveryError(str(exc)) from exc
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), self.timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise DiscoveryError("tailscale status timed out") from exc
        if proc.returncode != 0:
            raise DiscoveryError((stderr or b"").decode().strip() or "tailscale status failed")
        try:
            data = json.loads(stdout.decode("utf-8", errors="replace") or "{}")
        except json.JSONDecodeError as exc:
            raise DiscoveryError(f"invalid tailscale JSON: {exc}") from exc
        return self._parse(data)

    def _parse(self, data: dict[str, Any]) -> Status:
        self_peer = self._parse_peer(data.get("Self") or {})
        peers = [
            self._parse_peer(raw)
            for raw in (data.get("Peer") or {}).values()
            if isinstance(raw, dict)
        ]
        peers.sort(key=lambda p: p.hostname.lower())
        return Status(self_peer=self_peer, peers=peers)

    def _parse_peer(self, raw: dict[str, Any]) -> Peer:
        ips = raw.get("TailscaleIPs") or raw.get("AltShownIPs") or []
        cleaned: list[str] = []
        for candidate in ips:
            try:
                cleaned.append(str(ipaddress.ip_address(str(candidate))))
            except ValueError:
                continue
        return Peer(
            hostname=str(raw.get("HostName") or raw.get("DNSName") or "unknown"),
            dns_name=str(raw.get("DNSName") or ""),
            os=str(raw.get("OS") or ""),
            online=bool(raw.get("Online")),
            tailscale_ips=tuple(cleaned),
            last_seen=str(raw.get("LastSeen") or ""),
        )

    async def resolve(self, name: str) -> Peer | None:
        try:
            status = await self.status()
        except DiscoveryError as exc:
            log.warning("discovery failed: %s", exc)
            return None
        return status.find(name)

    async def is_online(self, name: str) -> bool:
        peer = await self.resolve(name)
        return bool(peer and peer.online)
