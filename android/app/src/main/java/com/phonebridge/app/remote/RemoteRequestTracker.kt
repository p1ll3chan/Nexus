package com.phonebridge.app.remote

import org.json.JSONObject

/** Lifecycle of one remote request as seen by the phone. */
enum class RequestPhase(val rank: Int, val terminal: Boolean) {
    QUEUED(0, false),
    ACCEPTED(1, false),
    AWAITING_APPROVAL(2, false),
    RUNNING(3, false),
    COMPLETED(10, true),
    FAILED(10, true),
    REJECTED(10, true),
    EXPIRED(10, true),
}

data class TrackedRequest(
    val requestId: String,
    val operation: String,
    val createdAtMs: Long,
    val phase: RequestPhase = RequestPhase.QUEUED,
    val result: JSONObject? = null,
    val errorCode: String = "",
    val errorMessage: String = "",
    val truncated: Boolean = false,
    val approvalId: String = "",
) {
    val isTerminal: Boolean get() = phase.terminal
}

/**
 * Correlates server frames to locally created requests by `request_id`.
 *
 * Rules (Day 17):
 *  - Frames for unknown request IDs are ignored (never shown in any panel).
 *  - Phases only move forward; late/out-of-order events are ignored.
 *  - Once terminal, a request never changes again (duplicate responses ignored).
 *  - Disconnect fails every non-terminal request; nothing is retried here.
 *
 * Pure Kotlin: no Android dependencies, fully unit-tested on the JVM.
 */
class RemoteRequestTracker(
    private val clock: () -> Long = System::currentTimeMillis,
    private val maxTracked: Int = 256,
) {
    private val requests = LinkedHashMap<String, TrackedRequest>()

    @Synchronized
    fun create(operation: String, requestId: String = RemoteProtocol.newRequestId()): TrackedRequest {
        require(RemoteProtocol.isValidRequestId(requestId)) { "invalid request id" }
        require(!requests.containsKey(requestId)) { "request id already used" }
        val request = TrackedRequest(requestId, operation, clock())
        requests[requestId] = request
        trim()
        return request
    }

    @Synchronized
    fun get(requestId: String): TrackedRequest? = requests[requestId]

    @Synchronized
    fun snapshot(): List<TrackedRequest> = requests.values.toList()

    /** Apply a frame. Returns the updated request, or null if the frame was ignored. */
    @Synchronized
    fun apply(frame: RemoteFrame): TrackedRequest? = when (frame) {
        is RemoteFrame.Event -> advance(frame.requestId) { current ->
            val phase = when (frame.state) {
                "accepted" -> RequestPhase.ACCEPTED
                "awaiting_approval" -> RequestPhase.AWAITING_APPROVAL
                "running" -> RequestPhase.RUNNING
                else -> return@advance null
            }
            current.copy(phase = phase)
        }

        is RemoteFrame.ApprovalRequired -> advance(frame.requestId) {
            it.copy(phase = RequestPhase.AWAITING_APPROVAL, approvalId = frame.approvalId)
        }

        is RemoteFrame.Response -> advance(frame.requestId) {
            if (frame.operation.isNotEmpty() && frame.operation != it.operation) return@advance null
            it.copy(
                phase = when (frame.status) {
                    ResponseStatus.COMPLETED -> RequestPhase.COMPLETED
                    ResponseStatus.FAILED -> RequestPhase.FAILED
                    ResponseStatus.REJECTED -> RequestPhase.REJECTED
                    ResponseStatus.EXPIRED -> RequestPhase.EXPIRED
                },
                result = if (frame.status == ResponseStatus.COMPLETED) frame.result else null,
                errorCode = frame.errorCode,
                errorMessage = frame.errorMessage,
                truncated = frame.truncated,
            )
        }

        is RemoteFrame.UnsupportedVersion -> if (frame.requestId.isNotEmpty()) {
            advance(frame.requestId) {
                it.copy(
                    phase = RequestPhase.FAILED,
                    errorCode = "unsupported_version",
                    errorMessage = "server sent protocol v${frame.version}; update the app",
                )
            }
        } else null

        else -> null
    }

    /** Mark one request failed locally (e.g. send failure). */
    @Synchronized
    fun fail(requestId: String, code: String, message: String): TrackedRequest? =
        advance(requestId) { it.copy(phase = RequestPhase.FAILED, errorCode = code, errorMessage = message) }

    /** Connection lost: fail every pending request. Returns the requests that changed. */
    @Synchronized
    fun failAllPending(code: String = "disconnected", message: String = "connection lost"): List<TrackedRequest> =
        requests.values.filter { !it.isTerminal }.mapNotNull { fail(it.requestId, code, message) }

    /** Expire requests that got no terminal response within [timeoutMs]. */
    @Synchronized
    fun expireOlderThan(timeoutMs: Long): List<TrackedRequest> {
        val cutoff = clock() - timeoutMs
        return requests.values
            .filter { !it.isTerminal && it.createdAtMs < cutoff }
            .mapNotNull {
                advance(it.requestId) { r ->
                    r.copy(phase = RequestPhase.EXPIRED, errorCode = "timeout", errorMessage = "no response from server")
                }
            }
    }

    private inline fun advance(requestId: String, transform: (TrackedRequest) -> TrackedRequest?): TrackedRequest? {
        val current = requests[requestId] ?: return null
        if (current.isTerminal) return null
        val next = transform(current) ?: return null
        if (!next.phase.terminal && next.phase.rank <= current.phase.rank) return null
        requests[requestId] = next
        return next
    }

    private fun trim() {
        if (requests.size <= maxTracked) return
        val iterator = requests.entries.iterator()
        while (requests.size > maxTracked && iterator.hasNext()) {
            val entry = iterator.next()
            if (entry.value.isTerminal) iterator.remove()
        }
    }
}
