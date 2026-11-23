package com.phonebridge.app.remote

import org.json.JSONArray
import org.json.JSONObject
import java.security.SecureRandom

/**
 * Nexus Remote wire protocol v1. Mirrors `phone-bridge/remote_protocol.py`.
 * Shared fixtures live in `protocol/fixtures/remote/` and are tested on both sides.
 */
object RemoteProtocol {
    const val VERSION = 1
    const val TYPE_PREFIX = "remote."
    const val TYPE_REQUEST = "remote.request"
    const val TYPE_RESPONSE = "remote.response"
    const val TYPE_EVENT = "remote.event"
    const val TYPE_CAPABILITIES = "remote.capabilities"
    const val TYPE_APPROVAL_REQUIRED = "remote.approval_required"
    const val TYPE_APPROVAL_RESULT = "remote.approval_result"

    private val REQUEST_ID = Regex("^[A-Za-z0-9_-]{8,64}$")
    private val random = SecureRandom()

    fun isRemoteType(type: String?): Boolean = type != null && type.startsWith(TYPE_PREFIX)

    /** 24 hex chars from a CSPRNG: unguessable and matches the server's request_id rule. */
    fun newRequestId(): String {
        val bytes = ByteArray(12)
        random.nextBytes(bytes)
        return bytes.joinToString("") { "%02x".format(it) }
    }

    fun isValidRequestId(id: String): Boolean = REQUEST_ID.matches(id)

    fun encodeRequest(
        requestId: String,
        operation: String,
        params: Map<String, Any?>,
        tsSeconds: Double,
    ): String {
        require(isValidRequestId(requestId)) { "invalid request id" }
        val p = JSONObject()
        params.forEach { (k, v) -> if (v != null) p.put(k, v) }
        return JSONObject()
            .put("type", TYPE_REQUEST)
            .put("v", VERSION)
            .put("request_id", requestId)
            .put("operation", operation)
            .put("params", p)
            .put("ts", tsSeconds)
            .toString()
    }

    /** Parse a server->device remote frame. Never throws. */
    fun parse(raw: String): RemoteFrame {
        val json = runCatching { JSONObject(raw) }.getOrElse { return RemoteFrame.Invalid("invalid json") }
        return parse(json)
    }

    fun parse(json: JSONObject): RemoteFrame {
        val type = json.optString("type")
        if (!isRemoteType(type)) return RemoteFrame.Invalid("not a remote frame")
        val v = json.opt("v")
        if (v !is Int || v != VERSION) {
            return RemoteFrame.UnsupportedVersion(type, json.optString("request_id"), v?.toString() ?: "missing")
        }
        val rid = json.optString("request_id")
        return when (type) {
            TYPE_CAPABILITIES -> RemoteFrame.Capabilities(
                available = json.optBoolean("available", false),
                server = json.optString("server"),
                deviceId = json.optString("device_id"),
                permissions = json.optJSONArray("permissions").strings(),
                operations = json.optJSONArray("operations").objects().map { op ->
                    OperationInfo(
                        id = op.optString("id"),
                        permission = op.optString("permission"),
                        risk = op.optString("risk"),
                        requiresApproval = op.optBoolean("requires_approval", false),
                        description = op.optString("description"),
                    )
                },
                reason = json.optString("reason"),
            )
            TYPE_EVENT -> if (rid.isEmpty()) RemoteFrame.Invalid("event without request_id") else
                RemoteFrame.Event(rid, json.optString("state"))
            TYPE_APPROVAL_REQUIRED -> RemoteFrame.ApprovalRequired(
                requestId = rid,
                approvalId = json.optString("approval_id"),
                operation = json.optString("operation"),
                expiresAt = json.optDouble("expires_at", 0.0),
            )
            TYPE_APPROVAL_RESULT -> RemoteFrame.ApprovalResult(
                requestId = rid,
                approvalId = json.optString("approval_id"),
                outcome = json.optString("outcome"),
            )
            TYPE_RESPONSE -> {
                val status = ResponseStatus.fromWire(json.optString("status"))
                    ?: return RemoteFrame.Invalid("unknown status")
                val error = json.optJSONObject("error")
                RemoteFrame.Response(
                    requestId = rid,
                    operation = json.optString("operation"),
                    status = status,
                    result = json.optJSONObject("result"),
                    errorCode = error?.optString("code").orEmpty(),
                    errorMessage = error?.optString("message").orEmpty(),
                    truncated = json.optBoolean("truncated", false),
                )
            }
            else -> RemoteFrame.Invalid("unknown remote type $type")
        }
    }
}

enum class ResponseStatus(val wire: String) {
    COMPLETED("completed"),
    FAILED("failed"),
    REJECTED("rejected"),
    EXPIRED("expired");

    companion object {
        fun fromWire(raw: String): ResponseStatus? = entries.firstOrNull { it.wire == raw }
    }
}

data class OperationInfo(
    val id: String,
    val permission: String,
    val risk: String,
    val requiresApproval: Boolean,
    val description: String,
)

sealed interface RemoteFrame {
    data class Capabilities(
        val available: Boolean,
        val server: String,
        val deviceId: String,
        val permissions: List<String>,
        val operations: List<OperationInfo>,
        val reason: String,
    ) : RemoteFrame {
        fun supports(operation: String) = available && operations.any { it.id == operation }
    }

    data class Event(val requestId: String, val state: String) : RemoteFrame
    data class ApprovalRequired(
        val requestId: String,
        val approvalId: String,
        val operation: String,
        val expiresAt: Double,
    ) : RemoteFrame

    data class ApprovalResult(val requestId: String, val approvalId: String, val outcome: String) : RemoteFrame
    data class Response(
        val requestId: String,
        val operation: String,
        val status: ResponseStatus,
        val result: JSONObject?,
        val errorCode: String,
        val errorMessage: String,
        val truncated: Boolean,
    ) : RemoteFrame

    data class UnsupportedVersion(val type: String, val requestId: String, val version: String) : RemoteFrame
    data class Invalid(val reason: String) : RemoteFrame
}

internal fun JSONArray?.strings(): List<String> =
    if (this == null) emptyList() else (0 until length()).mapNotNull { optString(it, null) }

internal fun JSONArray?.objects(): List<JSONObject> =
    if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it) }

internal fun JSONObject.optLongOrNull(key: String): Long? =
    if (isNull(key) || !has(key)) null else optLong(key)

internal fun JSONObject.optDoubleOrNull(key: String): Double? =
    if (isNull(key) || !has(key)) null else optDouble(key).takeUnless { it.isNaN() }

// ---------------------------------------------------------------- typed results

data class DiskInfo(val label: String, val totalBytes: Long, val usedBytes: Long, val percent: Double?)

data class SystemStatus(
    val host: String,
    val kernel: String,
    val cpuPercent: Double?,
    val cpuCount: Int?,
    val load: List<Double>,
    val memTotal: Long?,
    val memUsed: Long?,
    val memPercent: Double?,
    val disks: List<DiskInfo>,
    val uptimeSeconds: Double?,
    val collectedAt: Double,
    val unavailable: List<String>,
) {
    companion object {
        fun from(json: JSONObject): SystemStatus {
            val cpu = json.optJSONObject("cpu")
            val mem = json.optJSONObject("memory")
            val loadArr = cpu?.optJSONArray("load")
            return SystemStatus(
                host = json.optString("host"),
                kernel = json.optJSONObject("os")?.optString("kernel").orEmpty(),
                cpuPercent = cpu?.optDoubleOrNull("percent"),
                cpuCount = cpu?.optLongOrNull("count")?.toInt(),
                load = if (loadArr == null) emptyList() else (0 until loadArr.length()).map { loadArr.optDouble(it) },
                memTotal = mem?.optLongOrNull("total_bytes"),
                memUsed = mem?.optLongOrNull("used_bytes"),
                memPercent = mem?.optDoubleOrNull("percent"),
                disks = json.optJSONArray("disks").objects().map {
                    DiskInfo(
                        label = it.optString("label"),
                        totalBytes = it.optLong("total_bytes"),
                        usedBytes = it.optLong("used_bytes"),
                        percent = it.optDoubleOrNull("percent"),
                    )
                },
                uptimeSeconds = json.optDoubleOrNull("uptime_seconds"),
                collectedAt = json.optDouble("collected_at", 0.0),
                unavailable = json.optJSONArray("unavailable").strings(),
            )
        }
    }
}

data class ProcessSummary(
    val pid: Int,
    val name: String,
    val state: String,
    val own: Boolean,
    val rssBytes: Long,
    val memPercent: Double?,
    val cpuTimeSeconds: Double,
    val avgCpuPercent: Double?,
    val threads: Int,
    val startTicks: Long?,
    val runtimeSeconds: Double?,
) {
    companion object {
        fun from(json: JSONObject) = ProcessSummary(
            pid = json.optInt("pid"),
            name = json.optString("name").take(64),
            state = json.optString("state"),
            own = json.optBoolean("own", false),
            rssBytes = json.optLong("rss_bytes"),
            memPercent = json.optDoubleOrNull("mem_percent"),
            cpuTimeSeconds = json.optDouble("cpu_time_seconds", 0.0),
            avgCpuPercent = json.optDoubleOrNull("avg_cpu_percent"),
            threads = json.optInt("threads"),
            startTicks = json.optLongOrNull("start_ticks"),
            runtimeSeconds = json.optDoubleOrNull("runtime_seconds"),
        )
    }
}

data class ProcessPage(
    val total: Int,
    val offset: Int,
    val returned: Int,
    val truncated: Boolean,
    val inaccessible: Int,
    val items: List<ProcessSummary>,
) {
    companion object {
        fun from(json: JSONObject): ProcessPage {
            val items = json.optJSONArray("items").objects().map(ProcessSummary::from)
            return ProcessPage(
                total = json.optInt("total"),
                offset = json.optInt("offset"),
                returned = json.optInt("returned", items.size),
                truncated = json.optBoolean("truncated", false),
                inaccessible = json.optInt("inaccessible"),
                items = items,
            )
        }
    }
}

data class ProcessDetail(
    val summary: ProcessSummary,
    val ppid: Int,
    val nice: Int,
    val vmsBytes: Long,
) {
    companion object {
        fun from(json: JSONObject) = ProcessDetail(
            summary = ProcessSummary.from(json),
            ppid = json.optInt("ppid"),
            nice = json.optInt("nice"),
            vmsBytes = json.optLong("vms_bytes"),
        )
    }
}
