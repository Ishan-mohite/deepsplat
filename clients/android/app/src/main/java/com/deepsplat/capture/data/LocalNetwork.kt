package com.deepsplat.capture.data

import android.annotation.SuppressLint
import android.content.Context
import android.net.ConnectivityManager
import android.net.LinkAddress
import android.net.Network
import android.net.NetworkCapabilities
import com.deepsplat.capture.core.isPrivateNetworkHost
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import java.net.Inet4Address
import java.net.InetAddress

/**
 * Offline operation: on a Wi-Fi without internet, Android keeps mobile data as the default network and sends
 * traffic for 192.168.x.x there, where it never arrives. Requests to a backend on the local network are bound
 * to the Wi-Fi (or Ethernet) network whose subnet contains the backend.
 */
@Suppress("DEPRECATION") // allNetworks: still the simple way to list networks, including ones without internet
object LocalNetwork {
    /** The application context (set once in DeepsplatApp), not an Activity. */
    @SuppressLint("StaticFieldLeak")
    @Volatile var context: Context? = null

    fun forUrl(url: String): Network? {
        val ctx = context ?: return null
        val host = url.toHttpUrlOrNull()?.host ?: return null
        if (!isPrivateNetworkHost(host) || host == "localhost" || host.startsWith("127.")) return null
        val cm = ctx.getSystemService(ConnectivityManager::class.java) ?: return null
        val local = cm.allNetworks.filter { n ->
            cm.getNetworkCapabilities(n)?.let {
                it.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) || it.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)
            } == true
        }
        if (host.endsWith(".local")) return local.firstOrNull()
        val target = runCatching { InetAddress.getByName(host) }.getOrNull() as? Inet4Address ?: return null // IP literal, no lookup
        return local.firstOrNull { n -> cm.getLinkProperties(n)?.linkAddresses.orEmpty().any { it.contains(target) } }
    }

    /** True when some Wi-Fi or Ethernet network is up (for "Wi-Fi only"), with or without internet. */
    fun hasWifi(): Boolean {
        val cm = context?.getSystemService(ConnectivityManager::class.java) ?: return true
        return cm.allNetworks.any { n ->
            cm.getNetworkCapabilities(n)?.let {
                it.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) || it.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)
            } == true
        }
    }

    private fun LinkAddress.contains(ip: Inet4Address): Boolean {
        val a = address as? Inet4Address ?: return false
        val bits = prefixLength.coerceIn(0, 32)
        val mask = if (bits == 0) 0 else (-1 shl (32 - bits))
        return (a.toInt() and mask) == (ip.toInt() and mask)
    }

    private fun Inet4Address.toInt(): Int = address.fold(0) { acc, b -> (acc shl 8) or (b.toInt() and 0xFF) }
}
