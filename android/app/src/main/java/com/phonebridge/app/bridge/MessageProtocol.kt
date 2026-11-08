package com.phonebridge.app.bridge

import org.json.JSONObject
import java.util.UUID

/**
 * JSON wire protocol shared with the Python server. Mirrors `protocol.py`.
 */
object MessageProtocol {

    const val VERSION = 1

    private const val TYPE_HELLO = "hello"
    private const val TYPE_HELLO_ACK = "hello_ack"
    private const val TYPE_NOTIFICATION = "notification"
    private const val TYPE_CALL = "call"
    private const val TYPE_LOCATION = "location"
    private const val TYPE_LOCATE = "locate"
    private const val TYPE_PING = "ping"
    private const val TYPE_PONG = "pong"
    private const val TYPE_ACK = "ack"
    private const val TYPE_ERROR = "error"

    fun hello(device: String, token: String): String = JSONObject()
        .put("type", TYPE_HELLO)
        .put("device", device)
        .put("token", token)
        .put("version", VERSION)
        .put("platform", "android")
        .toString()

    fun notification(event: NotificationEvent): String = JSONObject()
        .put("type", TYPE_NOTIFICATION)
        .put("id", event.id)
        .put("app", event.app)
        .put("package", event.packageName)
        .put("title", event.title)
        .put("text", event.text)
        .put("ongoing", event.ongoing)
        .put("low_priority", event.lowPriority)
        .put("timestamp", event.timestamp)
        .toString()

    fun call(event: CallEvent): String = JSONObject()
        .put("type", TYPE_CALL)
        .put("id", event.id)
        .put("state", event.state.wire)
        .put("name", event.name)
        .put("number", event.number)
        .put("timestamp", event.timestamp)
        .toString()

    fun location(event: LocationEvent): String = JSONObject().apply {
        put("type", TYPE_LOCATION)
        put("id", event.id)
        put("latitude", event.latitude)
        put("longitude", event.longitude)
        put("accuracy", event.accuracy.toDouble())
        if (event.battery != null) put("battery", event.battery)
        put("charging", event.charging)
        if (event.altitude != null) put("altitude", event.altitude)
        if (event.speed != null) put("speed", event.speed.toDouble())
        if (event.bearing != null) put("bearing", event.bearing.toDouble())
        put("provider", event.provider)
        put("timestamp", event.timestamp)
    }.toString()

    fun ping(timestamp: Double = now()): String = JSONObject()
        .put("type", TYPE_PING)
        .put("timestamp", timestamp)
        .toString()

    fun pong(timestamp: Double = now()): String = JSONObject()
        .put("type", TYPE_PONG)
        .put("timestamp", timestamp)
        .toString()

    fun parse(raw: String): InboundFrame {
        val json = runCatching { JSONObject(raw) }
            .getOrElse { return InboundFrame.Unknown("invalid", raw) }
        return when (json.optString("type")) {
            TYPE_HELLO_ACK -> InboundFrame.HelloAck(
                server = json.optString("server"),
                version = json.optInt("version", VERSION),
            )
            TYPE_ACK -> InboundFrame.Ack(
                ref = json.optString("ref"),
                ok = json.optBoolean("ok", true),
                detail = json.optString("detail"),
            )
            TYPE_PING -> InboundFrame.Ping(json.optDouble("timestamp", now()))
            TYPE_PONG -> InboundFrame.Pong(json.optDouble("timestamp", now()))
            TYPE_LOCATE -> InboundFrame.Locate(
                id = json.optString("id"),
                timestamp = json.optDouble("timestamp", now()),
            )
            TYPE_ERROR -> InboundFrame.Error(
                code = json.optString("code"),
                message = json.optString("message"),
            )
            else -> InboundFrame.Unknown(json.optString("type"), raw)
        }
    }

    fun newId(): String = UUID.randomUUID().toString().replace("-", "")

    fun now(): Double = System.currentTimeMillis() / 1000.0
}

enum class CallState(val wire: String) {
    RINGING("ringing"),
    OFFHOOK("offhook"),
    IDLE("idle");

    companion object {
        fun fromAndroid(raw: Int): CallState = when (raw) {
            1 -> RINGING
            2 -> OFFHOOK
            else -> IDLE
        }
    }
}

sealed interface OutboundEvent {
    fun encode(): String

    data class Hello(val device: String, val token: String) : OutboundEvent {
        override fun encode() = MessageProtocol.hello(device, token)
    }

    data class Notification(val event: NotificationEvent) : OutboundEvent {
        override fun encode() = MessageProtocol.notification(event)
    }

    data class Call(val event: CallEvent) : OutboundEvent {
        override fun encode() = MessageProtocol.call(event)
    }

    data class Location(val event: LocationEvent) : OutboundEvent {
        override fun encode() = MessageProtocol.location(event)
    }

    data class Ping(val timestamp: Double = MessageProtocol.now()) : OutboundEvent {
        override fun encode() = MessageProtocol.ping(timestamp)
    }

    data class Pong(val timestamp: Double = MessageProtocol.now()) : OutboundEvent {
        override fun encode() = MessageProtocol.pong(timestamp)
    }
}

data class NotificationEvent(
    val app: String,
    val title: String,
    val text: String,
    val packageName: String,
    val id: String = MessageProtocol.newId(),
    val ongoing: Boolean = false,
    val lowPriority: Boolean = false,
    val timestamp: Double = MessageProtocol.now(),
)

data class CallEvent(
    val state: CallState,
    val name: String = "",
    val number: String = "",
    val id: String = MessageProtocol.newId(),
    val timestamp: Double = MessageProtocol.now(),
)

data class LocationEvent(
    val latitude: Double,
    val longitude: Double,
    val accuracy: Float = 0f,
    val battery: Int? = null,
    val charging: Boolean = false,
    val altitude: Double? = null,
    val speed: Float? = null,
    val bearing: Float? = null,
    val provider: String = "gps",
    val id: String = MessageProtocol.newId(),
    val timestamp: Double = MessageProtocol.now(),
)

sealed interface InboundFrame {
    data class HelloAck(val server: String, val version: Int) : InboundFrame
    data class Ack(val ref: String, val ok: Boolean, val detail: String) : InboundFrame
    data class Locate(val id: String, val timestamp: Double) : InboundFrame
    data class Ping(val timestamp: Double) : InboundFrame
    data class Pong(val timestamp: Double) : InboundFrame
    data class Error(val code: String, val message: String) : InboundFrame
    data class Unknown(val type: String, val raw: String) : InboundFrame
}
