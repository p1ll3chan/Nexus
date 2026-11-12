# PhoneBridge Message Flow — Independent Architecture Review

**Review date:** 2026-11-12<br>
**Document reviewed:** `docs/architecture/PHONEBRIDGE_MESSAGE_FLOW.md`<br>
**Source reviewed:** Android Kotlin message/runtime/service code and Python protocol/server/storage/dashboard code in this checkout.<br>
**Scope:** Static source review only. No source code or the original architecture document was changed; no dependencies were installed.

## 1. Executive Verdict

The document captures most of the main event path and names the correct components, but it is **not fully accurate as a lifecycle/guarantee document**. In particular:

1. The Mermaid diagram incorrectly shows an application `ack` for a dashboard-originated `locate`; the implementation sends no such WebSocket acknowledgement, and Android discards the locate ID.
2. It incorrectly places the first `openConnection()` call on the service's `Dispatchers.Default` scope. `ConnectionManager.start()` calls it synchronously on its caller (normally `BridgeService.onCreate()` on the main thread); only reconnect delays run in the supplied scope.
3. It understates a definite event-loss path: the Android shared flows have `replay = 0`. When they have no subscribers, `tryEmit()` can return true while the value is discarded; `extraBufferCapacity` does not preserve events in that case. Events may be published before `ConnectionManager`'s collector or the locate collector starts.
4. The prose overstates backward compatibility for future remote message types. Current unknown-frame handling differs between Android and Python, and neither side negotiates capabilities or validates the protocol version.
5. The document does not clearly state that `WebSocket.send()`/Android `send()` success is not an application-level delivery acknowledgement, and that acknowledged IDs are not tracked for retry or deduplication.

**Conclusion:** The message-flow document is a useful orientation guide but should not be treated as a reliable delivery-contract or protocol-extension specification without the corrections and decisions below. The existing architecture is sufficiently understood to prepare Day 3, but **do not implement remote operations based solely on the current extension section**.

## 2. Verified Claims

These documented paths and structures match the inspected source, with qualifications noted later:

- Android notification events are gathered by `NotificationBridgeListener.onNotificationPosted()` and published through `BridgeRuntime.publish()` (`NotificationBridgeListener.kt:16-64`; `BridgeRuntime.kt:31-53`).
- Calls are emitted by `CallMonitor.handle()` after telephony state changes (`CallMonitor.kt:25-53, 76-88`). The API 31+ callback passes an empty number; older `PhoneStateListener` passes its `phoneNumber` argument.
- GPS and battery fields originate in `LocationMonitor.emitLocation()` / `readBattery()` (`LocationMonitor.kt:161-218`). Regular location updates are registered with the main looper (`:69-100`); an immediate request uses `getCurrentLocation()` on API 30+ or a last-known fix (`:117-158`).
- `MessageProtocol.kt` serializes `hello`, `notification`, `call`, `location`, `ping`, and `pong`; its inbound parser recognizes `hello_ack`, `ack`, `ping`, `pong`, `locate`, and `error`, returning `Unknown` for invalid/unsupported types (`MessageProtocol.kt:13-102, 124-193`). The documented JSON field sets are substantially correct for example frames.
- The WebSocket server accepts a first `hello` frame, applies the configured device allow-list and shared-token check, responds with `hello_ack`, then stores the session and reads events (`server.py:138-224`). Authentication uses constant-time comparison and per-source failure limits (`authentication.py:29-66`).
- Python `protocol.loads()` requires a JSON object with a string `type`; `parse_event()` validates/converts the known inbound types (`protocol.py:186-207, 452-470`). Location coordinates are range checked (`:320-353`).
- Notification, call, and location paths invoke their respective server handlers and issue `ack` frames on the handled/disabled/filter branches (`server.py:226-367`). Only location events are stored in SQLite (`storage.py:109-134, 155-208`); notification/call content is not stored by `LocationStore`.
- Location persistence is invoked with `asyncio.to_thread()` and protected by a `threading.Lock`; the SQLite connection uses WAL mode for non-memory paths (`server.py:332-367`; `storage.py:90-107, 155-192`).
- The server's `_broadcast()` publishes to bounded per-subscriber queues and drops an old queued event when full (`server.py:111-125`). SSE writes queued event JSON and 15-second keepalives; it unsubscribes in `finally` (`web.py:776-801`).
- Dashboard locate requests call `BridgeServer.request_location(device)`, which creates a `locate` frame and sends it to matching active sessions (or all sessions when the device filter is empty) (`web.py:710-715`; `server.py:127-136`). Android parses the frame and emits a `Unit` locate request; `BridgeService` collects it on `Dispatchers.Main` and requests a fix (`MessageProtocol.kt:93-96`; `ConnectionManager.kt:160-163`; `BridgeRuntime.kt:39-54`; `BridgeService.kt:52-55`).
- Android's reconnect backoff is 5–60 seconds plus up to one second jitter; it resets `attempt` after `hello_ack`, and pending queue flush begins after that frame (`ConnectionManager.kt:109-131, 143-151`). The configured OkHttp ping interval and timeout values are also accurately listed (`:31-37`).

## 3. Incorrect or Misleading Documentation Claims

| Document location | Claim / issue | Source evidence and correction |
|---|---|---|
| Mermaid lines 101–115, especially 114 | A dashboard locate request leads to `ack(locate.id, ok=True, requested=N)` from Python to Android. | **Incorrect.** `request_location()` sends a `locate` frame and returns a count (`server.py:127-136`). The HTTP handler returns that count to the browser (`web.py:710-715`). It does not send an `ack` frame. The `ack(event.id, requested=...)` branch at `server.py:247-252` only handles a `locate` frame received from a client (the opposite direction). Android turns the incoming request into `Unit` (`BridgeRuntime.kt:39-54`) and discards its ID; its eventual location has a newly generated ID. |
| Mermaid line 36; table 3.2 row 3 | Initial `openConnection()` runs on the `Dispatchers.Default` scope. | **Incorrect.** `ConnectionManager.start()` calls `scope.launch(Dispatchers.IO)` for the event collector, then directly calls `openConnection()` (`ConnectionManager.kt:53-61`). `BridgeService.onCreate()` calls `manager.start()` (`BridgeService.kt:38-47`), normally on Android's main thread. `openConnection()` after a reconnect is invoked by a `scope.launch { delay(...); openConnection() }` (`ConnectionManager.kt:109-121`). Distinguish initial connection setup from reconnect setup.
| Diagram lines 86–87 | Notification handler returns “desktop notification shown.” | **Too strong.** The handler awaits a `NotificationService.notify()` attempt, but `notify()` can return `None` for ignored notifications or a backend may fail and log/return without display (`notifications.py:330-360, 141-168, 230-248`). The server can still send `ack(ok=True)` after the attempt. Label this as “notification delivery attempted” rather than asserting a user-visible display.
| Diagram line 97 / sections 3.4 and 5 | Acknowledgements are mentioned but their guarantee is not bounded. | An Android `ack` only logs rejection details; it does not resolve a pending event or cause retry (`ConnectionManager.kt:165-169`). Server `ack(ok=True)` means the corresponding handler reached its ack branch, not that a notification was actually shown or that the phone retained a durable delivery record. `websockets.send()` success is not proof that the peer processed the frame (`server.py:399-404`; `ConnectionManager.kt:80-91`). State this explicitly.
| Section 3.1 row 2 / section 5 row 10 | `tryEmit()` failure is presented as the principal Android flow loss case. | **Incomplete and misleading.** Both shared flows use `replay = 0` (`BridgeRuntime.kt:33-43`). With no subscribers, emissions are not retained for a future subscriber and `tryEmit()` may return true. The extra buffer only helps when subscribers exist but are behind. Therefore publish-before-collector is a silent-loss path, not just a “buffer full” case.
| Section 3.2 row 6 | `retryOnConnectionFailure(true)` appears alongside application WebSocket reconnect behavior without a clear boundary. | That OkHttp setting is not the PhoneBridge message retry mechanism. The app-level reconnect is `scheduleReconnect()`; already-accepted WebSocket sends are not re-enqueued based on server `ack`. Describe the OkHttp setting as connection-establishment behavior and avoid implying reliable frame retransmission.
| Section 3.6 rows 27–28 | Unknown/malformed inbound frames are simply logged; locate requests use a buffered flow. | Parsing returns `Unknown` for malformed JSON / unrecognized type and the callback logs only its type (`MessageProtocol.kt:78-102`; `ConnectionManager.kt:180`). For `locate`, `requestImmediateLocation()` returns a Boolean but the caller ignores it (`ConnectionManager.kt:160-163`). Also, zero subscribers means no replay, so a successful `tryEmit` can still lose a request if the service collector is not yet active.
| Section 7, paragraph “Why this is the safest seam,” lines 403–406 | Existing authentication and unknown-type behavior make `remote.*` backward-compatible; capability negotiation can be added later. | **Not established.** Python rejects unknown message types with `ProtocolError` (`protocol.py:202-207, 470`; `server.py:229-235`), sends an `error`, and keeps the socket open. Android ignores unknown inbound frames (`MessageProtocol.kt:101-102`; `ConnectionManager.kt:180`). This permits transport continuity but means one side's remote request/response can be silently unhandled or rejected. `Hello.version` is parsed but not checked (`protocol.py:217-224`; `server.py:194-218`), and Android parses but does not compare `hello_ack.version` (`MessageProtocol.kt:82-85`; `ConnectionManager.kt:143-151`). Capability negotiation cannot be postponed if feature correctness depends on peer support.
| Section 8 unknowns 1, 10, 13, 14 | Several code-defined behaviours are listed as unknown. | Their *desired policy* may be unknown, but implementation behaviour is inspectable: (1) zero-subscriber SharedFlow loss is defined; (10) disconnected sessions are not queued for locate and `request_location()` returns 0 when no send succeeds; (13) current URL builder always returns `ws://` and strips `wss://` (`Prefs.kt:21-29`); (14) no redaction is applied in the event path and server logs notification/call/location details (`server.py:262-267, 303-306, 342-349`). Reclassify each as “known current behavior; desired policy/open decision unknown.” |

### Additional claim qualifications

- **Android callback threads:** The message-flow document labels `NotificationListenerService.onNotificationPosted()` as a binder thread and the pre-API-31 telephony callback as a listener thread. Those exact thread labels are not established by app code. `CallMonitor` explicitly uses `context.mainExecutor` only for API 31+ and otherwise registers `PhoneStateListener` without specifying an executor/handler (`CallMonitor.kt:25-45`); `NotificationBridgeListener` also does not specify one. State framework callback context as unknown unless verified on supported Android versions.
- **Malformed host settings:** The document says invalid host/port/token are rejected by `BridgeSettings.isConfigured()`. That method checks host/token nonblank and port range only (`Prefs.kt:18-19`); host syntax is not validated. `ConnectionManager.openConnection()` builds the URL without catching URL-construction exceptions (`ConnectionManager.kt:94-106`), so malformed nonblank hosts may fail differently.

## 4. Missing Architectural Details

### Delivery, acknowledgement, and duplicates

- **No durable Android outbox.** `BridgeRuntime` flows and `ConnectionManager.pending` are process-memory only; `ConnectionManager.stop()` clears pending events (`ConnectionManager.kt:63-73`). Process death loses queued events.
- **Not exactly-once or at-least-once delivery.** An event is removed from the pending queue when `WebSocket.send()` returns true, not when its `ack` returns. If the connection fails after that enqueue but before server processing, the client does not replay based on the ack. Conversely, any future replay could duplicate side effects because the server has no event-ID deduplication; location `event_id` is not declared unique (`storage.py:112-127`), and notifications/calls have no persistence/deduplication layer.
- **Acknowledgements have branch-specific meaning.** For notification/call/location, `ack.ref` is the event ID and `ok` may be false when that feature is disabled; an ignored notification can be `ok=true, detail="ignored"` (`server.py:269-295, 307-330, 350-367`). Android logs only `ok=false`; it does not match `ref` to pending items or retry them (`ConnectionManager.kt:165-169`).
- **Locate request is fire-and-forget at the app protocol.** Python responds to the HTTP request with a count of WebSocket sends. There is no locate completion ack over WebSocket, no request-ID propagation through `BridgeRuntime.locateRequests` (`Unit` only), and no way to correlate the resulting location frame to that locate request. The client receives a location event ack only for the new location event.

### Startup, buffering, and lifecycle

- `BridgeService.onCreate()` starts the manager before it launches the `locateRequests` collector; the collector starts asynchronously in the service scope (`BridgeService.kt:38-55`). A very early server `locate` can be lost because the locate flow has no replay.
- Notification listener publishes before it calls `BridgeService.ensureRunning()` (`NotificationBridgeListener.kt:56-64`). If the service/collector is not already running, that event is not held by the zero-replay flow, even though `tryEmit()` may return true. `MainActivity.sendTestNotification()` similarly calls `BridgeService.start()` and immediately publishes test events (`MainActivity.kt:155-185`); service startup is asynchronous, so those emissions are not guaranteed to reach a collector.
- While the collector is subscribed and WebSocket is not authenticated/open, `ConnectionManager.pending` queues at most 200 events. It drops the oldest item on overflow and increments the generic `notificationsSkipped` statistic regardless of the dropped event's type (`ConnectionManager.kt:80-91`).
- `ConnectionManager.start()` launches its collector in `Dispatchers.IO`, but the initial `openConnection()` call is direct, as noted above. `BridgeService`'s provided scope is `SupervisorJob() + Dispatchers.Default` (`BridgeService.kt:33`).

### Ordering and concurrency

- A single `BridgeServer._read_loop()` processes a connection's incoming frames sequentially and awaits each handler (`server.py:226-252`). Different authenticated connections have independent handler tasks and can interleave; there is no global ordering across devices.
- The Android pending queue preserves FIFO order among queued events, but pending flush (`flushPending`) and live collector sends can run on different threads at reconnection time (`ConnectionManager.kt:57-60, 80-91, 124-131, 143-151`). The document correctly labels exact interleaving unverified; do not claim global event ordering.
- The server session map and SSE listener set are used from the asyncio loop. Location `record()` is offloaded to a thread and protected by a lock, but session setup calls `self.store.latest()` synchronously in `handler()` (`server.py:145`) and map API read/query/export functions call `LocationStore` synchronously from HTTP tasks (`web.py:751-774`). Those reads can block the event loop during disk I/O.
- On graceful WebSocket close, both `onClosing()` and `onClosed()` call `scheduleReconnect()` (`ConnectionManager.kt:184-191`). This can increment the attempt counter and cancel/reschedule the reconnect job twice for one close. Verify behavior against the pinned OkHttp lifecycle and make reconnect scheduling idempotent per socket.
- Callback handlers do not check whether the callback's `WebSocket` is still the current `webSocket` field (`ConnectionManager.kt:134-201`). Late callbacks from an old socket could potentially alter shared open/retry state after a replacement socket is active; this is a race hypothesis requiring a deterministic test, not a confirmed production failure.

### Persistence and dashboard

- Only GPS/location telemetry is persisted in `LocationStore`; notification and call events are sent to desktop notification backends and included in in-memory dashboard broadcasts, but are not stored by this SQLite schema.
- `_handle_notification()` and `_handle_call()` broadcast to SSE subscribers, but browser JavaScript only reacts to `location` and `session` events (`web.py:581-593`). It does not render/refresh for `notification` or `call`; describe those as server-side SSE broadcasts, not current dashboard UI features.
- SSE queues are bounded to 128 and discard the oldest event when full (`server.py:111-125`). The SSE event stream itself is not durable and has no cursor/replay; browser `EventSource` reconnect cannot recover a dropped or disconnected interval from the queue.
- In the locate diagram, the eventual `location` path stores the point before broadcasting and then sends an ack for the location's own event ID. The original locate request is not persisted and is not correlated to that point.

### Protocol compatibility

- Python `Hello.from_dict()` defaults `version` but `_handshake()` does not enforce it; Android accepts the server's version field but does not reject an unsupported version. The protocol version is informational in current code.
- Android `MessageProtocol.parse()` treats unrecognized types as `Unknown` (ignored); Python `protocol.message_type()` raises `ProtocolError` for an unknown type. There is no symmetric unknown-message policy.
- Python `parse_event()` includes `Locate` in the phone-to-server inbound type set. A client-originated `locate` causes the server to issue location requests to all connected sessions and ack the originating client's locate ID (`server.py:247-252`). This direction is separate from dashboard-originated server-to-phone locate.

## 5. Reliability Issues to Track

These are implementation-derived risks, not assumptions about a live deployment:

1. **Silent loss before subscriber activation:** notifications/test events can be published while no `BridgeRuntime.events` collector exists; zero replay discards them.
2. **Loss after WebSocket send acceptance:** no ack-based outbox or resend; server may not process a frame the client already removed from its pending queue.
3. **Duplicate side effects if retry is added naively:** server does not deduplicate event IDs; notification and call handlers can repeat desktop notifications.
4. **Locate result not correlated:** ID is discarded on Android and result uses a separate location ID; HTTP “requested” means frames were queued/sent, not that a fix arrived.
5. **Close callback duplicate reconnect scheduling:** graceful `onClosing` and `onClosed` both schedule a retry.
6. **Cross-thread pending/live send ordering:** exact ordering during queue flush is unverified.
7. **SSE gap loss:** bounded queues, no replay/cursor, and dropped oldest event mean the UI can miss state transitions until it next refreshes/receives a later event.
8. **Event-loop blocking on SQLite reads:** synchronous history/status/export queries can delay other server coroutines under disk contention or larger histories.
9. **No cross-device ordering/serialization:** independent session tasks run concurrently; duplicate device names also merge records in device-keyed queries.
10. **Handler failure closes the client session:** `ProtocolError` is recoverable, but unexpected exceptions from parsing, notifier/storage operations propagate out of `_read_loop()` and terminate the handler (`server.py:162-165, 226-235`).

## 6. Review of the 14 Documented Unknowns

| # | Review |
|---:|---|
| 1 | **Corrected:** buffer capacity and zero-subscriber behavior are defined by `MutableSharedFlow` semantics. With `replay=0`, no-subscriber events are discarded; `tryEmit()` may still return true. Whether the configured capacity is adequate under load remains an open product/performance question. |
| 2 | **Valid policy question:** implementation definitively drops the oldest of 200 queued events, across event types, and increments `notificationsSkipped` even for a call/location. Desired priority/coalescing policy is unknown. |
| 3 | **Over-specific speculation:** no demonstrated `open=true`/null field race from the call sequence; callbacks use their socket argument while the field is volatile. A more grounded unverified concern is stale callback handling because there is no current-socket identity check. |
| 4 | **Verified, not unknown:** location updates are explicitly requested with the main looper. Potential ANR impact is a performance question, not a thread-location unknown. |
| 5 | **Valid runtime unknown:** manifest declares background permission, but the UI only requests foreground coarse/fine. Actual behavior across OS/device versions needs Android device tests. |
| 6 | **Mostly known:** formula and clamping are explicit. Timing under load and interaction with an unusually large timeout are untested; avoid describing the formula itself as unknown. |
| 7 | **Partly known:** full queue drops oldest (code is explicit); whether the UI can recover is unknown. No SSE replay/cursor is implemented, so missed events are not recovered from a server-side backlog. |
| 8 | **Static fact, performance untested:** HTTP read APIs call the store synchronously on the asyncio loop. Whether the resulting delay is material under real load is unknown; “observed in tests” is unsupported by the documented test scope. |
| 9 | **Valid limitation:** exact OkHttp internal retry conditions are not established here. Do not confuse them with PhoneBridge's explicit application reconnect loop or message resend. |
| 10 | **Known implementation behavior:** if no matching connected sessions exist, `request_location()` sends nothing, returns 0, and does not queue a locate for later. Desired UX/retry policy remains open. |
| 11 | **Known storage behavior; policy open:** records are keyed by device-name string in filters/grouping, so same-name clients share a logical identity. Whether this is acceptable is a product decision. |
| 12 | **Valid ordering question:** queue order is FIFO, but concurrent flush/live sends and reconnect callbacks have not been tested for ordering. No protocol sequence number exists. |
| 13 | **Not unknown in current implementation:** `BridgeSettings.webSocketUrl()` removes any recognized scheme and returns `ws://`; current app does not support WSS through that field. Whether WSS should be supported is a decision. |
| 14 | **Known behavior:** no redaction is performed in the Android payload path or Python notification/call/location logging. Whether to add a redaction/privacy mode is a decision. |

## 7. Safest Extension Points for `remote.request` / `remote.response`

The document's suggestion to extend protocol dataclasses and the two receive-side dispatchers is a reasonable **starting seam**, but its proposed symmetric server handlers and backward-compatibility rationale need correction.

Assuming the intended direction is **server → Android `remote.request`**, then **Android → server `remote.response`**:

1. Define direction-specific schemas and parsers in `phone-bridge/protocol.py` and `MessageProtocol.kt`, including a bounded request ID, action/type, bounded payload/result, explicit status/error, and timestamp only if needed. Do not let a generic untyped dictionary cross the handler boundary.
2. On the Python side, add a narrowly scoped `BridgeServer.send_remote_request(session, request)` using the established session and existing `_send()` path; parse/dispatch Android `remote.response` in `_read_loop()` to a dedicated handler. Do not add a server `_handle_remote_request()` unless phone-originated requests are a separate supported direction.
3. On Android, add an inbound `RemoteRequest` frame in `MessageProtocol.parse()` and route it from `ConnectionManager.SocketListener.onMessage()` into an explicit service-owned handler/executor. Send the response as a typed `OutboundEvent.RemoteResponse` through a lifecycle-safe sender. Do not block OkHttp's callback on long work.
4. Add explicit capability negotiation before sending requests. Current `hello`/`hello_ack` carries a version but does not enforce it, and there is no capability set. A peer must advertise support; old peers should not be sent unsupported requests.
5. Track outstanding requests by `(session, request_id)` with bounded capacity, timeout, cancellation, disconnect cleanup, and a defined response to unknown/late/duplicate IDs. Decide whether requests survive reconnect; current `BridgeRuntime` and pending queue are volatile.
6. Isolate operation implementation behind a constrained executor interface. The shared token only authenticates the whole connection; it is not per-operation authorization. Require explicit allow-listed operations, bounded resources, and user consent appropriate to the operation. Do not expose these operations through the unauthenticated dashboard HTTP routes.
7. Define idempotency and duplicate semantics before retrying any request. A `remote.response` must be correlated to its request and replay handling must not repeat a side effect blindly.
8. Add protocol compatibility tests and end-to-end tests for supported/unsupported capabilities, timeout, duplicate response, disconnected target, malformed/oversized data, and reconnect behavior. Place `remote.*` tests in new focused modules rather than coupling privileged behavior to the notification/storage integration test.

This is safer than treating `remote.*` as automatically backward-compatible. Android currently ignores unknown inbound frames; Python rejects unknown inbound types with an error frame. Either way, unsupported peers do not implement the operation.

## 8. Questions for Investigation Before Day 3 Implementation

1. What delivery contract is required for existing events and for remote requests: best effort, at-least-once, or effectively-once with idempotency? What data may be dropped under pressure?
2. Should notifications emitted before service/collector startup be intentionally dropped, or should startup ordering/outbox buffering guarantee handoff?
3. Should a locate request have a completion response with the original request ID, timeout, and explicit “no fix” result, or is the current best-effort count enough?
4. Should duplicate event IDs be rejected/deduplicated, and what retention window should an idempotency store have?
5. What should happen to active remote requests when an Android service stops, the phone disconnects, or the process restarts?
6. Which protocol version/capability negotiation format is required, and should old peers close on unsupported versions or continue with a known capability subset?
7. Is device name a unique stable ID or a display label? If multiple clients connect with the same name, should routing target one session or all sessions?
8. What is the intended UI behavior for dashboard notification/call SSE events? At present the browser ignores these event types.
9. Should event-loop SQLite queries be moved off-loop before concurrent client/traffic growth? Establish a latency/load threshold first.
10. What is the supported Android API range for background/boot location behavior, and what on-device lifecycle tests are required?
11. Should graceful close callbacks schedule reconnect once per socket? How should stale socket callbacks be fenced from the current connection state?

## 9. Day 3 Preparation Recommendations

Before implementing `remote.*`:

1. Agree the request direction, operation boundary, authorization and user-consent model, capability/version policy, and delivery contract.
2. Write protocol state transitions: connected/authenticated → request sent → response accepted/rejected/timed out/cancelled; define disconnect and duplicate handling.
3. Add tests for current lifecycle hazards first: zero subscribers, service startup race, send accepted but no ack, duplicate event ID, locate correlation, both close callbacks, stale socket callback, unsupported protocol version/type, and SSE queue overflow/reconnect.
4. Keep source changes split into small compatible steps: message schema and parsers, capability negotiation, routing/correlation, then one benign remote operation. Preserve current notification/call/location paths and their regression tests.
5. Do not promise durable delivery or exactly-once behavior until an outbox/idempotency design is implemented and tested.

## 10. Review Scope and Verdict

This review inspected the architecture document and relevant Android/Python source paths. It did not run a live device, emulator, network, load, or integration test; it does not claim runtime behavior beyond source-derived facts. No source files, Git history, or the original architecture document were changed. No dependencies were installed.

**Verdict:** The documented architecture is **mostly accurate for a high-level orientation**, but **not sufficiently accurate to proceed directly to Day 3 remote-message implementation**. First correct the locate-ack and threading claims, document zero-subscriber loss and non-durable acknowledgement semantics, and replace the backwards-compatibility assumption with explicit capability/version negotiation and request correlation. Day 3 can begin with those decisions and lifecycle tests.
