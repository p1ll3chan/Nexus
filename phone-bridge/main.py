"""Command line entry point for phone-bridge."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import signal
import sys
import webbrowser
from pathlib import Path
from typing import Any, Sequence

import client as client_module
import config as config_module
import discovery as discovery_module
from config import Config, ConfigError, load_config, write_default_config
from server import BridgeServer
from storage import LocationStore
from web import MapWebServer

log = logging.getLogger("phone-bridge")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _config_overrides(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "host": getattr(args, "host", None),
        "port": getattr(args, "port", None),
        "token": getattr(args, "token", None),
        "device_name": getattr(args, "device_name", None),
        "phone_name": getattr(args, "phone_name", None),
        "notify_backend": getattr(args, "backend", None),
        "log_level": getattr(args, "log_level", None),
        "allow_notifications": _tri_state(getattr(args, "notifications", None)),
        "allow_calls": _tri_state(getattr(args, "calls", None)),
        "allow_location": _tri_state(getattr(args, "location", None)),
        "only_allowed_apps": _tri_state(getattr(args, "only_allowed_apps", None)),
        "db_path": getattr(args, "db_path", None),
        "map_enabled": _tri_state(getattr(args, "map", None)),
        "map_host": getattr(args, "map_host", None),
        "map_port": getattr(args, "map_port", None),
        "remote_db_path": getattr(args, "remote_db_path", None),
    }


def _tri_state(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.lower() in {"on", "yes", "true", "1"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="phone-bridge",
        description="Bridge Android notifications, calls, and GPS location to a Linux desktop.",
    )
    parser.add_argument("--config", help="path to a TOML or JSON config file")
    parser.add_argument("--log-level", help="override the configured log level")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the WebSocket server and Map UI")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--token")
    serve.add_argument("--device-name", dest="device_name")
    serve.add_argument("--phone-name", dest="phone_name")
    serve.add_argument("--backend", choices=["auto", "dbus", "notify-send", "console", "none"])
    serve.add_argument("--notifications", choices=["on", "off"])
    serve.add_argument("--only-allowed-apps", dest="only_allowed_apps", choices=["on", "off"])
    serve.add_argument("--calls", choices=["on", "off"])
    serve.add_argument("--location", choices=["on", "off"])
    serve.add_argument("--db-path", dest="db_path")
    serve.add_argument("--map", choices=["on", "off"])
    serve.add_argument("--map-host", dest="map_host")
    serve.add_argument("--map-port", dest="map_port", type=int)

    map_cmd = sub.add_parser("map", help="run or open the standalone Map UI server")
    map_cmd.add_argument("--db-path", dest="db_path")
    map_cmd.add_argument("--map-host", dest="map_host")
    map_cmd.add_argument("--map-port", dest="map_port", type=int)
    map_cmd.add_argument("--open", action="store_true", help="open Map UI in browser")

    where = sub.add_parser("where", help="show latest known phone location from SQLite")
    where.add_argument("--device", help="filter by device name")
    where.add_argument("--db-path", dest="db_path")
    where.add_argument("--history", type=int, default=0, help="also list N recent fixes")
    where.add_argument("--json", action="store_true", help="output JSON")
    where.add_argument("--geojson", action="store_true", help="export GeoJSON")
    where.add_argument("--gpx", action="store_true", help="export GPX XML")

    loc = sub.add_parser("location", help="send a test GPS location event")
    loc.add_argument("latitude", type=float, nargs="?", default=37.7749)
    loc.add_argument("longitude", type=float, nargs="?", default=-122.4194)
    loc.add_argument("--accuracy", type=float, default=6.5)
    loc.add_argument("--battery", type=int, default=88)
    loc.add_argument("--charging", action="store_true")
    loc.add_argument("--speed", type=float, default=None)
    loc.add_argument("--altitude", type=float, default=None)
    loc.add_argument("--provider", default="gps")
    loc.add_argument("--device", default="test-phone")

    locate = sub.add_parser("locate", help="request an immediate GPS fix from connected phones")
    locate.add_argument("--device", default="test-phone")

    send = sub.add_parser("send", help="send a test notification")
    send.add_argument("app", nargs="?", default="WhatsApp")
    send.add_argument("title", nargs="?", default="John")
    send.add_argument("text", nargs="?", default="from phone-bridge")
    send.add_argument("--package", default="")
    send.add_argument("--device", default="test-phone")

    call = sub.add_parser("call", help="send a test call event")
    call.add_argument("state", choices=["ringing", "offhook", "idle"], nargs="?", default="ringing")
    call.add_argument("name", nargs="?", default="Test Caller")
    call.add_argument("--device", default="test-phone")

    simulate = sub.add_parser("simulate", help="emit a scripted sequence of events")
    simulate.add_argument("--device", default="test-phone")

    ping = sub.add_parser("ping", help="measure round-trip time to the server")
    ping.add_argument("--device", default="test-phone")

    sub.add_parser("token", help="generate a new shared secret")
    sub.add_parser("status", help="show effective configuration, peers, and latest GPS fix")

    init_config = sub.add_parser("init-config", help="write a starter config file")
    init_config.add_argument(
        "path",
        nargs="?",
        default=str(Path.home() / ".config" / "phone-bridge" / "config.toml"),
    )
    init_config.add_argument("--token")

    _add_remote_parser(sub)

    return parser


def _add_remote_parser(sub: Any) -> None:
    remote = sub.add_parser(
        "remote",
        help="Nexus Remote: pair devices, manage permissions, approvals and audit",
    )
    remote.add_argument("--remote-db", dest="remote_db_path", help="path to nexus.db")
    rsub = remote.add_subparsers(dest="remote_command", required=True)

    pair = rsub.add_parser("pair", help="pair a new device and print its credential once")
    pair.add_argument("name", help="human-friendly device name, e.g. pixel-9")
    pair.add_argument(
        "--grant",
        action="append",
        default=[],
        metavar="PERMISSION",
        help="grant a permission at pairing time (e.g. remote.read)",
    )
    pair.add_argument("--json", action="store_true")

    devices = rsub.add_parser("devices", help="list paired devices and their permissions")
    devices.add_argument("--json", action="store_true")

    for name, help_text in (
        ("revoke", "revoke a device: disconnects it and removes all grants"),
        ("rotate", "issue a new secret for a device (old secret stops working)"),
    ):
        cmd = rsub.add_parser(name, help=help_text)
        cmd.add_argument("device_id")
    rsub.add_parser("revoke-all", help="panic button: revoke every paired device")

    for name, help_text in (
        ("grant", "grant a permission to a device"),
        ("ungrant", "remove a permission from a device"),
    ):
        cmd = rsub.add_parser(name, help=help_text)
        cmd.add_argument("device_id")
        cmd.add_argument("permission", help="remote.read | remote.execute")

    ops = rsub.add_parser("ops", help="list the fixed operation registry")
    ops.add_argument("--json", action="store_true")

    run = rsub.add_parser("run", help="run a registered read operation locally (no network)")
    run.add_argument("operation")
    run.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="operation parameter; ints/bools are parsed as JSON",
    )

    audit = rsub.add_parser("audit", help="show remote audit metadata")
    audit.add_argument("--limit", type=int, default=30)
    audit.add_argument("--device", dest="audit_device")
    audit.add_argument("--json", action="store_true")

    rsub.add_parser("approvals", help="list pending approval requests")
    for name in ("approve", "deny"):
        cmd = rsub.add_parser(name, help=f"{name} a pending approval")
        cmd.add_argument("approval_id")

    migrate = rsub.add_parser("migrate", help="apply nexus.db schema migrations")
    migrate.add_argument("--backup", metavar="PATH", help="write a backup copy first")


async def _run_serve(config: Config) -> int:
    server = BridgeServer(config)
    map_server = MapWebServer(config, server.store, server) if config.map_enabled else None
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def _request_stop(*_: Any) -> None:
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:  # pragma: no cover - non-POSIX
            pass

    await server.start()
    if map_server is not None:
        await map_server.start()
    try:
        await stop.wait()
    finally:
        if map_server is not None:
            await map_server.stop()
        await server.stop()
    return 0


async def _run_map(config: Config, args: argparse.Namespace) -> int:
    store = LocationStore(config.db_path)
    map_server = MapWebServer(config, store)
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def _request_stop(*_: Any) -> None:
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:  # pragma: no cover
            pass

    await map_server.start()
    if getattr(args, "open", False):
        webbrowser.open(map_server.url)
    try:
        await stop.wait()
    finally:
        await map_server.stop()
        store.close()
    return 0


def _run_where(config: Config, args: argparse.Namespace) -> int:
    store = LocationStore(config.db_path)
    try:
        device = getattr(args, "device", None)
        if getattr(args, "geojson", False):
            print(json.dumps(store.export_geojson(device), indent=2))
            return 0
        if getattr(args, "gpx", False):
            print(store.export_gpx(device))
            return 0
        latest = store.latest(device)
        if latest is None:
            if getattr(args, "json", False):
                print(json.dumps({"latest": None, "history": []}))
            else:
                print("no GPS locations recorded yet")
            return 1
        history_count = int(getattr(args, "history", 0) or 0)
        history = store.history(device, limit=history_count) if history_count > 0 else []
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {
                        "latest": latest.to_dict(),
                        "history": [r.to_dict() for r in history],
                    },
                    indent=2,
                )
            )
            return 0
        when = dt.datetime.fromtimestamp(latest.timestamp).strftime("%Y-%m-%d %H:%M:%S")
        batt = f"{latest.battery}%" if latest.battery is not None else "n/a"
        if latest.charging:
            batt += " (charging)"
        print(f"device:      {latest.device}")
        print(f"coordinates: {latest.latitude:.6f}, {latest.longitude:.6f}")
        print(f"accuracy:    ±{latest.accuracy:.1f} m ({latest.provider})")
        print(f"battery:     {batt}")
        print(f"timestamp:   {when}")
        print(f"map:         {latest.osm_url}")
        if history:
            print(f"\nrecent fixes ({len(history)}):")
            for rec in history:
                ts = dt.datetime.fromtimestamp(rec.timestamp).strftime("%H:%M:%S")
                rb = f"{rec.battery}%" if rec.battery is not None else "n/a"
                print(
                    f"  {ts}  {rec.latitude:.6f}, {rec.longitude:.6f}  "
                    f"±{rec.accuracy:.1f}m  battery={rb}"
                )
        return 0
    finally:
        store.close()


async def _run_location(config: Config, args: argparse.Namespace) -> int:
    async with client_module.BridgeClient(config, device=args.device) as bridge:
        await bridge.location(
            args.latitude,
            args.longitude,
            accuracy=args.accuracy,
            battery=args.battery,
            charging=args.charging,
            speed=args.speed,
            altitude=args.altitude,
            provider=args.provider,
        )
        log.info("sent location: %.6f, %.6f", args.latitude, args.longitude)
    return 0


async def _run_locate(config: Config, args: argparse.Namespace) -> int:
    async with client_module.BridgeClient(config, device=args.device) as bridge:
        await bridge.locate()
        log.info("requested immediate GPS fix")
    return 0


async def _run_send(config: Config, args: argparse.Namespace) -> int:
    async with client_module.BridgeClient(config, device=args.device) as bridge:
        await bridge.notify(
            args.app,
            args.title,
            args.text,
            package=getattr(args, "package", ""),
        )
        log.info("sent notification")
    return 0


async def _run_call(config: Config, args: argparse.Namespace) -> int:
    from protocol import CallState

    async with client_module.BridgeClient(config, device=args.device) as bridge:
        await bridge.call(CallState.from_android(args.state), name=args.name)
        log.info("sent call event: %s", args.state)
    return 0


async def _run_ping(config: Config, args: argparse.Namespace) -> int:
    rtt = await client_module.ping(config, device=args.device)
    print(f"pong in {rtt:.1f} ms")
    return 0


async def _run_status(config: Config) -> int:
    print("effective configuration:")
    for key, value in config.to_public_dict().items():
        print(f"  {key} = {value!r}")
    store = LocationStore(config.db_path)
    try:
        total = store.count()
        latest = store.latest()
        print(f"stored locations: {total} ({config.db_path})")
        if latest is not None:
            print(
                f"latest fix: {latest.device} at {latest.latitude:.6f}, {latest.longitude:.6f} "
                f"(±{latest.accuracy:.1f}m, battery={latest.battery}%)"
            )
    finally:
        store.close()
    disc = discovery_module.TailscaleDiscovery(config.tailscale_command)
    if not disc.available():
        print("tailscale CLI not found; skipping peer discovery")
        return 0
    try:
        status = await disc.status()
    except discovery_module.DiscoveryError as exc:
        print(f"tailscale status unavailable: {exc}")
        return 1
    if status.self_peer:
        print(f"self: {status.self_peer.hostname} {', '.join(status.self_peer.addresses)}")
    for peer in status.online_peers():
        marker = " <-- phone" if config.phone_name and peer.matches(config.phone_name) else ""
        addresses = ", ".join(peer.addresses) or "no address"
        print(f"  {peer.hostname} [{peer.os}] {addresses}{marker}")
    return 0


def _parse_param(raw: str) -> tuple[str, Any]:
    if "=" not in raw:
        raise ValueError(f"--param must be KEY=VALUE, got {raw!r}")
    key, value = raw.split("=", 1)
    try:
        parsed: Any = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    return key.strip(), parsed


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "-"
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _run_remote(config: Config, args: argparse.Namespace) -> int:
    from remote_registry import OperationContext, ParamError, OperationError, default_registry
    from remote_store import RemoteStore, StoreError

    cmd = args.remote_command
    registry = default_registry()

    if cmd == "ops":
        ops = [op.describe() for op in registry.all()]
        if args.json:
            print(json.dumps(ops, indent=2))
            return 0
        for op in ops:
            approval = "approval" if op["requires_approval"] else "no approval"
            print(f"{op['id']:<18} {op['permission']:<15} {op['risk']}  {approval:<11} {op['description']}")
            for pname, spec in op["params"].items():
                extra = {k: v for k, v in spec.items() if k not in {"type", "required"}}
                req = "required" if spec["required"] else "optional"
                print(f"{'':20}- {pname} ({spec['type']}, {req}) {extra if extra else ''}")
        return 0

    if cmd == "run":
        try:
            op = registry.get(args.operation)
            params = op.validate_params(dict(_parse_param(p) for p in args.param))
            if op.requires_approval:
                print("operations that require approval cannot be run from this command", file=sys.stderr)
                return 2
            result = op.handler(
                params,
                OperationContext(
                    device_name=config.device_name,
                    disk_paths=tuple(config.remote_disks.items()),
                    max_output_bytes=op.max_output_bytes,
                ),
            )
        except (ParamError, OperationError, LookupError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2))
        return 0

    store = RemoteStore(config.remote_db_path)
    try:
        if cmd == "pair":
            record, credential = store.pair(args.name, tuple(args.grant))
            perms = sorted(store.permissions(record.device_id))
            if args.json:
                print(json.dumps({"device_id": record.device_id, "credential": credential, "permissions": perms}))
                return 0
            print(f"paired device:  {record.name}")
            print(f"device id:      {record.device_id}")
            print(f"permissions:    {', '.join(perms) or '(none - grant with `phone-bridge remote grant`)'}")
            print()
            print("Device credential (shown ONCE - paste it into the Android app, Settings > Nexus credential):")
            print()
            print(f"    {credential}")
            print()
            print("Anyone with this credential can act as this device. Revoke with:")
            print(f"    phone-bridge remote revoke {record.device_id}")
            return 0
        if cmd == "devices":
            rows = []
            for rec in store.devices():
                row = rec.to_public_dict()
                row["permissions"] = sorted(store.permissions(rec.device_id))
                rows.append(row)
            if args.json:
                print(json.dumps(rows, indent=2))
                return 0
            if not rows:
                print("no paired devices; run `phone-bridge remote pair <name>`")
                return 0
            for row in rows:
                state = "REVOKED" if row["revoked_at"] else "active"
                print(
                    f"{row['device_id']}  {row['name']:<20} {state:<8} "
                    f"perms={','.join(row['permissions']) or '-'}  last_seen={_fmt_ts(row['last_seen_at'])}"
                )
            return 0
        if cmd == "revoke":
            if store.revoke(args.device_id):
                print(f"revoked {args.device_id}; active sessions close within 5 seconds")
                return 0
            print(f"no active device {args.device_id}", file=sys.stderr)
            return 1
        if cmd == "revoke-all":
            count = store.revoke_all()
            print(f"revoked {count} device(s)")
            return 0
        if cmd == "rotate":
            credential = store.rotate(args.device_id)
            print("New credential (shown ONCE; the old one no longer works):")
            print(f"    {credential}")
            return 0
        if cmd == "grant":
            store.grant(args.device_id, args.permission)
            print(f"granted {args.permission} to {args.device_id}")
            return 0
        if cmd == "ungrant":
            removed = store.ungrant(args.device_id, args.permission)
            print(("removed " if removed else "not granted: ") + f"{args.permission} on {args.device_id}")
            return 0 if removed else 1
        if cmd == "audit":
            rows = store.audit(limit=args.limit, device_id=args.audit_device)
            if args.json:
                print(json.dumps(rows, indent=2))
                return 0
            if not rows:
                print("no remote audit records")
            for row in rows:
                err = f" error={row['error_code']}" if row["error_code"] else ""
                trunc = " truncated" if row["truncated"] else ""
                print(
                    f"{_fmt_ts(row['finished_at'])}  {row['device_id']:<20} {row['operation']:<16} "
                    f"{row['decision']:<8} {row['status']:<9} approval={row['approval_state']}"
                    f"{err}{trunc} ({row['duration_ms'] if row['duration_ms'] is not None else '-'} ms)"
                )
            return 0
        if cmd == "approvals":
            pending = store.pending_approvals()
            if not pending:
                print("no pending approvals")
            for rec in pending:
                device = store.device(rec.device_id)
                name = device.name if device else rec.device_id
                remaining = max(0, int(rec.expires_at - dt.datetime.now().timestamp()))
                print(f"{rec.approval_id}  {name:<20} {rec.operation:<16} expires in {remaining}s")
            return 0
        if cmd in {"approve", "deny"}:
            state = store.decide(args.approval_id, approve=cmd == "approve")
            print(f"{args.approval_id}: {state}")
            expected = "approved" if cmd == "approve" else "denied"
            return 0 if state == expected else 1
        if cmd == "migrate":
            if args.backup:
                print(f"backup written to {store.backup(args.backup)}")
            version = store.migrate()
            print(f"nexus.db schema version {version} ({store.path})")
            return 0
    except StoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "serve"

    try:
        config = load_config(path=args.config, overrides=_config_overrides(args))
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    _setup_logging(args.log_level or config.log_level)

    try:
        if command == "serve":
            return asyncio.run(_run_serve(config))
        if command == "map":
            return asyncio.run(_run_map(config, args))
        if command == "where":
            return _run_where(config, args)
        if command == "location":
            return asyncio.run(_run_location(config, args))
        if command == "locate":
            return asyncio.run(_run_locate(config, args))
        if command == "send":
            return asyncio.run(_run_send(config, args))
        if command == "call":
            return asyncio.run(_run_call(config, args))
        if command == "simulate":
            return asyncio.run(client_module.simulate(config, device=args.device)) or 0
        if command == "ping":
            return asyncio.run(_run_ping(config, args))
        if command == "status":
            return asyncio.run(_run_status(config))
        if command == "token":
            print(config_module.generate_token())
            return 0
        if command == "remote":
            return _run_remote(config, args)
        if command == "init-config":
            token = args.token or config_module.generate_token()
            path = write_default_config(args.path, Config(token=token))
            print(f"wrote {path}")
            print(f"token: {token}")
            return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        log.error("%s", exc)
        return 1

    parser.error(f"unknown command: {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
