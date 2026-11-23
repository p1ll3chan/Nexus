package com.phonebridge.app.data

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import android.util.Log
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Encrypts small secrets (the Nexus device credential) with a non-exportable
 * AES-256-GCM key held in the Android Keystore. Only ciphertext is written to
 * SharedPreferences.
 */
object SecretStore {
    private const val TAG = "SecretStore"
    private const val KEYSTORE = "AndroidKeyStore"
    private const val ALIAS = "nexus_device_credential_v1"
    private const val TRANSFORMATION = "AES/GCM/NoPadding"
    private const val IV_BYTES = 12
    private const val TAG_BITS = 128

    private fun key(): SecretKey {
        val ks = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (ks.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(
                ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build()
        )
        return generator.generateKey()
    }

    fun encrypt(plain: String): String {
        if (plain.isEmpty()) return ""
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val out = cipher.iv + cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(out, Base64.NO_WRAP)
    }

    /** Returns "" if the blob is empty or cannot be decrypted (e.g. key was reset). */
    fun decrypt(blob: String): String {
        if (blob.isEmpty()) return ""
        return try {
            val raw = Base64.decode(blob, Base64.NO_WRAP)
            if (raw.size <= IV_BYTES) return ""
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(
                Cipher.DECRYPT_MODE,
                key(),
                GCMParameterSpec(TAG_BITS, raw.copyOfRange(0, IV_BYTES)),
            )
            String(cipher.doFinal(raw.copyOfRange(IV_BYTES, raw.size)), Charsets.UTF_8)
        } catch (e: Exception) {
            Log.w(TAG, "could not decrypt stored credential; it must be re-entered")
            ""
        }
    }
}

/** `dev_<16 hex>.<secret>` as printed by `phone-bridge remote pair`. */
data class DeviceCredential(val deviceId: String, val secret: String) {
    override fun toString(): String = "DeviceCredential($deviceId, secret=****)"

    companion object {
        private val PATTERN = Regex("^(dev_[0-9a-f]{16})\\.([A-Za-z0-9_-]{32,128})$")

        fun parse(raw: String): DeviceCredential? {
            val match = PATTERN.matchEntire(raw.trim()) ?: return null
            return DeviceCredential(match.groupValues[1], match.groupValues[2])
        }
    }
}
