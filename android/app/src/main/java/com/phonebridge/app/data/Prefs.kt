package com.phonebridge.app.data

import android.content.Context
import android.os.Build

data class BridgeSettings(
    val host: String = "",
    val port: Int = DEFAULT_PORT,
    val token: String = "",
    val deviceName: String = defaultDeviceName(),
    val notificationsEnabled: Boolean = true,
    val socialOnlyNotifications: Boolean = true,
    val callsEnabled: Boolean = true,
    val locationEnabled: Boolean = true,
    val locationIntervalSeconds: Int = DEFAULT_LOCATION_INTERVAL_SEC,
    val startOnBoot: Boolean = false,
) {
    fun isConfigured(): Boolean =
        host.isNotBlank() && port in 1..65535 && token.isNotBlank()

    fun webSocketUrl(): String {
        var value = host.trim()
        listOf("ws://", "wss://", "http://", "https://").forEach { scheme ->
            if (value.startsWith(scheme, ignoreCase = true)) {
                value = value.substring(scheme.length)
            }
        }
        value = value.substringBefore('/').trim().trimEnd(':')
        return if (value.contains(':')) "ws://$value" else "ws://$value:$port"
    }

    companion object {
        const val DEFAULT_PORT = 8765
        const val DEFAULT_LOCATION_INTERVAL_SEC = 60

        fun defaultDeviceName(): String {
            val model = Build.MODEL.ifBlank { "android-phone" }
            return model.replace(Regex("[^A-Za-z0-9]+"), "-").trim('-')
        }
    }
}

object Prefs {

    private const val FILE = "phone_bridge_prefs"
    private const val KEY_HOST = "host"
    private const val KEY_PORT = "port"
    private const val KEY_TOKEN = "token"
    private const val KEY_DEVICE = "device_name"
    private const val KEY_NOTIFICATIONS = "notifications_enabled"
    private const val KEY_SOCIAL_ONLY = "social_only_notifications"
    private const val KEY_CALLS = "calls_enabled"
    private const val KEY_LOCATION = "location_enabled"
    private const val KEY_LOCATION_INTERVAL = "location_interval_seconds"
    private const val KEY_BOOT = "start_on_boot"

    fun load(context: Context): BridgeSettings {
        val prefs = context.getSharedPreferences(FILE, Context.MODE_PRIVATE)
        return BridgeSettings(
            host = prefs.getString(KEY_HOST, "") ?: "",
            port = prefs.getInt(KEY_PORT, BridgeSettings.DEFAULT_PORT),
            token = prefs.getString(KEY_TOKEN, "") ?: "",
            deviceName = prefs.getString(KEY_DEVICE, BridgeSettings.defaultDeviceName())
                ?: BridgeSettings.defaultDeviceName(),
            notificationsEnabled = prefs.getBoolean(KEY_NOTIFICATIONS, true),
            socialOnlyNotifications = prefs.getBoolean(KEY_SOCIAL_ONLY, true),
            callsEnabled = prefs.getBoolean(KEY_CALLS, true),
            locationEnabled = prefs.getBoolean(KEY_LOCATION, true),
            locationIntervalSeconds = prefs.getInt(
                KEY_LOCATION_INTERVAL,
                BridgeSettings.DEFAULT_LOCATION_INTERVAL_SEC,
            ).coerceIn(5, 3600),
            startOnBoot = prefs.getBoolean(KEY_BOOT, false),
        )
    }

    fun save(context: Context, settings: BridgeSettings) {
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit()
            .putString(KEY_HOST, settings.host)
            .putInt(KEY_PORT, settings.port)
            .putString(KEY_TOKEN, settings.token)
            .putString(KEY_DEVICE, settings.deviceName)
            .putBoolean(KEY_NOTIFICATIONS, settings.notificationsEnabled)
            .putBoolean(KEY_SOCIAL_ONLY, settings.socialOnlyNotifications)
            .putBoolean(KEY_CALLS, settings.callsEnabled)
            .putBoolean(KEY_LOCATION, settings.locationEnabled)
            .putInt(KEY_LOCATION_INTERVAL, settings.locationIntervalSeconds.coerceIn(5, 3600))
            .putBoolean(KEY_BOOT, settings.startOnBoot)
            .apply()
    }
}
