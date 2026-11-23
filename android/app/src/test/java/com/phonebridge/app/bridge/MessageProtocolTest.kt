package com.phonebridge.app.bridge

import com.phonebridge.app.data.DeviceCredential
import com.phonebridge.app.remote.RemoteFormat
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class MessageProtocolTest {

    private val credential = DeviceCredential.parse("dev_0123456789abcdef." + "A".repeat(43))!!

    @Test
    fun `hello with device credential omits the shared token`() {
        val hello = JSONObject(MessageProtocol.hello("pixel", "legacy-token", credential))
        assertEquals("dev_0123456789abcdef", hello.getString("device_id"))
        assertEquals("A".repeat(43), hello.getString("device_secret"))
        assertFalse(hello.has("token"))
    }

    @Test
    fun `legacy hello is unchanged`() {
        val hello = JSONObject(MessageProtocol.hello("pixel", "legacy-token"))
        assertEquals("legacy-token", hello.getString("token"))
        assertFalse(hello.has("device_id"))
        assertEquals("hello", hello.getString("type"))
    }

    @Test
    fun `remote frames are routed and legacy frames still parse`() {
        assertTrue(MessageProtocol.parse("""{"type":"remote.response","v":1}""") is InboundFrame.Remote)
        assertTrue(MessageProtocol.parse("""{"type":"locate","id":"x"}""") is InboundFrame.Locate)
        val ack = MessageProtocol.parse("""{"type":"hello_ack","server":"arch","version":1,"auth":"device","device_id":"dev_0123456789abcdef"}""")
            as InboundFrame.HelloAck
        assertEquals("device", ack.auth)
        val oldAck = MessageProtocol.parse("""{"type":"hello_ack","server":"arch","version":1}""") as InboundFrame.HelloAck
        assertEquals("token", oldAck.auth)
    }

    @Test
    fun `credential parsing and redaction`() {
        assertNull(DeviceCredential.parse(""))
        assertNull(DeviceCredential.parse("dev_0123456789abcdef"))
        assertNull(DeviceCredential.parse("dev_XYZ.secretsecretsecretsecretsecretsecret"))
        assertNull(DeviceCredential.parse("dev_0123456789abcdef.short"))
        assertEquals("dev_0123456789abcdef", DeviceCredential.parse("  ${"dev_0123456789abcdef." + "b".repeat(40)}  ")!!.deviceId)
        assertFalse(credential.toString().contains("AAAA"))
        assertFalse(OutboundEvent.Hello("p", "tok", credential).toString().contains("AAAA"))
    }

    @Test
    fun `formatting helpers`() {
        assertEquals("—", RemoteFormat.bytes(null))
        assertEquals("512 B", RemoteFormat.bytes(512))
        assertEquals("1.5 KB", RemoteFormat.bytes(1536))
        assertEquals("16.0 GB", RemoteFormat.bytes(16L * 1024 * 1024 * 1024))
        assertEquals("37.5%", RemoteFormat.percent(37.5))
        assertEquals("—", RemoteFormat.percent(null))
        assertEquals("45s", RemoteFormat.duration(45.0))
        assertEquals("2m 5s", RemoteFormat.duration(125.0))
        assertEquals("1h 0m", RemoteFormat.duration(3600.0))
        assertEquals("2d 3h", RemoteFormat.duration(2 * 86400.0 + 3 * 3600))
        assertEquals("just now", RemoteFormat.age(10_000, 9_000))
        assertEquals("30s ago", RemoteFormat.age(40_000, 10_000))
        assertEquals("never", RemoteFormat.age(1, 0))
        assertEquals("0.50 · 0.40", RemoteFormat.load(listOf(0.5, 0.4)))
        assertTrue(RemoteFormat.error("permission_denied", "").contains("remote grant"))
        assertEquals("custom", RemoteFormat.error("weird_code", "custom"))
    }
}
