package com.phonebridge.app.remote

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Day 17: request-state correctness under out-of-order and duplicate frames. */
class RemoteRequestTrackerTest {

    private var now = 1_000L
    private val tracker = RemoteRequestTracker(clock = { now })

    private fun response(id: String, status: ResponseStatus, op: String = "system.status", code: String = "") =
        RemoteFrame.Response(id, op, status, if (status == ResponseStatus.COMPLETED) JSONObject("{\"ok\":1}") else null,
            code, "", false)

    @Test
    fun `normal lifecycle`() {
        val r = tracker.create("system.status", "req_000000000001")
        assertEquals(RequestPhase.QUEUED, r.phase)
        assertEquals(RequestPhase.ACCEPTED, tracker.apply(RemoteFrame.Event(r.requestId, "accepted"))!!.phase)
        assertEquals(RequestPhase.RUNNING, tracker.apply(RemoteFrame.Event(r.requestId, "running"))!!.phase)
        val done = tracker.apply(response(r.requestId, ResponseStatus.COMPLETED))!!
        assertEquals(RequestPhase.COMPLETED, done.phase)
        assertEquals(1, done.result!!.getInt("ok"))
    }

    @Test
    fun `out of order events are ignored and response can arrive first`() {
        val r = tracker.create("system.status", "req_000000000002")
        tracker.apply(RemoteFrame.Event(r.requestId, "running"))
        assertNull(tracker.apply(RemoteFrame.Event(r.requestId, "accepted")))
        assertEquals(RequestPhase.RUNNING, tracker.get(r.requestId)!!.phase)

        val r2 = tracker.create("system.status", "req_000000000003")
        tracker.apply(response(r2.requestId, ResponseStatus.COMPLETED))
        assertNull(tracker.apply(RemoteFrame.Event(r2.requestId, "running")))
        assertEquals(RequestPhase.COMPLETED, tracker.get(r2.requestId)!!.phase)
    }

    @Test
    fun `duplicate and conflicting responses do not change a terminal request`() {
        val r = tracker.create("system.status", "req_000000000004")
        tracker.apply(response(r.requestId, ResponseStatus.COMPLETED))
        assertNull(tracker.apply(response(r.requestId, ResponseStatus.COMPLETED)))
        assertNull(tracker.apply(response(r.requestId, ResponseStatus.FAILED, code = "timeout")))
        assertEquals(RequestPhase.COMPLETED, tracker.get(r.requestId)!!.phase)
    }

    @Test
    fun `responses for unknown or mismatched requests are ignored`() {
        assertNull(tracker.apply(response("req_unknown00001", ResponseStatus.COMPLETED)))
        val r = tracker.create("process.list", "req_000000000005")
        // Same id but a different operation: never shown in this panel.
        assertNull(tracker.apply(response(r.requestId, ResponseStatus.COMPLETED, op = "system.status")))
        assertEquals(RequestPhase.QUEUED, tracker.get(r.requestId)!!.phase)
    }

    @Test
    fun `approval phases`() {
        val r = tracker.create("test.approve", "req_000000000006")
        tracker.apply(RemoteFrame.Event(r.requestId, "accepted"))
        val waiting = tracker.apply(RemoteFrame.ApprovalRequired(r.requestId, "apr_0123456789abcdef", "test.approve", 0.0))!!
        assertEquals(RequestPhase.AWAITING_APPROVAL, waiting.phase)
        assertEquals("apr_0123456789abcdef", waiting.approvalId)
        val rejected = tracker.apply(response(r.requestId, ResponseStatus.REJECTED, op = "test.approve", code = "approval_denied"))!!
        assertEquals(RequestPhase.REJECTED, rejected.phase)
        assertEquals("approval_denied", rejected.errorCode)
    }

    @Test
    fun `disconnect fails pending requests only`() {
        val done = tracker.create("system.status", "req_000000000007")
        tracker.apply(response(done.requestId, ResponseStatus.COMPLETED))
        val pending = tracker.create("process.list", "req_000000000008")
        val changed = tracker.failAllPending()
        assertEquals(listOf(pending.requestId), changed.map { it.requestId })
        assertEquals("disconnected", tracker.get(pending.requestId)!!.errorCode)
        assertEquals(RequestPhase.COMPLETED, tracker.get(done.requestId)!!.phase)
        // A late response after disconnect is ignored.
        assertNull(tracker.apply(response(pending.requestId, ResponseStatus.COMPLETED, op = "process.list")))
    }

    @Test
    fun `local expiry`() {
        val r = tracker.create("system.status", "req_000000000009")
        now += 5_000
        assertTrue(tracker.expireOlderThan(10_000).isEmpty())
        now += 10_000
        assertEquals(RequestPhase.EXPIRED, tracker.expireOlderThan(10_000).single().phase)
        assertEquals(RequestPhase.EXPIRED, tracker.get(r.requestId)!!.phase)
    }

    @Test
    fun `unsupported version response fails the request`() {
        val r = tracker.create("system.status", "req_000000000010")
        val failed = tracker.apply(RemoteFrame.UnsupportedVersion("remote.response", r.requestId, "9"))!!
        assertEquals("unsupported_version", failed.errorCode)
    }

    @Test(expected = IllegalArgumentException::class)
    fun `request ids cannot be reused`() {
        tracker.create("system.status", "req_000000000011")
        tracker.create("system.status", "req_000000000011")
    }

    @Test
    fun `tracker stays bounded`() {
        val small = RemoteRequestTracker(clock = { now }, maxTracked = 10)
        repeat(50) {
            val r = small.create("system.status")
            small.apply(response(r.requestId, ResponseStatus.COMPLETED))
        }
        assertTrue(small.snapshot().size <= 10)
    }
}
