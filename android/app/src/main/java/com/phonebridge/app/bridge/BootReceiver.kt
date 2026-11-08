package com.phonebridge.app.bridge

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.phonebridge.app.data.Prefs

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_MY_PACKAGE_REPLACED,
            -> {
                val settings = Prefs.load(context)
                if (settings.startOnBoot && settings.isConfigured()) {
                    BridgeService.start(context)
                }
            }
        }
    }
}
