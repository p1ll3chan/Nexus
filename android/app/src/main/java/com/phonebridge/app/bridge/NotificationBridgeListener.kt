package com.phonebridge.app.bridge

import android.app.Notification
import android.content.Context
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import com.phonebridge.app.data.Prefs

/**
 * Captures every posted notification and forwards it to the laptop. Requires
 * the user to grant notification access in system settings.
 */
class NotificationBridgeListener : NotificationListenerService() {

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        val settings = Prefs.load(this)
        if (!settings.isConfigured() || !settings.notificationsEnabled) return
        if (sbn.packageName == packageName) return
        if (sbn.notification.flags and Notification.FLAG_GROUP_SUMMARY != 0) return

        val extras = sbn.notification.extras
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
        val text = sequenceOf(
            extras.getCharSequence(Notification.EXTRA_TEXT),
            extras.getCharSequence(Notification.EXTRA_BIG_TEXT),
            extras.getCharSequence(Notification.EXTRA_SUMMARY_TEXT),
        ).firstOrNull { !it.isNullOrBlank() }?.toString().orEmpty()

        if (title.isBlank() && text.isBlank()) return

        val appName = appLabel(sbn.packageName)
        if (settings.socialOnlyNotifications &&
            !isSocialOrCommunicationNotification(
                pkg = sbn.packageName,
                appName = appName,
                category = sbn.notification.category,
            )
        ) {
            BridgeRuntime.updateStats {
                it.copy(notificationsSkipped = it.notificationsSkipped + 1)
            }
            return
        }

        val event = NotificationEvent(
            app = appName,
            title = title,
            text = text,
            packageName = sbn.packageName,
            ongoing = sbn.isOngoing,
            lowPriority = sbn.notification.priority <= Notification.PRIORITY_LOW,
            timestamp = sbn.postTime / 1000.0,
        )

        if (!BridgeRuntime.publish(OutboundEvent.Notification(event))) {
            Log.w(TAG, "event buffer full, dropping notification from ${sbn.packageName}")
            return
        }
        BridgeRuntime.updateStats {
            it.copy(notificationsSent = it.notificationsSent + 1)
        }
        BridgeService.ensureRunning(this)
    }

    private fun appLabel(pkg: String): String = try {
        val info = packageManager.getApplicationInfo(pkg, 0)
        packageManager.getApplicationLabel(info).toString()
    } catch (_: Exception) {
        pkg.substringAfterLast('.').replaceFirstChar { it.uppercase() }
    }

    companion object {
        private const val TAG = "NotifBridge"

        private val SYSTEM_IGNORED_PACKAGES = setOf(
            "android",
            "com.android.systemui",
            "com.android.settings",
            "com.android.vending",
            "com.android.providers.downloads",
            "com.google.android.gms",
            "com.google.android.gsf",
            "com.google.android.deskclock",
            "com.google.android.googlequicksearchbox",
        )

        private val ALLOWED_PACKAGES = setOf(
            // Messaging & communication
            "com.whatsapp",
            "com.whatsapp.w4b",
            "org.telegram.messenger",
            "org.thunderdog.challegram",
            "org.thoughtcrime.securesms",
            "com.discord",
            "com.slack",
            "com.Slack",
            "com.google.android.apps.messaging",
            "com.android.mms",
            "com.samsung.android.messaging",
            "com.facebook.orca",
            "com.facebook.mlite",
            "com.microsoft.teams",
            "com.google.android.apps.dynamite",
            "com.google.android.apps.meetings",
            "us.zoom.videomeetings",
            "com.skype.raider",
            "com.viber.voip",
            "jp.naver.line.android",
            "com.tencent.mm",
            "im.vector.app",
            "ch.threema.app",
            "network.loki.messenger",
            "org.briarproject.briar.android",
            "com.mattermost.rn",
            "com.zulipmobile",
            // Email
            "com.google.android.gm",
            "com.microsoft.office.outlook",
            "ch.protonmail.android",
            "net.thunderbird.android",
            "com.fsck.k9",
            "de.tutao.tutanota",
            "com.yahoo.mobile.client.android.mail",
            "com.readdle.spark",
            "eu.faircode.email",
            "me.bluemail.mail",
            "com.fastmail.app",
            "com.samsung.android.email.provider",
            "com.android.email",
            // Social media
            "com.instagram.android",
            "com.instagram.barcelona",
            "com.twitter.android",
            "com.reddit.frontpage",
            "com.linkedin.android",
            "com.facebook.katana",
            "com.facebook.lite",
            "com.snapchat.android",
            "org.joinmastodon.android",
            "xyz.blueskyweb.app",
            // Phone / Dialer
            "com.google.android.dialer",
            "com.android.dialer",
            "com.samsung.android.incallui",
        )

        private val ALLOWED_KEYWORDS = setOf(
            "whatsapp",
            "telegram",
            "signal",
            "discord",
            "slack",
            "messages",
            "messenger",
            "teams",
            "zoom",
            "skype",
            "viber",
            "wechat",
            "element",
            "threema",
            "gmail",
            "outlook",
            "protonmail",
            "thunderbird",
            "tutanota",
            "tuta",
            "fairemail",
            "bluemail",
            "fastmail",
            "email",
            "mail",
            "instagram",
            "threads",
            "twitter",
            "reddit",
            "linkedin",
            "facebook",
            "snapchat",
            "mastodon",
            "bluesky",
        )

        private val ALLOWED_CATEGORIES = setOf(
            Notification.CATEGORY_MESSAGE,
            Notification.CATEGORY_EMAIL,
            Notification.CATEGORY_CALL,
            Notification.CATEGORY_SOCIAL,
        )

        fun isSocialOrCommunicationNotification(
            pkg: String,
            appName: String,
            category: String?,
        ): Boolean {
            val pkgNorm = pkg.trim().lowercase()
            if (pkgNorm in SYSTEM_IGNORED_PACKAGES) return false
            if (pkg in ALLOWED_PACKAGES || pkgNorm in ALLOWED_PACKAGES) return true
            if (category != null && category in ALLOWED_CATEGORIES) return true
            val appNorm = appName.trim().lowercase()
            if (appNorm == "x" || appNorm == "line") return true
            val tokens = appNorm.split(Regex("[^a-z0-9]+")).filter { it.isNotEmpty() }
            return tokens.any { it in ALLOWED_KEYWORDS }
        }

        fun isEnabled(context: Context): Boolean {
            val flat = android.provider.Settings.Secure.getString(
                context.contentResolver,
                "enabled_notification_listeners",
            ) ?: return false
            return flat.split(':').any { it.contains(context.packageName) }
        }
    }
}
