package com.phonebridge.app.bridge

import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow

sealed interface ConnectionState {
    data object Disconnected : ConnectionState
    data object Connecting : ConnectionState
    data class Connected(val server: String) : ConnectionState
    data class Retrying(val attempt: Int, val delayMs: Long, val reason: String) : ConnectionState
}

data class BridgeStats(
    val notificationsSent: Int = 0,
    val notificationsSkipped: Int = 0,
    val callsSent: Int = 0,
    val locationsSent: Int = 0,
    val lastLocationSummary: String = "",
    val reconnects: Int = 0,
    val connectedSince: Long = 0L,
    val lastError: String = "",
)

/**
 * Process-wide runtime shared between the system-bound listeners, the
 * foreground service and the UI. The listeners publish events without knowing
 * whether a WebSocket is currently connected.
 */
object BridgeRuntime {

    private val _events = MutableSharedFlow<OutboundEvent>(
        replay = 0,
        extraBufferCapacity = 256,
    )
    val events = _events.asSharedFlow()

    private val _locateRequests = MutableSharedFlow<Unit>(
        replay = 0,
        extraBufferCapacity = 16,
    )
    val locateRequests = _locateRequests.asSharedFlow()

    private val _state = MutableStateFlow<ConnectionState>(ConnectionState.Disconnected)
    val state = _state.asStateFlow()

    private val _stats = MutableStateFlow(BridgeStats())
    val stats = _stats.asStateFlow()

    fun publish(event: OutboundEvent): Boolean = _events.tryEmit(event)

    fun requestImmediateLocation(): Boolean = _locateRequests.tryEmit(Unit)

    internal fun updateState(state: ConnectionState) {
        _state.value = state
    }

    internal fun updateStats(transform: (BridgeStats) -> BridgeStats) {
        _stats.value = transform(_stats.value)
    }

    internal fun resetStats() {
        _stats.value = BridgeStats()
    }
}
