package com.deepsplat.capture.core

import java.net.URI

/** Plain http is accepted only for private-network hosts (same Wi-Fi testing); everything else must be https. */
fun isPrivateNetworkHost(host: String?): Boolean {
    val h = host?.lowercase()?.trim('[', ']') ?: return false
    if (h == "localhost" || h.endsWith(".local")) return true
    val p = h.split(".").map { it.toIntOrNull() ?: return false }
    if (p.size != 4 || p.any { it !in 0..255 }) return false
    return p[0] == 10 || (p[0] == 172 && p[1] in 16..31) || (p[0] == 192 && p[1] == 168) ||
        (p[0] == 169 && p[1] == 254) || p[0] == 127
}

fun isAllowedBackendUri(uri: URI): Boolean =
    uri.scheme == "https" || (uri.scheme == "http" && isPrivateNetworkHost(uri.host))

/**
 * Cleans what a person types into the backend field. A missing scheme becomes http:// for private hosts
 * ("192.168.1.20:8000") and https:// otherwise. Returns null for anything the app will not talk to.
 */
fun normalizeBackendUrl(text: String): String? {
    var t = text.trim().trimEnd('/')
    if (t.isEmpty()) return null
    if (!t.contains("://")) {
        val host = t.substringBefore('/').substringBefore(':')
        t = (if (isPrivateNetworkHost(host)) "http://" else "https://") + t
    }
    val uri = runCatching { URI(t) }.getOrNull() ?: return null
    if (uri.host.isNullOrEmpty() || uri.userInfo != null || uri.query != null || uri.fragment != null) return null
    if (!isAllowedBackendUri(uri)) return null
    return t
}

/** Signed upload URLs must not downgrade to http on the open internet or carry credentials. */
fun isValidUploadUrl(url: String): Boolean {
    val uri = runCatching { URI(url) }.getOrNull() ?: return false
    return !uri.host.isNullOrEmpty() && uri.userInfo == null && isAllowedBackendUri(uri)
}

/** The iOS retry curve: 2 s doubling up to 5 minutes. */
fun retryDelaySeconds(attempt: Int): Long = minOf(300L, 2L shl (attempt - 1).coerceIn(0, 8))
