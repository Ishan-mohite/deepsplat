package com.deepsplat.capture.data

import com.deepsplat.capture.core.isValidUploadUrl
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okio.BufferedSink
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit

class BackendException(message: String, val code: Int = 0) : IOException(message)

@Serializable
data class Health(val ok: Boolean = false, val vps: String? = null, val detector: String? = null,
                  @SerialName("detector_reachable") val detectorReachable: Boolean? = null)

@Serializable
data class MapSummary(
    val id: String,
    val name: String,
    val status: String = "ready",
    @SerialName("vps_status") val vpsStatus: String = "none",
    val property: String? = null,
    @SerialName("floor_label") val floorLabel: String? = null,
)

@Serializable
data class UploadTicket(@SerialName("upload_url") val uploadUrl: String, @SerialName("expires_at") val expiresAt: String? = null)

@Serializable
data class CaptureStatus(val status: String, val detail: String? = null, val error: String? = null)

@Serializable
private data class CreateCapture(
    @SerialName("capture_id") val captureId: String,
    @SerialName("map_id") val mapId: String,
    @SerialName("size_bytes") val sizeBytes: Long,
    @SerialName("created_unix") val createdUnix: Double,
    @SerialName("device_id") val deviceId: String? = null,
    val recorder: String? = null,
)

@Serializable
private data class ObservationOut(
    @SerialName("detected_type") val detectedType: String = "defect",
    @SerialName("length_m") val lengthM: Double? = null,
    @SerialName("area_m2") val areaM2: Double? = null,
    val change: String? = null,
)

@Serializable
private data class CaptureOut(
    val observations: List<ObservationOut> = emptyList(),
    @SerialName("overlay_url") val overlayUrl: String? = null,
)

data class CaptureResult(val findings: List<Finding>, val overlayUrl: String?)

/** The /v1 capture contract (clients/ios/BACKEND.md) plus the read-only endpoints the app shows. */
class BackendClient(baseUrl: String, private val token: String, private val http: OkHttpClient = defaultHttp) {
    private val base: HttpUrl = baseUrl.toHttpUrlOrNull() ?: throw BackendException("Invalid backend address.")
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false }
    private val jsonType = "application/json".toMediaType()

    private fun url(vararg path: String): HttpUrl = base.newBuilder().apply { path.forEach { addPathSegment(it) } }.build()

    private fun <T> call(request: Request, parse: (String) -> T): T {
        http.newCall(request).execute().use { r ->
            val body = r.body?.string().orEmpty()
            if (!r.isSuccessful) {
                throw when (r.code) {
                    401, 403 -> BackendException("The backend rejected the token. Check it in Settings.", r.code)
                    404 -> BackendException("Not found on the backend (HTTP 404).", 404)
                    409 -> BackendException(detail(body) ?: "The backend is busy with this capture (HTTP 409).", 409)
                    else -> BackendException("Backend request failed (HTTP ${r.code}).", r.code)
                }
            }
            return parse(body)
        }
    }

    private fun detail(body: String): String? = runCatching {
        json.parseToJsonElement(body).let { (it as? kotlinx.serialization.json.JsonObject)?.get("detail")?.toString()?.trim('"') }
    }.getOrNull()

    private fun authed(url: HttpUrl) = Request.Builder().url(url)
        .header("Authorization", "Bearer $token").header("Accept", "application/json")

    fun health(): Health = call(Request.Builder().url(url("health")).build()) { json.decodeFromString(Health.serializer(), it) }

    /** True when the token is accepted. A made-up capture id returns 404 after auth and 401 before. */
    fun tokenAccepted(): Boolean = try {
        call(authed(url("v1", "captures", "token-check")).get().build()) { }
        true
    } catch (e: BackendException) {
        when (e.code) { 404 -> true; 401, 403 -> false; else -> throw e }
    }

    fun maps(): List<MapSummary> =
        call(Request.Builder().url(url("maps")).build()) { json.decodeFromString(ListSerializer(MapSummary.serializer()), it) }

    fun createUpload(id: String, mapId: String, size: Long, createdUnix: Double, deviceId: String?, recorder: String?): UploadTicket {
        val body = json.encodeToString(CreateCapture.serializer(), CreateCapture(id, mapId, size, createdUnix, deviceId, recorder?.ifBlank { null }))
        val ticket = call(authed(url("v1", "captures")).post(body.toRequestBody(jsonType)).build()) {
            json.decodeFromString(UploadTicket.serializer(), it)
        }
        val fixed = reachableUploadUrl(ticket.uploadUrl)
        if (!isValidUploadUrl(fixed)) throw BackendException("The backend returned an invalid upload address.")
        return ticket.copy(uploadUrl = fixed)
    }

    /**
     * A backend started without PUBLIC_BASE_URL signs local uploads for localhost, which the phone cannot
     * reach. Loopback upload hosts are pointed at the backend address the app already uses.
     */
    internal fun reachableUploadUrl(url: String): String {
        val u = url.toHttpUrlOrNull() ?: return url
        val loopback = u.host == "localhost" || u.host.startsWith("127.")
        if (!loopback || base.host == u.host) return url
        return u.newBuilder().scheme(base.scheme).host(base.host).port(base.port).build().toString()
    }

    /** PUT the zip to the signed URL. The bearer token is never sent to storage. */
    fun upload(url: String, zip: File, onProgress: (Float) -> Unit) {
        val request = Request.Builder().url(url).put(ProgressBody(zip, "application/zip".toMediaType(), onProgress)).build()
        uploadHttp.newCall(request).execute().use { r ->
            if (!r.isSuccessful) throw BackendException("Storage upload failed (HTTP ${r.code}). A fresh upload address will be requested.", r.code)
        }
    }

    fun complete(id: String): CaptureStatus =
        call(authed(url("v1", "captures", id, "complete")).post(ByteArray(0).toRequestBody(null)).build()) {
            runCatching { json.decodeFromString(CaptureStatus.serializer(), it) }.getOrDefault(CaptureStatus("processing"))
        }

    fun status(id: String): CaptureStatus =
        call(authed(url("v1", "captures", id)).get().build()) { json.decodeFromString(CaptureStatus.serializer(), it) }

    fun result(id: String): CaptureResult = call(authed(url("captures", id)).get().build()) {
        val c = json.decodeFromString(CaptureOut.serializer(), it)
        CaptureResult(c.observations.map { o -> Finding(o.detectedType, o.lengthM, o.areaM2, o.change) }, c.overlayUrl)
    }

    fun bytes(url: String): ByteArray {
        val target = base.resolve(url) ?: throw BackendException("Bad URL")
        http.newCall(Request.Builder().url(target).build()).execute().use { r ->
            if (!r.isSuccessful) throw BackendException("Download failed (HTTP ${r.code}).", r.code)
            return r.body?.bytes() ?: ByteArray(0)
        }
    }

    companion object {
        val defaultHttp: OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS).writeTimeout(30, TimeUnit.SECONDS)
            .followRedirects(false).followSslRedirects(false)
            .build()
        private val uploadHttp: OkHttpClient = defaultHttp.newBuilder()
            .writeTimeout(2, TimeUnit.MINUTES).readTimeout(2, TimeUnit.MINUTES)
            .retryOnConnectionFailure(false)
            .build()
    }
}

private class ProgressBody(private val file: File, private val type: MediaType, private val onProgress: (Float) -> Unit) : RequestBody() {
    override fun contentType() = type
    override fun contentLength() = file.length()
    override fun writeTo(sink: BufferedSink) {
        val total = file.length().coerceAtLeast(1)
        var sent = 0L
        var lastReported = -1
        val buf = ByteArray(1 shl 16)
        file.inputStream().use { inp ->
            while (true) {
                val n = inp.read(buf)
                if (n < 0) break
                sink.write(buf, 0, n)
                sent += n
                val pct = (sent * 100 / total).toInt()
                if (pct != lastReported) { lastReported = pct; onProgress(sent.toFloat() / total) }
            }
        }
    }
}
