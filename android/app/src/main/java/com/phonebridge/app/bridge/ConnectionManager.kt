package com.phonebridge.app.bridge

import android.util.Log
import com.phonebridge.app.data.BridgeSettings
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.ArrayDeque
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.min
import kotlin.random.Random

/**
 * Maintains the WebSocket connection to the laptop, authenticates with the
 * shared secret, replies to heartbeats and transparently reconnects with
 * exponential backoff.
 */
class ConnectionManager(
    private val settingsProvider: () -> BridgeSettings,
    private val scope: CoroutineScope,
) {

    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .writeTimeout(15, TimeUnit.SECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()

    private val running = AtomicBoolean(false)
    private val open = AtomicBoolean(false)
    private val pending = ArrayDeque<OutboundEvent>()
    private val pendingLock = Any()

    @Volatile
    private var webSocket: WebSocket? = null
    private var senderJob: Job? = null
    private var reconnectJob: Job? = null
    private var attempt = 0

    val isRunning: Boolean get() = running.get()
    val isConnected: Boolean get() = open.get()

    fun start() {
        if (!running.compareAndSet(false, true)) return
        attempt = 0
        BridgeRuntime.resetStats()
        senderJob = scope.launch(Dispatchers.IO) {
            BridgeRuntime.events.collect { event -> dispatch(event) }
        }
        openConnection()
    }

    fun stop() {
        running.set(false)
        senderJob?.cancel()
        senderJob = null
        reconnectJob?.cancel()
        reconnectJob = null
        open.set(false)
        webSocket?.close(NORMAL_CLOSURE, "client shutting down")
        webSocket = null
        synchronized(pendingLock) { pending.clear() }
        BridgeRuntime.updateState(ConnectionState.Disconnected)
    }

    fun sendNow(event: OutboundEvent) {
        dispatch(event)
    }

    private fun dispatch(event: OutboundEvent) {
        val socket = webSocket
        if (open.get() && socket != null && socket.send(event.encode())) {
            return
        }
        synchronized(pendingLock) {
            if (pending.size >= MAX_PENDING) {
                pending.pollFirst()
                BridgeRuntime.updateStats { it.copy(notificationsSkipped = it.notificationsSkipped + 1) }
            }
            pending.addLast(event)
        }
    }

    private fun openConnection() {
        if (!running.get()) return
        val settings = settingsProvider()
        if (!settings.isConfigured()) {
            BridgeRuntime.updateState(
                ConnectionState.Retrying(attempt, 0, "server address and token are required")
            )
            return
        }
        val url = settings.webSocketUrl()
        BridgeRuntime.updateState(ConnectionState.Connecting)
        val request = Request.Builder().url(url).build()
        webSocket = client.newWebSocket(request, SocketListener(settings))
    }

    private fun scheduleReconnect(reason: String) {
        if (!running.get()) return
        open.set(false)
        attempt += 1
        val backoff = min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * (1L shl min(attempt - 1, 5)))
        val delayMs = backoff + Random.nextLong(0, 1_000)
        BridgeRuntime.updateState(ConnectionState.Retrying(attempt, delayMs, reason))
        BridgeRuntime.updateStats { it.copy(reconnects = it.reconnects + 1, lastError = reason) }
        reconnectJob?.cancel()
        reconnectJob = scope.launch {
            delay(delayMs)
            openConnection()
        }
    }

    private fun flushPending(socket: WebSocket) {
        while (running.get()) {
            val next = synchronized(pendingLock) { pending.pollFirst() } ?: return
            if (!socket.send(next.encode())) {
                synchronized(pendingLock) { pending.addFirst(next) }
                return
            }
        }
    }

    private inner class SocketListener(private val settings: BridgeSettings) : WebSocketListener() {

        override fun onOpen(webSocket: WebSocket, response: Response) {
            Log.i(TAG, "connected to ${settings.webSocketUrl()}")
            webSocket.send(OutboundEvent.Hello(settings.deviceName, settings.token).encode())
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            when (val frame = MessageProtocol.parse(text)) {
                is InboundFrame.HelloAck -> {
                    open.set(true)
                    attempt = 0
                    BridgeRuntime.updateState(ConnectionState.Connected(frame.server))
                    BridgeRuntime.updateStats {
                        it.copy(connectedSince = System.currentTimeMillis(), lastError = "")
                    }
                    Log.i(TAG, "authenticated with ${frame.server} (protocol v${frame.version})")
                    flushPending(webSocket)
                }

                is InboundFrame.Ping -> webSocket.send(
                    OutboundEvent.Pong(frame.timestamp).encode()
                )

                is InboundFrame.Pong -> Unit

                is InboundFrame.Locate -> {
                    Log.i(TAG, "server requested immediate location fix (${frame.id})")
                    BridgeRuntime.requestImmediateLocation()
                }

                is InboundFrame.Ack -> {
                    if (!frame.ok) {
                        Log.w(TAG, "server rejected ${frame.ref}: ${frame.detail}")
                    }
                }

                is InboundFrame.Error -> {
                    Log.e(TAG, "server error ${frame.code}: ${frame.message}")
                    BridgeRuntime.updateStats { it.copy(lastError = frame.message) }
                    if (frame.code == "auth_failed" || frame.code == "device_not_allowed") {
                        // Avoid a hot retry loop on a configuration problem.
                        attempt = maxOf(attempt, 4)
                    }
                }

                is InboundFrame.Unknown -> Log.d(TAG, "ignoring frame: ${frame.type}")
            }
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(NORMAL_CLOSURE, null)
            if (running.get()) scheduleReconnect("closed: $reason")
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            if (running.get()) scheduleReconnect("closed: $reason")
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            Log.w(TAG, "connection failure: ${t.message}")
            if (running.get()) {
                scheduleReconnect(t.message ?: "connection failure")
            } else {
                open.set(false)
                BridgeRuntime.updateState(ConnectionState.Disconnected)
            }
        }
    }

    companion object {
        private const val TAG = "ConnectionManager"
        private const val NORMAL_CLOSURE = 1000
        private const val MAX_PENDING = 200
        private const val BASE_BACKOFF_MS = 5_000L
        private const val MAX_BACKOFF_MS = 60_000L
    }
}
