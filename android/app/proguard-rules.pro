# Keep the notification listener and service entry points referenced from XML.
-keep class com.phonebridge.app.bridge.NotificationBridgeListener { *; }
-keep class com.phonebridge.app.bridge.BridgeService { *; }
-keep class com.phonebridge.app.bridge.BootReceiver { *; }

# OkHttp
-dontwarn okhttp3.**
-dontwarn okio.**
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**
