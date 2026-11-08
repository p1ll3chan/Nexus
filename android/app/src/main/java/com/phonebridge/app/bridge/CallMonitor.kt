package com.phonebridge.app.bridge

import android.content.Context
import android.os.Build
import android.telephony.PhoneStateListener
import android.telephony.TelephonyCallback
import android.telephony.TelephonyManager
import android.util.Log
import com.phonebridge.app.data.Prefs

/**
 * Watches the telephony call state and emits RINGING / OFFHOOK / IDLE events.
 * Requires the READ_PHONE_STATE runtime permission.
 */
object CallMonitor {

    private const val TAG = "CallMonitor"

    @Volatile
    private var lastState: Int = TelephonyManager.CALL_STATE_IDLE
    private var callback: TelephonyCallback? = null
    private var legacyListener: PhoneStateListener? = null
    private var registered = false

    fun start(context: Context) {
        if (registered) return
        val manager = context.getSystemService(TelephonyManager::class.java) ?: return
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                val listener = object : TelephonyCallback(), TelephonyCallback.CallStateListener {
                    override fun onCallStateChanged(state: Int) = handle(context, state, "")
                }
                manager.registerTelephonyCallback(context.mainExecutor, listener)
                callback = listener
            } else {
                @Suppress("DEPRECATION")
                val listener = object : PhoneStateListener() {
                    @Deprecated("Deprecated in Java")
                    override fun onCallStateChanged(state: Int, phoneNumber: String?) {
                        handle(context, state, phoneNumber.orEmpty())
                    }
                }
                @Suppress("DEPRECATION")
                manager.listen(listener, PhoneStateListener.LISTEN_CALL_STATE)
                legacyListener = listener
            }
            registered = true
            Log.i(TAG, "call monitoring started")
        } catch (exc: SecurityException) {
            Log.w(TAG, "READ_PHONE_STATE not granted: ${exc.message}")
        } catch (exc: Exception) {
            Log.w(TAG, "cannot register call monitor: ${exc.message}")
        }
    }

    fun stop(context: Context) {
        if (!registered) return
        val manager = context.getSystemService(TelephonyManager::class.java)
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                callback?.let { manager?.unregisterTelephonyCallback(it) }
            } else {
                @Suppress("DEPRECATION")
                legacyListener?.let { manager?.listen(it, PhoneStateListener.LISTEN_NONE) }
            }
        } catch (exc: Exception) {
            Log.w(TAG, "error stopping call monitor: ${exc.message}")
        } finally {
            callback = null
            legacyListener = null
            registered = false
            lastState = TelephonyManager.CALL_STATE_IDLE
        }
    }

    private fun handle(context: Context, state: Int, number: String) {
        if (state == lastState) return
        lastState = state
        val settings = Prefs.load(context)
        if (!settings.isConfigured() || !settings.callsEnabled) return
        val event = CallEvent(
            state = CallState.fromAndroid(state),
            number = number,
        )
        if (BridgeRuntime.publish(OutboundEvent.Call(event))) {
            BridgeRuntime.updateStats { it.copy(callsSent = it.callsSent + 1) }
        }
    }
}
