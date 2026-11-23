package com.phonebridge.app.remote

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/** Day 14: Kotlin models round-trip the fixtures shared with the Python agent. */
class RemoteProtocolFixturesTest {

    private fun fixtures(direction: String): List<JSONObject> {
        val dir = File(System.getProperty("nexus.fixtures") ?: "../../protocol/fixtures/remote")
        assertTrue("fixtures dir missing: ${dir.absolutePath}", dir.isDirectory)
        return dir.listFiles { f -> f.name.endsWith(".json") }!!
            .sortedBy { it.name }
            .map { JSONObject(it.readText()) }
            .filter { it.getString("direction") == direction }
    }

    @Test
    fun `server frames parse into typed models`() {
        val all = fixtures("linux_to_android")
        assertTrue(all.size >= 8)
        for (fx in all) {
            val name = fx.getString("name")
            val frame = RemoteProtocol.parse(fx.getJSONObject("frame").toString())
            if (fx.getString("expect") == "invalid") {
                assertTrue("$name should be unsupported", frame is RemoteFrame.UnsupportedVersion)
                continue
            }
            val expected = when (fx.getJSONObject("frame").getString("type")) {
                "remote.capabilities" -> RemoteFrame.Capabilities::class
                "remote.event" -> RemoteFrame.Event::class
                "remote.response" -> RemoteFrame.Response::class
                "remote.approval_required" -> RemoteFrame.ApprovalRequired::class
                "remote.approval_result" -> RemoteFrame.ApprovalResult::class
                else -> error("unexpected type in $name")
            }
            assertTrue("$name parsed as $frame", expected.isInstance(frame))
        }
    }

    @Test
    fun `requests encode exactly like the shared fixtures`() {
        for (fx in fixtures("android_to_linux").filter { it.getString("expect") == "valid" }) {
            val want = fx.getJSONObject("frame")
            val params = want.getJSONObject("params").let { p -> p.keys().asSequence().associateWith { p.get(it) } }
            val encoded = JSONObject(
                RemoteProtocol.encodeRequest(
                    want.getString("request_id"),
                    want.getString("operation"),
                    params,
                    want.getDouble("ts"),
                )
            )
            assertEquals(fx.getString("name"), want.keySet(), encoded.keySet())
            for (key in want.keySet()) {
                val a = want.get(key)
                val b = encoded.get(key)
                if (a is Number && b is Number) {
                    // JSON numbers: 1795440002.0 and 1795440002 are the same value.
                    assertEquals("${fx.getString("name")}.$key", a.toDouble(), b.toDouble(), 0.0)
                } else {
                    assertEquals("${fx.getString("name")}.$key", a.toString(), b.toString())
                }
            }
        }
    }

    @Test
    fun `capabilities and typed results`() {
        val all = fixtures("linux_to_android").associateBy { it.getString("name") }
        val caps = RemoteProtocol.parse(all.getValue("capabilities_available").getJSONObject("frame").toString())
            as RemoteFrame.Capabilities
        assertTrue(caps.available)
        assertTrue(caps.supports("system.status"))
        assertFalse(caps.supports("process.kill"))
        assertEquals(listOf("remote.read"), caps.permissions)

        val legacy = RemoteProtocol.parse(all.getValue("capabilities_legacy").getJSONObject("frame").toString())
            as RemoteFrame.Capabilities
        assertFalse(legacy.available)
        assertFalse(legacy.supports("system.status"))

        val status = RemoteProtocol.parse(all.getValue("response_completed_status").getJSONObject("frame").toString())
            as RemoteFrame.Response
        assertEquals(ResponseStatus.COMPLETED, status.status)
        val s = SystemStatus.from(status.result!!)
        assertEquals("arch-laptop", s.host)
        assertEquals(12.5, s.cpuPercent!!, 0.001)
        assertEquals(8, s.cpuCount)
        assertEquals(37.5, s.memPercent!!, 0.001)
        assertEquals("root", s.disks.single().label)

        val list = RemoteProtocol.parse(all.getValue("response_completed_process_list").getJSONObject("frame").toString())
            as RemoteFrame.Response
        val page = ProcessPage.from(list.result!!)
        assertEquals(2, page.items.size)
        assertEquals("firefox", page.items[0].name)
        assertEquals(98765L, page.items[0].startTicks)
        assertEquals(null, page.items[1].avgCpuPercent)

        val rejected = RemoteProtocol.parse(all.getValue("response_rejected_permission").getJSONObject("frame").toString())
            as RemoteFrame.Response
        assertEquals("permission_denied", rejected.errorCode)
        assertEquals(null, rejected.result)
    }

    @Test
    fun `garbage and unknown frames never throw`() {
        for (raw in listOf("", "{", "[]", "null", """{"type":"remote.nope","v":1}""", """{"type":"notification"}""",
            """{"type":"remote.response","v":1,"status":"weird"}""", """{"type":"remote.event","v":"1"}""")) {
            assertNotNull(RemoteProtocol.parse(raw))
        }
        assertTrue(RemoteProtocol.parse("""{"type":"remote.event","v":1,"state":"running"}""") is RemoteFrame.Invalid)
    }

    @Test
    fun `request ids are unique and valid`() {
        val ids = (1..500).map { RemoteProtocol.newRequestId() }.toSet()
        assertEquals(500, ids.size)
        assertTrue(ids.all { RemoteProtocol.isValidRequestId(it) })
        assertFalse(RemoteProtocol.isValidRequestId("../../etc"))
    }
}
