package com.phonebridge.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.google.android.material.snackbar.Snackbar
import com.phonebridge.app.bridge.BridgeRuntime
import com.phonebridge.app.bridge.BridgeService
import com.phonebridge.app.bridge.CallState
import com.phonebridge.app.bridge.CallEvent
import com.phonebridge.app.bridge.ConnectionState
import com.phonebridge.app.bridge.LocationEvent
import com.phonebridge.app.bridge.LocationMonitor
import com.phonebridge.app.bridge.NotificationBridgeListener
import com.phonebridge.app.bridge.NotificationEvent
import com.phonebridge.app.bridge.OutboundEvent
import com.phonebridge.app.data.BridgeSettings
import com.phonebridge.app.data.Prefs
import com.phonebridge.app.databinding.ActivityMainBinding
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding

    private val permissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { grants ->
            if (grants.values.any { !it }) {
                Snackbar.make(binding.root, R.string.permission_denied, Snackbar.LENGTH_LONG).show()
            }
            updatePermissionButtons()
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        populate(Prefs.load(this))

        binding.saveButton.setOnClickListener { saveAndConnect() }
        binding.stopButton.setOnClickListener { BridgeService.stop(this) }
        binding.notifAccessButton.setOnClickListener {
            startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
        }
        binding.phonePermissionButton.setOnClickListener { requestPhonePermissions() }
        binding.locationPermissionButton.setOnClickListener { requestLocationPermissions() }
        binding.testButton.setOnClickListener { sendTestNotification() }
        binding.remoteButton.setOnClickListener {
            startActivity(Intent(this, com.phonebridge.app.remote.RemoteActivity::class.java))
        }

        observeRuntime()
    }

    override fun onResume() {
        super.onResume()
        updatePermissionButtons()
    }

    private fun updatePermissionButtons() {
        binding.notifAccessButton.text = if (NotificationBridgeListener.isEnabled(this)) {
            getString(R.string.notif_access_granted)
        } else {
            getString(R.string.action_notif_access)
        }
        binding.locationPermissionButton.text = if (LocationMonitor.hasPermission(this)) {
            getString(R.string.location_permission_granted)
        } else {
            getString(R.string.action_location_permission)
        }
    }

    private fun populate(settings: BridgeSettings) {
        binding.hostInput.setText(settings.host)
        binding.portInput.setText(settings.port.toString())
        binding.tokenInput.setText(settings.token)
        binding.credentialInput.setText(settings.deviceCredential)
        binding.deviceInput.setText(settings.deviceName)
        binding.locationIntervalInput.setText(settings.locationIntervalSeconds.toString())
        binding.notificationsSwitch.isChecked = settings.notificationsEnabled
        binding.socialOnlySwitch.isChecked = settings.socialOnlyNotifications
        binding.callsSwitch.isChecked = settings.callsEnabled
        binding.locationSwitch.isChecked = settings.locationEnabled
        binding.bootSwitch.isChecked = settings.startOnBoot
    }

    private fun readSettings(): BridgeSettings {
        val port = binding.portInput.text?.toString()?.toIntOrNull()
            ?: BridgeSettings.DEFAULT_PORT
        val interval = binding.locationIntervalInput.text?.toString()?.toIntOrNull()
            ?: BridgeSettings.DEFAULT_LOCATION_INTERVAL_SEC
        return BridgeSettings(
            host = binding.hostInput.text?.toString()?.trim().orEmpty(),
            port = port,
            token = binding.tokenInput.text?.toString()?.trim().orEmpty(),
            deviceCredential = binding.credentialInput.text?.toString()?.trim().orEmpty(),
            deviceName = binding.deviceInput.text?.toString()?.trim()
                .takeUnless { it.isNullOrBlank() } ?: BridgeSettings.defaultDeviceName(),
            notificationsEnabled = binding.notificationsSwitch.isChecked,
            socialOnlyNotifications = binding.socialOnlySwitch.isChecked,
            callsEnabled = binding.callsSwitch.isChecked,
            locationEnabled = binding.locationSwitch.isChecked,
            locationIntervalSeconds = interval.coerceIn(5, 3600),
            startOnBoot = binding.bootSwitch.isChecked,
        )
    }

    private fun saveAndConnect() {
        val settings = readSettings()
        if (settings.deviceCredential.isNotBlank() && settings.credential() == null) {
            binding.credentialLayout.error = getString(R.string.error_bad_credential)
            return
        }
        binding.credentialLayout.error = null
        if (!settings.isConfigured()) {
            Snackbar.make(binding.root, R.string.error_incomplete, Snackbar.LENGTH_LONG).show()
            return
        }
        Prefs.save(this, settings)
        requestNotificationPermission()
        if (settings.locationEnabled && !LocationMonitor.hasPermission(this)) {
            requestLocationPermissions()
        }
        BridgeService.start(this)
        Snackbar.make(binding.root, R.string.saved_connecting, Snackbar.LENGTH_SHORT).show()
    }

    private fun requestPhonePermissions() {
        val permissions = mutableListOf(Manifest.permission.READ_PHONE_STATE)
        if (binding.callsSwitch.isChecked) {
            permissions += Manifest.permission.READ_CALL_LOG
        }
        permissionLauncher.launch(permissions.toTypedArray())
    }

    private fun requestLocationPermissions() {
        val permissions = arrayOf(
            Manifest.permission.ACCESS_FINE_LOCATION,
            Manifest.permission.ACCESS_COARSE_LOCATION,
        )
        permissionLauncher.launch(permissions)
    }

    private fun requestNotificationPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val granted = ContextCompat.checkSelfPermission(
            this,
            Manifest.permission.POST_NOTIFICATIONS,
        ) == PackageManager.PERMISSION_GRANTED
        if (!granted) {
            permissionLauncher.launch(arrayOf(Manifest.permission.POST_NOTIFICATIONS))
        }
    }

    private fun sendTestNotification() {
        val settings = readSettings()
        if (!settings.isConfigured()) {
            Snackbar.make(binding.root, R.string.error_incomplete, Snackbar.LENGTH_LONG).show()
            return
        }
        Prefs.save(this, settings)
        BridgeService.start(this)
        val event = NotificationEvent(
            app = "WhatsApp",
            title = "Test notification",
            text = "Hello from ${settings.deviceName}",
            packageName = "com.whatsapp",
        )
        BridgeRuntime.publish(OutboundEvent.Notification(event))
        BridgeRuntime.publish(OutboundEvent.Call(CallEvent(CallState.RINGING, name = "Test Caller")))
        if (!LocationMonitor.sendImmediateFix(this)) {
            val (battery, charging) = LocationMonitor.readBattery(this)
            BridgeRuntime.publish(
                OutboundEvent.Location(
                    LocationEvent(
                        latitude = 37.7749,
                        longitude = -122.4194,
                        accuracy = 10f,
                        battery = battery,
                        charging = charging,
                        provider = "test",
                    )
                )
            )
        }
        Snackbar.make(binding.root, R.string.test_sent, Snackbar.LENGTH_SHORT).show()
    }

    private fun observeRuntime() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                launch {
                    BridgeRuntime.state.collect { state ->
                        binding.statusText.text = describe(state)
                    }
                }
                launch {
                    BridgeRuntime.stats.collect { stats ->
                        val connected = stats.connectedSince
                        val since = if (connected > 0) {
                            val minutes = (System.currentTimeMillis() - connected) / 60_000
                            getString(R.string.stat_connected_for, minutes)
                        } else {
                            ""
                        }
                        val locLine = if (stats.lastLocationSummary.isNotBlank()) {
                            "\nGPS: ${stats.lastLocationSummary}"
                        } else {
                            ""
                        }
                        binding.statsText.text = getString(
                            R.string.stats_format,
                            stats.notificationsSent,
                            stats.notificationsSkipped,
                            stats.callsSent,
                            stats.locationsSent,
                            stats.reconnects,
                        ) + since + locLine
                        binding.statsText.visibility = android.view.View.VISIBLE
                    }
                }
            }
        }
    }

    private fun describe(state: ConnectionState): String = when (state) {
        is ConnectionState.Connected -> getString(R.string.state_connected, state.server)
        is ConnectionState.Connecting -> getString(R.string.state_connecting)
        is ConnectionState.Retrying -> getString(
            R.string.state_retrying_detail,
            state.attempt,
            state.reason,
        )
        ConnectionState.Disconnected -> getString(R.string.state_disconnected)
    }
}
