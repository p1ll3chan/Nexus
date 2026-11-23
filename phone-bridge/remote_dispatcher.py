"""Request dispatcher for Nexus Remote.

Implements the pipeline in docs/architecture/NEXUS_REMOTE_DESIGN.md §5.  Every
path ends in exactly one ``remote.response`` and one audit row.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable

import remote_protocol as rp
from remote_protocol import EventState, RemoteProtocolError, RemoteRequest, RemoteResponse, Status
from remote_registry import (
    OperationContext,
    OperationError,
    OperationRegistry,
    ParamError,
    RegistryError,
)
from remote_store import ApprovalRecord, RemoteStore

log = logging.getLogger("phone-bridge.remote")

Send = Callable[[dict[str, Any]], Awaitable[bool]]


@dataclass(frozen=True)
class RemotePrincipal:
    """An authenticated device identity bound to one WebSocket session."""

    device_id: str
    name: str
    secret_hash: str
    authenticated_at: float = field(default_factory=time.time)


@dataclass
class DispatcherLimits:
    per_device_concurrency: int = 4
    global_concurrency: int = 8
    rate_limit: int = 20
    rate_window: float = 10.0
    replay_ttl: float = 600.0
    replay_cache_size: int = 1024
    clock_skew: float = 300.0
    approval_ttl: float = 60.0
    approval_poll: float = 0.5


class RemoteDispatcher:
    def __init__(
        self,
        registry: OperationRegistry,
        store: RemoteStore,
        context: OperationContext,
        *,
        limits: DispatcherLimits | None = None,
        on_approval_needed: Callable[[ApprovalRecord, RemotePrincipal], Awaitable[None]] | None = None,
    ) -> None:
        self.registry = registry
        self.store = store
        self.context = context
        self.limits = limits or DispatcherLimits()
        self.on_approval_needed = on_approval_needed
        self._seen: dict[str, OrderedDict[str, float]] = defaultdict(OrderedDict)
        self._rate: dict[str, deque[float]] = defaultdict(deque)
        self._inflight: dict[str, int] = defaultdict(int)
        self._inflight_total = 0

    # ------------------------------------------------------------------ capabilities

    def capabilities(self, principal: RemotePrincipal | None, server: str) -> dict[str, Any]:
        if principal is None:
            return rp.capabilities(
                available=False,
                server=server,
                reason="legacy_auth: pair this device with `phone-bridge remote pair` to use Remote",
            )
        perms = self.store.permissions(principal.device_id)
        ops = [op.describe() for op in self.registry.available_for(perms)]
        return rp.capabilities(
            available=True,
            server=server,
            device_id=principal.device_id,
            permissions=sorted(perms),
            operations=ops,
        )

    # ------------------------------------------------------------------ entry point

    async def handle(
        self,
        data: dict[str, Any],
        raw_size: int,
        principal: RemotePrincipal | None,
        send: Send,
        on_revoked: Callable[[], Awaitable[None]] | None = None,
    ) -> RemoteResponse:
        rid = data.get("request_id") if isinstance(data.get("request_id"), str) else ""
        rid = rid if rp.REQUEST_ID_RE.match(rid or "") else ""
        device_id = principal.device_id if principal else "legacy"
        audit: dict[str, Any] = {"operation": str(data.get("operation", "?"))[:64]}

        async def finish(resp: RemoteResponse, decision: str, **extra: Any) -> RemoteResponse:
            await send(resp.to_dict())
            fields = {**audit, **extra}
            try:
                self.store.record_audit(
                    request_id=resp.request_id or "-",
                    device_id=device_id,
                    decision=decision,
                    status=resp.status.value,
                    error_code=resp.error_code,
                    truncated=resp.truncated,
                    started_at=resp.started_at,
                    finished_at=resp.finished_at,
                    **fields,
                )
            except Exception:  # pragma: no cover - audit failure must not crash the session
                log.exception("failed to write remote audit row")
            return resp

        def reject(code: str, message: str) -> RemoteResponse:
            return rp.rejected(rid, code, message, audit["operation"])

        # 1. authenticated device principal (never a legacy shared-token session)
        if principal is None:
            return await finish(
                reject("not_authorized", "remote operations require a paired device credential"),
                "denied",
                reason="legacy_session",
            )

        # 2. strict schema
        if data.get("type") not in rp.INBOUND_REMOTE_TYPES:
            return await finish(
                reject("invalid_request", "devices may only send remote.request"),
                "rejected",
                reason="bad_type",
            )
        try:
            req = RemoteRequest.parse(data, raw_size)
        except RemoteProtocolError as exc:
            return await finish(
                rp.rejected(exc.request_id or rid, exc.code, str(exc), audit["operation"]),
                "rejected",
                reason="schema",
            )
        audit["operation"] = req.operation
        audit["params_hash"] = rp.params_hash(req.params)
        if req.device_id and req.device_id != principal.device_id:
            return await finish(
                reject("invalid_request", "device_id does not match the authenticated device"),
                "rejected",
                reason="device_mismatch",
            )

        # 3. freshness + replay
        now = time.time()
        if abs(now - req.ts) > self.limits.clock_skew:
            return await finish(
                RemoteResponse(
                    rid,
                    Status.EXPIRED,
                    req.operation,
                    error_code="expired",
                    error_message="request timestamp outside the accepted window (check phone clock)",
                ),
                "rejected",
                reason="stale",
            )
        if not self._remember(principal.device_id, rid, now):
            return await finish(
                reject("duplicate_request", "request_id was already used"),
                "rejected",
                reason="replay",
            )

        # 4. rate limit
        if not self._rate_ok(principal.device_id, now):
            return await finish(
                reject("rate_limited", "too many remote requests; slow down"),
                "rejected",
                reason="rate",
            )

        # 5. registry (fail closed)
        try:
            op = self.registry.get(req.operation)
        except RegistryError as exc:
            return await finish(reject(exc.code, str(exc)), "rejected", reason=exc.code)

        # 6. fresh identity check (revocation / rotation)
        if not self.store.session_valid(principal.device_id, principal.secret_hash):
            resp = await finish(
                reject("device_revoked", "this device credential was revoked or rotated"),
                "denied",
                reason="revoked",
            )
            if on_revoked is not None:
                await on_revoked()
            return resp

        # 7. permission (server-side, fresh, default deny)
        if op.permission not in self.store.permissions(principal.device_id):
            return await finish(
                reject("permission_denied", f"device lacks permission {op.permission}"),
                "denied",
                reason="permission",
            )

        # 8. parameters
        try:
            params = op.validate_params(req.params)
        except ParamError as exc:
            return await finish(reject("invalid_params", str(exc)), "rejected", reason="params")

        # 9. concurrency
        if (
            self._inflight[principal.device_id] >= self.limits.per_device_concurrency
            or self._inflight_total >= self.limits.global_concurrency
        ):
            return await finish(
                reject("busy", "too many concurrent remote requests"), "rejected", reason="busy"
            )
        self._acquire(principal.device_id)
        released = False
        worker: asyncio.Future[Any] | None = None

        def release(*_: Any) -> None:
            nonlocal released
            if not released:
                released = True
                self._release(principal.device_id)

        try:
            await send(rp.event(rid, EventState.ACCEPTED, operation=op.id))
            self.store.touch(principal.device_id)

            # 10. approval (bound, single-use, fail-closed)
            approval_state = "not_required"
            if op.requires_approval:
                outcome = await self._await_approval(req, op.id, principal, send)
                approval_state = outcome
                if outcome != "approved":
                    code = "approval_expired" if outcome == "expired" else "approval_denied"
                    return await finish(
                        reject(code, f"approval {outcome}"),
                        "denied",
                        reason="approval",
                        approval_state=outcome,
                    )

            # 11. execute with timeout
            started = time.time()
            await send(rp.event(rid, EventState.RUNNING, operation=op.id))
            ctx = replace(self.context, max_output_bytes=op.max_output_bytes)
            worker = asyncio.ensure_future(asyncio.to_thread(op.handler, params, ctx))
            worker.add_done_callback(release)  # slot held until the thread really ends
            try:
                result = await asyncio.wait_for(asyncio.shield(worker), op.timeout)
            except asyncio.TimeoutError:
                return await finish(
                    RemoteResponse(
                        rid,
                        Status.FAILED,
                        op.id,
                        error_code="timeout",
                        error_message=f"operation exceeded {op.timeout:.0f}s",
                        started_at=started,
                    ),
                    "allowed",
                    approval_state=approval_state,
                )
            except OperationError as exc:
                return await finish(
                    RemoteResponse(
                        rid,
                        Status.FAILED,
                        op.id,
                        error_code=exc.code,
                        error_message=str(exc),
                        started_at=started,
                    ),
                    "allowed",
                    approval_state=approval_state,
                )
            except Exception:
                log.exception("remote operation %s crashed", op.id)
                return await finish(
                    RemoteResponse(
                        rid,
                        Status.FAILED,
                        op.id,
                        error_code="operation_error",
                        error_message="operation failed",
                        started_at=started,
                    ),
                    "allowed",
                    approval_state=approval_state,
                )

            # 12. output cap
            encoded = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
            if len(encoded.encode("utf-8")) > op.max_output_bytes:
                return await finish(
                    RemoteResponse(
                        rid,
                        Status.FAILED,
                        op.id,
                        error_code="output_too_large",
                        error_message="result exceeded the operation output cap",
                        started_at=started,
                    ),
                    "allowed",
                    approval_state=approval_state,
                )
            truncated = bool(isinstance(result, dict) and result.get("truncated"))
            return await finish(
                RemoteResponse(
                    rid,
                    Status.COMPLETED,
                    op.id,
                    result=result,
                    truncated=truncated,
                    started_at=started,
                ),
                "allowed",
                approval_state=approval_state,
            )
        finally:
            if worker is None:
                release()

    # ------------------------------------------------------------------ helpers

    def _remember(self, device_id: str, request_id: str, now: float) -> bool:
        seen = self._seen[device_id]
        cutoff = now - self.limits.replay_ttl
        while seen:
            oldest_id, oldest_ts = next(iter(seen.items()))
            if oldest_ts >= cutoff:
                break
            seen.pop(oldest_id)
        if request_id in seen:
            return False
        seen[request_id] = now
        while len(seen) > self.limits.replay_cache_size:
            seen.popitem(last=False)
        return True

    def _rate_ok(self, device_id: str, now: float) -> bool:
        bucket = self._rate[device_id]
        while bucket and bucket[0] < now - self.limits.rate_window:
            bucket.popleft()
        if len(bucket) >= self.limits.rate_limit:
            return False
        bucket.append(now)
        return True

    def _acquire(self, device_id: str) -> None:
        self._inflight[device_id] += 1
        self._inflight_total += 1

    def _release(self, device_id: str) -> None:
        self._inflight[device_id] = max(0, self._inflight[device_id] - 1)
        self._inflight_total = max(0, self._inflight_total - 1)

    def inflight(self, device_id: str | None = None) -> int:
        return self._inflight[device_id] if device_id else self._inflight_total

    async def _await_approval(
        self,
        req: RemoteRequest,
        operation: str,
        principal: RemotePrincipal,
        send: Send,
    ) -> str:
        phash = rp.params_hash(req.params)
        record = self.store.create_approval(
            request_id=req.request_id,
            device_id=principal.device_id,
            operation=operation,
            params_hash=phash,
            ttl=self.limits.approval_ttl,
        )
        await send(
            rp.approval_required(req.request_id, record.approval_id, operation, record.expires_at)
        )
        await send(
            rp.event(req.request_id, EventState.AWAITING_APPROVAL, approval_id=record.approval_id)
        )
        if self.on_approval_needed is not None:
            try:
                await self.on_approval_needed(record, principal)
            except Exception:  # pragma: no cover - prompt is best effort; still fail closed
                log.exception("approval prompt failed")
        try:
            while True:
                current = self.store.approval(record.approval_id)
                if current is None:
                    outcome = "denied"
                    break
                if current.state == "approved":
                    ok = self.store.consume_approval(
                        record.approval_id,
                        request_id=req.request_id,
                        device_id=principal.device_id,
                        operation=operation,
                        params_hash=phash,
                    )
                    outcome = "approved" if ok else "denied"
                    break
                if current.state in ("denied", "cancelled", "consumed"):
                    outcome = "denied"
                    break
                if current.state == "expired" or time.time() >= current.expires_at:
                    self.store.expire_approvals()
                    outcome = "expired"
                    break
                await asyncio.sleep(self.limits.approval_poll)
        except asyncio.CancelledError:
            self.store.cancel_approval(record.approval_id)
            raise
        result = {
            "approved": rp.ApprovalOutcome.APPROVED,
            "expired": rp.ApprovalOutcome.EXPIRED,
        }.get(outcome, rp.ApprovalOutcome.DENIED)
        await send(rp.approval_result(req.request_id, record.approval_id, result))
        return outcome
