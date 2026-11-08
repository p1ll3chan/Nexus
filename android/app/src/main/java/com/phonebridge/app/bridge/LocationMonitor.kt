package com.phonebridge.app.bridge

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.BatteryManager
import android.os.Build
import android.os.Bundle
import android.os.Looper
import android.util.Log
import androidx.core.content.ContextCompat
import com.phonebridge.app.data.Prefs
import java.util.Locale

/**
 * Tracks device GPS/network location and battery telemetry and forwards fixes
 * over the PhoneBridge WebSocket connection.
 */
object LocationMonitor {

    private const val TAG = "LocationMonitor"
    private const val MIN_DISTANCE_METERS = 8f

    @Volatile
    private var registered = false
    private var listener: LocationListener? = null

    fun hasPermission(context: Context): Boolean {
        val fine = ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.ACCESS_FINE_LOCATION,
        ) == PackageManager.PERMISSION_GRANTED
        val coarse = ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.ACCESS_COARSE_LOCATION,
        ) == PackageManager.PERMISSION_GRANTED
        return fine || coarse
    }

    @SuppressLint("MissingPermission")
    fun start(context: Context) {
        if (registered) return
        val settings = Prefs.load(context)
        if (!settings.locationEnabled) return
        if (!hasPermission(context)) {
            Log.w(TAG, "location permission not granted yet")
            return
        }
        val manager = context.getSystemService(LocationManager::class.java) ?: return
        val intervalMs = (settings.locationIntervalSeconds.coerceIn(5, 3600) * 1000L)

        val locListener = object : LocationListener {
            override fun onLocationChanged(location: Location) {
                emitLocation(context.applicationContext, location)
            }

            @Deprecated("Deprecated in Java")
            override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) = Unit
            override fun onProviderEnabled(provider: String) = Unit
            override fun onProviderDisabled(provider: String) = Unit
        }

        val providers = buildList {
            add(LocationManager.GPS_PROVIDER)
            add(LocationManager.NETWORK_PROVIDER)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                add(LocationManager.FUSED_PROVIDER)
            }
        }.distinct()

        var anyRegistered = false
        for (provider in providers) {
            runCatching {
                if (manager.isProviderEnabled(provider)) {
                    manager.requestLocationUpdates(
                        provider,
                        intervalMs,
                        MIN_DISTANCE_METERS,
                        locListener,
                        Looper.getMainLooper(),
                    )
                    anyRegistered = true
                }
            }.onFailure { exc ->
                Log.w(TAG, "unable to subscribe to $provider: ${exc.message}")
            }
        }

        if (anyRegistered) {
            listener = locListener
            registered = true
            Log.i(TAG, "location monitoring started (interval=${intervalMs}ms)")
            sendBestLastKnown(context.applicationContext, manager)
        }
    }

    fun stop(context: Context) {
        if (!registered) return
        val manager = context.getSystemService(LocationManager::class.java)
        try {
            listener?.let { manager?.removeUpdates(it) }
        } catch (exc: Exception) {
            Log.w(TAG, "error stopping location monitor: ${exc.message}")
        } finally {
            listener = null
            registered = false
        }
    }

    @SuppressLint("MissingPermission")
    fun sendImmediateFix(context: Context): Boolean {
        val appContext = context.applicationContext
        if (!hasPermission(appContext)) return false
        val manager = appContext.getSystemService(LocationManager::class.java) ?: return false

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val provider = when {
                manager.isProviderEnabled(LocationManager.GPS_PROVIDER) -> LocationManager.GPS_PROVIDER
                manager.isProviderEnabled(LocationManager.NETWORK_PROVIDER) -> LocationManager.NETWORK_PROVIDER
                else -> LocationManager.PASSIVE_PROVIDER
            }
            runCatching {
                manager.getCurrentLocation(
                    provider,
                    null,
                    appContext.mainExecutor,
                ) { location ->
                    if (location != null) {
                        emitLocation(appContext, location)
                    } else {
                        sendBestLastKnown(appContext, manager)
                    }
                }
                return true
            }
        }
        return sendBestLastKnown(appContext, manager)
    }

    @SuppressLint("MissingPermission")
    private fun sendBestLastKnown(context: Context, manager: LocationManager): Boolean {
        val providers = listOf(
            LocationManager.GPS_PROVIDER,
            LocationManager.NETWORK_PROVIDER,
            LocationManager.PASSIVE_PROVIDER,
        )
        val best = providers.mapNotNull { provider ->
            runCatching { manager.getLastKnownLocation(provider) }.getOrNull()
        }.maxByOrNull { it.time } ?: return false

        emitLocation(context, best)
        return true
    }

    private fun emitLocation(context: Context, location: Location) {
        val settings = Prefs.load(context)
        if (!settings.isConfigured() || !settings.locationEnabled) return

        val (batteryLevel, isCharging) = readBattery(context)
        val event = LocationEvent(
            latitude = location.latitude,
            longitude = location.longitude,
            accuracy = if (location.hasAccuracy()) location.accuracy else 0f,
            battery = batteryLevel,
            charging = isCharging,
            altitude = if (location.hasAltitude()) location.altitude else null,
            speed = if (location.hasSpeed()) location.speed else null,
            bearing = if (location.hasBearing()) location.bearing else null,
            provider = location.provider ?: "gps",
            timestamp = if (location.time > 0) location.time / 1000.0 else MessageProtocol.now(),
        )

        if (BridgeRuntime.publish(OutboundEvent.Location(event))) {
            val summary = String.format(
                Locale.US,
                "%.5f, %.5f (±%.0fm)",
                event.latitude,
                event.longitude,
                event.accuracy,
            )
            BridgeRuntime.updateStats {
                it.copy(
                    locationsSent = it.locationsSent + 1,
                    lastLocationSummary = summary,
                )
            }
        }
    }

    fun readBattery(context: Context): Pair<Int?, Boolean> {
        return try {
            val intent = context.registerReceiver(
                null,
                IntentFilter(Intent.ACTION_BATTERY_CHANGED),
            )
            val level = intent?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
            val scale = intent?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
            val status = intent?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
            val pct = if (level >= 0 && scale > 0) {
                ((level * 100f) / scale).toInt().coerceIn(0, 100)
            } else {
                val bm = context.getSystemService(BatteryManager::class.java)
                val prop = bm?.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY) ?: -1
                if (prop in 0..100) prop else null
            }
            val charging = status == BatteryManager.BATTERY_STATUS_CHARGING ||
                status == BatteryManager.BATTERY_STATUS_FULL
            pct to charging
        } catch (_: Exception) {
            null to false
        }
    }
}
