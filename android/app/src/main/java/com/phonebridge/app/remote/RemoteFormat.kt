package com.phonebridge.app.remote

import java.util.Locale
import kotlin.math.abs

/** Pure formatting helpers for the Remote screen (JVM unit-tested). */
object RemoteFormat {

    fun bytes(value: Long?): String {
        if (value == null || value < 0) return "—"
        val units = listOf("B", "KB", "MB", "GB", "TB", "PB")
        var size = value.toDouble()
        var unit = 0
        while (size >= 1024 && unit < units.lastIndex) {
            size /= 1024
            unit++
        }
        return if (unit == 0) "$value B" else String.format(Locale.US, "%.1f %s", size, units[unit])
    }

    fun percent(value: Double?): String =
        if (value == null || value.isNaN()) "—" else String.format(Locale.US, "%.1f%%", value)

    fun duration(seconds: Double?): String {
        if (seconds == null || seconds.isNaN() || seconds < 0) return "—"
        val total = seconds.toLong()
        val days = total / 86_400
        val hours = (total % 86_400) / 3_600
        val minutes = (total % 3_600) / 60
        return when {
            days > 0 -> "${days}d ${hours}h"
            hours > 0 -> "${hours}h ${minutes}m"
            minutes > 0 -> "${minutes}m ${total % 60}s"
            else -> "${total}s"
        }
    }

    fun age(nowMs: Long, thenMs: Long): String {
        if (thenMs <= 0) return "never"
        val s = abs(nowMs - thenMs) / 1000
        return when {
            s < 5 -> "just now"
            s < 60 -> "${s}s ago"
            s < 3600 -> "${s / 60}m ago"
            else -> "${s / 3600}h ago"
        }
    }

    fun load(values: List<Double>): String =
        if (values.isEmpty()) "—" else values.joinToString(" · ") { String.format(Locale.US, "%.2f", it) }

    /** Human-readable explanation for server/local error codes. */
    fun error(code: String, message: String): String = when (code) {
        "disconnected" -> "Not connected to the Linux agent."
        "not_authorized" -> "This phone uses the legacy shared token. Pair it with `phone-bridge remote pair` to use Remote."
        "permission_denied" -> "This device lacks the required permission. On Linux run: phone-bridge remote grant <device-id> remote.read"
        "device_revoked" -> "This device was revoked on the Linux agent. Pair again to restore access."
        "rate_limited" -> "Too many requests; slowing down."
        "busy" -> "The Linux agent is busy; try again."
        "timeout" -> "The Linux agent did not answer in time."
        "expired" -> "Request rejected as stale. Check that the phone clock is correct."
        "not_found" -> "That process no longer exists."
        "process_changed" -> "That PID now belongs to a different process. Refresh the list."
        "unknown_operation", "operation_disabled" -> "The Linux agent does not support this operation."
        "unsupported_version" -> "Protocol version mismatch; update the app or the agent."
        "approval_denied" -> "Denied on the Linux computer."
        "approval_expired" -> "Nobody approved the request on the Linux computer in time."
        else -> message.ifBlank { code.ifBlank { "Unknown error" } }
    }
}
