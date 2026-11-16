package com.deepsplat.capture.data

import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

data class AppSettings(
    val backendUrl: String = "",
    val mapId: String = "",
    val mapName: String = "",
    val recorder: String = "",
    val fps: Double = 5.0,
    val maxDuration: Double = 20.0,
    val imuRateHz: Double = 100.0,
    val jpegQuality: Double = 0.9,
    val recordGps: Boolean = false,
    val wifiOnly: Boolean = false,
    val autoUpload: Boolean = true,
    val showPoints: Boolean = true,
) {
    val capture: CaptureConfiguration
        get() = CaptureConfiguration(
            mapId = mapId.trim(),
            fps = fps.coerceIn(1.0, 15.0),
            maxDuration = maxDuration.coerceIn(5.0, 60.0),
            imuRateHz = imuRateHz.coerceIn(25.0, 200.0),
            jpegQuality = jpegQuality.coerceIn(0.5, 1.0),
            recordGps = recordGps,
        )
}

data class CaptureConfiguration(
    val mapId: String,
    val fps: Double,
    val maxDuration: Double,
    val imuRateHz: Double,
    val jpegQuality: Double,
    val recordGps: Boolean,
)

class SettingsRepository(context: Context) {
    private val prefs: SharedPreferences = context.getSharedPreferences("settings", Context.MODE_PRIVATE)
    private val tokens = TokenVault(context.getSharedPreferences("tokens", Context.MODE_PRIVATE))
    private val _state = MutableStateFlow(read())
    val state: StateFlow<AppSettings> = _state.asStateFlow()
    val value: AppSettings get() = _state.value

    private fun read() = AppSettings(
        backendUrl = prefs.getString("backendUrl", "") ?: "",
        mapId = prefs.getString("mapId", "") ?: "",
        mapName = prefs.getString("mapName", "") ?: "",
        recorder = prefs.getString("recorder", "") ?: "",
        fps = prefs.getFloat("fps", 5f).toDouble(),
        maxDuration = prefs.getFloat("maxDuration", 20f).toDouble(),
        imuRateHz = prefs.getFloat("imuRateHz", 100f).toDouble(),
        jpegQuality = prefs.getFloat("jpegQuality", 0.9f).toDouble(),
        recordGps = prefs.getBoolean("recordGps", false),
        wifiOnly = prefs.getBoolean("wifiOnly", false),
        autoUpload = prefs.getBoolean("autoUpload", true),
        showPoints = prefs.getBoolean("showPoints", true),
    )

    fun update(change: (AppSettings) -> AppSettings) {
        val s = change(_state.value)
        prefs.edit()
            .putString("backendUrl", s.backendUrl).putString("mapId", s.mapId).putString("mapName", s.mapName)
            .putString("recorder", s.recorder)
            .putFloat("fps", s.fps.toFloat()).putFloat("maxDuration", s.maxDuration.toFloat())
            .putFloat("imuRateHz", s.imuRateHz.toFloat()).putFloat("jpegQuality", s.jpegQuality.toFloat())
            .putBoolean("recordGps", s.recordGps).putBoolean("wifiOnly", s.wifiOnly)
            .putBoolean("autoUpload", s.autoUpload).putBoolean("showPoints", s.showPoints)
            .apply()
        _state.value = s
    }

    fun token(backendUrl: String = value.backendUrl): String = tokens.read(backendUrl)
    fun setToken(token: String, backendUrl: String = value.backendUrl) = tokens.write(backendUrl, token.trim())
}

/** Backend tokens per backend URL, encrypted with an AES-GCM key that never leaves the Android Keystore. */
private class TokenVault(private val prefs: SharedPreferences) {
    private val alias = "deepsplat.backend-token"

    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(alias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(
                KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .build()
            )
        }.generateKey()
    }

    fun read(account: String): String {
        val stored = prefs.getString(account, null) ?: return ""
        return runCatching {
            val bytes = Base64.decode(stored, Base64.NO_WRAP)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes, 0, 12))
            String(cipher.doFinal(bytes, 12, bytes.size - 12), Charsets.UTF_8)
        }.getOrDefault("")
    }

    fun write(account: String, token: String) {
        if (token.isEmpty()) { prefs.edit().remove(account).apply(); return }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val out = cipher.iv + cipher.doFinal(token.toByteArray(Charsets.UTF_8))
        prefs.edit().putString(account, Base64.encodeToString(out, Base64.NO_WRAP)).apply()
    }
}
