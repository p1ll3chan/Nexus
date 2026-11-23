package com.phonebridge.app.remote

import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withTimeoutOrNull

sealed interface RemoteAvailability {
    /** Not connected, or connected but the server has not announced capabilities yet. */
    data object Unknown : RemoteAvailability

    /** Server announced remote is unavailable for this session (e.g. legacy token). */
    data class Unavailable(val reason: String, val server: String) : RemoteAvailability

    data class Available(val caps: RemoteFrame.Capabilities) : RemoteAvailability
}

/**
 * Process-wide Remote state shared by the connection and the Remote screen.
 *
 * Remote requests are sent only over a live, authenticated socket. They are never
 * queued while offline and never re-sent with the same request_id.
 */
object RemoteRuntime {

    private val tracker = RemoteRequestTracker()

    private val _availability = MutableStateFlow<RemoteAvailability>(RemoteAvailability.Unknown)
    val availability = _availability.asStateFlow()

    private val _updates = MutableSharedFlow<TrackedRequest>(extraBufferCapacity = 128)
    val updates = _updates.asSharedFlow()

    @Volatile
    private var sender: ((String) -> Boolean)? = null

    /** Called by ConnectionManager once hello_ack is received. */
    fun attach(send: (String) -> Boolean) {
        sender = send
    }

    /** Called by ConnectionManager on close/failure/stop. */
    fun detach(reason: String = "connection lost") {
        sender = null
        _availability.value = RemoteAvailability.Unknown
        tracker.failAllPending("disconnected", reason).forEach { _updates.tryEmit(it) }
    }

    val isAttached: Boolean get() = sender != null

    /** Feed a raw server frame whose type starts with "remote.". */
    fun onFrame(raw: String) {
        when (val frame = RemoteProtocol.parse(raw)) {
            is RemoteFrame.Capabilities -> _availability.value = if (frame.available) {
                RemoteAvailability.Available(frame)
            } else {
                RemoteAvailability.Unavailable(frame.reason, frame.server)
            }
            else -> tracker.apply(frame)?.let { _updates.tryEmit(it) }
        }
    }

    /** Send a request; returns the tracked request (already FAILED if offline). */
    fun submit(operation: String, params: Map<String, Any?> = emptyMap()): TrackedRequest =
        send(tracker.create(operation), params)

    private fun send(request: TrackedRequest, params: Map<String, Any?>): TrackedRequest {
        val transport = sender
        if (transport == null) {
            return tracker.fail(request.requestId, "disconnected", "not connected to the Linux agent")!!
                .also { _updates.tryEmit(it) }
        }
        val frame = RemoteProtocol.encodeRequest(
            request.requestId,
            request.operation,
            params,
            System.currentTimeMillis() / 1000.0,
        )
        if (!transport(frame)) {
            return tracker.fail(request.requestId, "send_failed", "could not send request")!!
                .also { _updates.tryEmit(it) }
        }
        _updates.tryEmit(request)
        return request
    }

    /** Submit and suspend until the request is terminal or [timeoutMs] elapses. */
    suspend fun call(
        operation: String,
        params: Map<String, Any?> = emptyMap(),
        timeoutMs: Long = 15_000,
    ): TrackedRequest = coroutineScope {
        val request = tracker.create(operation)
        // Subscribe before sending so a fast response cannot be missed.
        val waiter = async(start = CoroutineStart.UNDISPATCHED) {
            updates.first { it.requestId == request.requestId && it.isTerminal }
        }
        val sent = send(request, params)
        if (sent.isTerminal) {
            waiter.cancel()
            return@coroutineScope sent
        }
        val done = withTimeoutOrNull(timeoutMs) { waiter.await() }
        if (done != null) return@coroutineScope done
        waiter.cancel()
        tracker.get(request.requestId)?.takeIf { it.isTerminal }?.let { return@coroutineScope it }
        tracker.expireOlderThan(timeoutMs - 1).forEach { _updates.tryEmit(it) }
        tracker.get(request.requestId) ?: request
    }
}
