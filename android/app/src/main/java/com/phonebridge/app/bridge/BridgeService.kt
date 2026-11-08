package com.phonebridge.app.bridge

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.phonebridge.app.MainActivity
import com.phonebridge.app.R
import com.phonebridge.app.data.Prefs
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Foreground service that owns the WebSocket connection so Android does not
 * kill it while the app is backgrounded.
 */
class BridgeService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private lateinit var manager: ConnectionManager
    private var stateJob: kotlinx.coroutines.Job? = null
    private var locateJob: kotlinx.coroutines.Job? = null

    override fun onCreate() {
        super.onCreate()
        createChannel()
        startForegroundCompat(buildNotification(ConnectionState.Connecting))
        manager = ConnectionManager(
            settingsProvider = { Prefs.load(applicationContext) },
            scope = scope,
        )
        manager.start()
        CallMonitor.start(this)
        LocationMonitor.start(this)
        stateJob = scope.launch {
            BridgeRuntime.state.collectLatest { state -> updateNotification(state) }
        }
        locateJob = scope.launch(Dispatchers.Main) {
            BridgeRuntime.locateRequests.collect {
                LocationMonitor.sendImmediateFix(this@BridgeService)
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (!manager.isRunning) manager.start()
        LocationMonitor.stop(this)
        LocationMonitor.start(this)
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        stateJob?.cancel()
        locateJob?.cancel()
        CallMonitor.stop(this)
        LocationMonitor.stop(this)
        manager.stop()
        scope.cancel()
        running.set(false)
        super.onDestroy()
    }

    private fun startForegroundCompat(notification: Notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            var fgType = ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
            if (LocationMonitor.hasPermission(this)) {
                fgType = fgType or ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION
            }
            startForeground(NOTIFICATION_ID, notification, fgType)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun createChannel() {
        val manager = getSystemService(NotificationManager::class.java)
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.channel_bridge_name),
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = getString(R.string.channel_bridge_description)
            setShowBadge(false)
        }
        manager.createNotificationChannel(channel)
    }

    private fun buildNotification(state: ConnectionState): Notification {
        val intent = Intent(this, MainActivity::class.java)
        val pending = PendingIntent.getActivity(
            this,
            0,
            intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val stop = PendingIntent.getService(
            this,
            1,
            Intent(this, BridgeService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.notification_title))
            .setContentText(describe(state))
            .setSmallIcon(R.drawable.ic_bridge)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(pending)
            .addAction(0, getString(R.string.action_stop), stop)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
    }

    private fun updateNotification(state: ConnectionState) {
        getSystemService(NotificationManager::class.java).notify(
            NOTIFICATION_ID,
            buildNotification(state),
        )
    }

    private fun describe(state: ConnectionState): String = when (state) {
        is ConnectionState.Connected -> getString(R.string.state_connected, state.server)
        is ConnectionState.Connecting -> getString(R.string.state_connecting)
        is ConnectionState.Retrying -> getString(R.string.state_retrying, state.attempt)
        ConnectionState.Disconnected -> getString(R.string.state_disconnected)
    }

    companion object {
        private const val TAG = "BridgeService"
        private const val CHANNEL_ID = "phone_bridge_status"
        private const val NOTIFICATION_ID = 7401
        const val ACTION_STOP = "com.phonebridge.app.STOP"

        private val running = AtomicBoolean(false)

        fun start(context: Context) {
            val intent = Intent(context, BridgeService::class.java)
            if (running.compareAndSet(false, true)) {
                Log.i(TAG, "starting bridge service")
            }
            ContextCompat.startForegroundService(context, intent)
        }

        fun ensureRunning(context: Context) {
            if (!running.get()) start(context)
        }

        fun stop(context: Context) {
            running.set(false)
            context.stopService(Intent(context, BridgeService::class.java))
        }

        fun isRunning(): Boolean = running.get()
    }
}
