package com.phonebridge.app.remote

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.AdapterView
import android.widget.ArrayAdapter
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.phonebridge.app.R
import com.phonebridge.app.bridge.BridgeRuntime
import com.phonebridge.app.bridge.ConnectionState
import com.phonebridge.app.databinding.ActivityRemoteBinding
import com.phonebridge.app.databinding.ItemProcessBinding
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch
import kotlin.math.min

/**
 * Nexus Remote screen: read-only Linux status and processes.
 *
 * Polling runs only while the screen is STARTED (visible) and the agent has
 * announced remote capability. Every poll is a new request_id; failed requests are
 * never re-sent, and the next poll backs off (5s -> 10s -> 20s -> 30s).
 */
class RemoteActivity : AppCompatActivity() {

    private lateinit var binding: ActivityRemoteBinding
    private val adapter = ProcessAdapter { onProcessClicked(it) }

    private var lastStatusMs = 0L
    private var lastStatus: SystemStatus? = null
    private var statusError: String = ""
    private var processes = mutableListOf<ProcessSummary>()
    private var processTotal = 0
    private var processJob: Job? = null
    private var detailDialog: AlertDialog? = null

    private val sortKeys = listOf("memory", "cpu_time", "pid", "name")

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityRemoteBinding.inflate(layoutInflater)
        setContentView(binding.root)
        title = getString(R.string.remote_title)

        binding.processList.layoutManager = LinearLayoutManager(this)
        binding.processList.adapter = adapter

        binding.processSort.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            resources.getStringArray(R.array.remote_sort_options),
        )
        binding.processSort.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            private var first = true
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, pos: Int, id: Long) {
                if (first) { first = false; return }
                loadProcesses(reset = true)
            }
            override fun onNothingSelected(parent: AdapterView<*>?) = Unit
        }
        binding.processOwnOnly.setOnCheckedChangeListener { _, _ -> loadProcesses(reset = true) }
        binding.processRefresh.setOnClickListener { loadProcesses(reset = true) }
        binding.processMore.setOnClickListener { loadProcesses(reset = false) }

        observe()
    }

    override fun onDestroy() {
        detailDialog?.dismiss()
        super.onDestroy()
    }

    private fun observe() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                launch {
                    combine(BridgeRuntime.state, RemoteRuntime.availability) { s, a -> s to a }
                        .collect { (state, availability) -> renderHeader(state, availability) }
                }
                launch { pollStatus() }
                launch {
                    // Ticker for "updated Xs ago" and stale detection.
                    while (true) {
                        renderStatus()
                        delay(1_000)
                    }
                }
                launch {
                    RemoteRuntime.availability.collect { availability ->
                        if (availability is RemoteAvailability.Available &&
                            availability.caps.supports(OP_PROCESS_LIST) && processes.isEmpty()
                        ) {
                            loadProcesses(reset = true)
                        }
                    }
                }
            }
        }
    }

    // ---------------------------------------------------------------- header

    private fun renderHeader(state: ConnectionState, availability: RemoteAvailability) {
        binding.remoteConnection.text = when (state) {
            is ConnectionState.Connected -> getString(R.string.state_connected, state.server)
            ConnectionState.Connecting -> getString(R.string.state_connecting)
            is ConnectionState.Retrying -> getString(R.string.state_retrying, state.attempt)
            ConnectionState.Disconnected -> getString(R.string.state_disconnected)
        }
        val (identity, banner) = when (availability) {
            is RemoteAvailability.Available -> {
                val caps = availability.caps
                getString(
                    R.string.remote_identity,
                    caps.deviceId,
                    caps.permissions.joinToString().ifBlank { getString(R.string.remote_no_permissions) },
                ) to if (caps.operations.isEmpty()) getString(R.string.remote_banner_no_ops) else ""
            }
            is RemoteAvailability.Unavailable ->
                getString(R.string.remote_identity_legacy) to getString(R.string.remote_banner_legacy)
            RemoteAvailability.Unknown ->
                getString(R.string.remote_identity_unknown) to
                    if (state is ConnectionState.Connected) getString(R.string.remote_banner_unsupported)
                    else getString(R.string.remote_banner_offline)
        }
        binding.remoteIdentity.text = identity
        binding.remoteBanner.text = banner
        binding.remoteBanner.visibility = if (banner.isBlank()) View.GONE else View.VISIBLE
        val canList = availability is RemoteAvailability.Available && availability.caps.supports(OP_PROCESS_LIST)
        binding.processRefresh.isEnabled = canList
        binding.processSort.isEnabled = canList
        binding.processOwnOnly.isEnabled = canList
    }

    // ---------------------------------------------------------------- status

    private suspend fun pollStatus() {
        var backoffMs = POLL_MS
        while (true) {
            val availability = RemoteRuntime.availability.value
            if (availability !is RemoteAvailability.Available || !availability.caps.supports(OP_STATUS)) {
                delay(1_000)
                continue
            }
            val result = RemoteRuntime.call(OP_STATUS, timeoutMs = REQUEST_TIMEOUT_MS)
            if (result.phase == RequestPhase.COMPLETED && result.result != null) {
                lastStatus = SystemStatus.from(result.result)
                lastStatusMs = System.currentTimeMillis()
                statusError = ""
                backoffMs = POLL_MS
            } else {
                statusError = RemoteFormat.error(result.errorCode, result.errorMessage)
                backoffMs = min(MAX_BACKOFF_MS, backoffMs * 2)
            }
            renderStatus()
            delay(backoffMs)
        }
    }

    private fun renderStatus() {
        val status = lastStatus
        val now = System.currentTimeMillis()
        val stale = status != null && now - lastStatusMs > STALE_MS
        binding.statusState.text = when {
            status == null && statusError.isNotBlank() -> statusError
            status == null -> getString(R.string.remote_status_loading)
            statusError.isNotBlank() -> getString(R.string.remote_status_error_stale, statusError, RemoteFormat.age(now, lastStatusMs))
            stale -> getString(R.string.remote_status_stale, RemoteFormat.age(now, lastStatusMs))
            else -> getString(R.string.remote_status_updated, RemoteFormat.age(now, lastStatusMs))
        }
        if (status == null) return
        val alpha = if (stale || statusError.isNotBlank()) 0.5f else 1f
        listOf(binding.statusCpu, binding.statusMemory, binding.statusDisks, binding.statusUptime)
            .forEach { it.alpha = alpha }
        binding.statusCpu.text = getString(
            R.string.remote_cpu,
            RemoteFormat.percent(status.cpuPercent),
            status.cpuCount ?: 0,
            RemoteFormat.load(status.load),
        )
        binding.statusCpuBar.setProgressCompat((status.cpuPercent ?: 0.0).toInt().coerceIn(0, 100), false)
        binding.statusMemory.text = getString(
            R.string.remote_memory,
            RemoteFormat.bytes(status.memUsed),
            RemoteFormat.bytes(status.memTotal),
            RemoteFormat.percent(status.memPercent),
        )
        binding.statusMemoryBar.setProgressCompat((status.memPercent ?: 0.0).toInt().coerceIn(0, 100), false)
        binding.statusDisks.text = if (status.disks.isEmpty()) getString(R.string.remote_no_disks) else
            status.disks.joinToString("\n") {
                getString(
                    R.string.remote_disk,
                    it.label,
                    RemoteFormat.bytes(it.usedBytes),
                    RemoteFormat.bytes(it.totalBytes),
                    RemoteFormat.percent(it.percent),
                )
            }
        binding.statusUptime.text = getString(
            R.string.remote_uptime,
            RemoteFormat.duration(status.uptimeSeconds),
            status.host,
            status.kernel,
        )
    }

    // ---------------------------------------------------------------- processes

    private fun loadProcesses(reset: Boolean) {
        val availability = RemoteRuntime.availability.value
        if (availability !is RemoteAvailability.Available || !availability.caps.supports(OP_PROCESS_LIST)) return
        if (processJob?.isActive == true) return
        val offset = if (reset) 0 else processes.size
        binding.processState.text = getString(R.string.remote_loading)
        binding.processMore.isEnabled = false
        processJob = lifecycleScope.launch {
            val params = mapOf(
                "limit" to PAGE_SIZE,
                "offset" to offset,
                "sort_by" to sortKeys.getOrElse(binding.processSort.selectedItemPosition) { "memory" },
                "order" to if (binding.processSort.selectedItemPosition >= 2) "asc" else "desc",
                "own_only" to binding.processOwnOnly.isChecked,
            )
            val result = RemoteRuntime.call(OP_PROCESS_LIST, params, REQUEST_TIMEOUT_MS)
            if (result.phase == RequestPhase.COMPLETED && result.result != null) {
                val page = ProcessPage.from(result.result)
                if (reset) processes.clear()
                processes.addAll(page.items)
                processTotal = page.total
                adapter.submit(processes.toList())
                binding.processState.text = when {
                    processes.isEmpty() -> getString(R.string.remote_no_processes)
                    else -> getString(R.string.remote_process_count, processes.size, page.total) +
                        (if (page.truncated) " · " + getString(R.string.remote_truncated) else "") +
                        (if (page.inaccessible > 0) " · " + getString(R.string.remote_inaccessible, page.inaccessible) else "")
                }
            } else {
                binding.processState.text = RemoteFormat.error(result.errorCode, result.errorMessage)
            }
            val hasMore = processes.size < processTotal && processes.size < MAX_ROWS
            binding.processMore.visibility = if (hasMore) View.VISIBLE else View.GONE
            binding.processMore.isEnabled = true
        }
    }

    private fun onProcessClicked(process: ProcessSummary) {
        val availability = RemoteRuntime.availability.value
        if (availability !is RemoteAvailability.Available || !availability.caps.supports(OP_PROCESS_INSPECT)) return
        detailDialog?.dismiss()
        val dialog = MaterialAlertDialogBuilder(this)
            .setTitle(getString(R.string.remote_detail_title, process.name, process.pid))
            .setMessage(R.string.remote_loading)
            .setPositiveButton(android.R.string.ok, null)
            .show()
        detailDialog = dialog
        lifecycleScope.launch {
            val params = buildMap<String, Any?> {
                put("pid", process.pid)
                process.startTicks?.let { put("start_ticks", it) } // PID-reuse guard
            }
            val result = RemoteRuntime.call(OP_PROCESS_INSPECT, params, REQUEST_TIMEOUT_MS)
            if (!dialog.isShowing) return@launch
            dialog.setMessage(
                if (result.phase == RequestPhase.COMPLETED && result.result != null) {
                    val d = ProcessDetail.from(result.result)
                    getString(
                        R.string.remote_detail_body,
                        d.summary.pid,
                        d.ppid,
                        d.summary.state,
                        if (d.summary.own) getString(R.string.remote_owner_you) else getString(R.string.remote_owner_other),
                        RemoteFormat.bytes(d.summary.rssBytes),
                        RemoteFormat.bytes(d.vmsBytes),
                        RemoteFormat.percent(d.summary.memPercent),
                        RemoteFormat.duration(d.summary.cpuTimeSeconds),
                        RemoteFormat.percent(d.summary.avgCpuPercent),
                        d.summary.threads,
                        d.nice,
                        RemoteFormat.duration(d.summary.runtimeSeconds),
                    )
                } else {
                    RemoteFormat.error(result.errorCode, result.errorMessage)
                }
            )
        }
    }

    // ---------------------------------------------------------------- adapter

    private class ProcessAdapter(
        private val onClick: (ProcessSummary) -> Unit,
    ) : RecyclerView.Adapter<ProcessAdapter.Holder>() {
        private var items: List<ProcessSummary> = emptyList()

        fun submit(newItems: List<ProcessSummary>) {
            items = newItems
            @Suppress("NotifyDataSetChanged")
            notifyDataSetChanged()
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
            Holder(ItemProcessBinding.inflate(LayoutInflater.from(parent.context), parent, false))

        override fun getItemCount() = items.size

        override fun onBindViewHolder(holder: Holder, position: Int) {
            val p = items[position]
            val ctx = holder.binding.root.context
            holder.binding.processName.text = p.name.ifBlank { "?" }
            holder.binding.processMeta.text = ctx.getString(
                R.string.remote_process_meta,
                p.pid,
                RemoteFormat.bytes(p.rssBytes),
                RemoteFormat.duration(p.cpuTimeSeconds),
                p.state,
                if (p.own) ctx.getString(R.string.remote_owner_you) else ctx.getString(R.string.remote_owner_other),
            )
            holder.binding.root.contentDescription = ctx.getString(
                R.string.remote_process_a11y, p.name, p.pid, RemoteFormat.bytes(p.rssBytes),
            )
            holder.binding.root.setOnClickListener { onClick(p) }
        }

        class Holder(val binding: ItemProcessBinding) : RecyclerView.ViewHolder(binding.root)
    }

    companion object {
        const val OP_STATUS = "system.status"
        const val OP_PROCESS_LIST = "process.list"
        const val OP_PROCESS_INSPECT = "process.inspect"
        private const val POLL_MS = 5_000L
        private const val MAX_BACKOFF_MS = 30_000L
        private const val STALE_MS = 15_000L
        private const val REQUEST_TIMEOUT_MS = 12_000L
        private const val PAGE_SIZE = 25
        private const val MAX_ROWS = 200
    }
}
