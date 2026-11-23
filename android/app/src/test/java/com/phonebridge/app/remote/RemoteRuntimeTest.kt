package com.phonebridge.app.remote

import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList

/** Day 18: no offline queueing, no automatic re-send, disconnect handling. */
class RemoteRuntimeTest {

    private val sent = CopyOnWriteArrayList<JSONObject>()

    @After
    fun tearDown() = RemoteRuntime.detach("test done")

    private fun capabilities(available: Boolean = true) = """
        {"type":"remote.capabilities","v":1,"available":$available,"server":"arch","device_id":"dev_0123456789abcdef",
         "permissions":["remote.read"],"operations":[{"id":"system.status","permission":"remote.read","risk":"R1"}],
         "reason":"${if (available) "" else "legacy_auth"}"}
    """.trimIndent()

    @Test
    fun `offline submit fails immediately and is never queued`() {
        RemoteRuntime.detach()
        val r = RemoteRuntime.submit("system.status")
        assertEquals(RequestPhase.FAILED, r.phase)
        assertEquals("disconnected", r.errorCode)
        // Re-attaching does not flush anything.
        RemoteRuntime.attach { sent.add(JSONObject(it)); true }
        assertTrue(sent.isEmpty())
    }

    @Test
    fun `call completes from the matching response`() = runBlocking {
        RemoteRuntime.attach { raw ->
            val req = JSONObject(raw)
            sent.add(req)
            // Respond synchronously (before call() would subscribe if it subscribed late).
            RemoteRuntime.onFrame(
                """{"type":"remote.response","v":1,"request_id":"${req.getString("request_id")}",
                   "operation":"system.status","status":"completed","result":{"host":"arch"}}"""
            )
            true
        }
        val result = RemoteRuntime.call("system.status", timeoutMs = 2_000)
        assertEquals(RequestPhase.COMPLETED, result.phase)
        assertEquals("arch", result.result!!.getString("host"))
        assertEquals(1, sent.size)
        assertEquals("remote.request", sent[0].getString("type"))
        assertEquals(1, sent[0].getInt("v"))
    }

    @Test
    fun `timeout expires locally without re-sending`() = runBlocking {
        RemoteRuntime.attach { sent.add(JSONObject(it)); true }
        val result = RemoteRuntime.call("system.status", timeoutMs = 200)
        assertEquals(RequestPhase.EXPIRED, result.phase)
        assertEquals(1, sent.size)
    }

    @Test
    fun `send failure is reported`() {
        RemoteRuntime.attach { false }
        val r = RemoteRuntime.submit("system.status")
        assertEquals("send_failed", r.errorCode)
    }

    @Test
    fun `capabilities drive availability and detach resets it`() {
        RemoteRuntime.attach { true }
        RemoteRuntime.onFrame(capabilities(true))
        val available = RemoteRuntime.availability.value as RemoteAvailability.Available
        assertTrue(available.caps.supports("system.status"))
        RemoteRuntime.onFrame(capabilities(false))
        assertTrue(RemoteRuntime.availability.value is RemoteAvailability.Unavailable)
        RemoteRuntime.detach()
        assertEquals(RemoteAvailability.Unknown, RemoteRuntime.availability.value)
    }

    @Test
    fun `detach fails in-flight requests`() {
        RemoteRuntime.attach { true }
        val r = RemoteRuntime.submit("system.status")
        assertEquals(RequestPhase.QUEUED, r.phase)
        RemoteRuntime.detach("network switch")
        RemoteRuntime.onFrame(
            """{"type":"remote.response","v":1,"request_id":"${r.requestId}","operation":"system.status","status":"completed","result":{}}"""
        )
        // The late response cannot resurrect it; nothing was re-sent.
        assertTrue(RemoteRuntime.isAttached.not())
    }
}
