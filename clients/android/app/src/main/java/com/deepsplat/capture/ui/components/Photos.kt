package com.deepsplat.capture.ui.components

import android.graphics.Bitmap
import android.util.LruCache
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.PhotoCamera
import androidx.compose.material3.Icon
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.dp
import com.deepsplat.capture.capture.CaptureEngine
import com.deepsplat.capture.ui.theme.Ds
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

object PhotoCache {
    private val cache = object : LruCache<String, Bitmap>(24 * 1024 * 1024) {
        override fun sizeOf(key: String, value: Bitmap) = value.byteCount
    }

    suspend fun load(file: File, maxSide: Int): Bitmap? {
        val key = "${file.path}@$maxSide@${file.lastModified()}"
        cache.get(key)?.let { return it }
        return withContext(Dispatchers.IO) {
            if (!file.isFile) null else CaptureEngine.previewOf(file.path, maxSide)?.also { cache.put(key, it) }
        }
    }
}

/** The defect photo, upright, from the raw sensor-orientation JPEG. */
@Composable
fun CapturePhoto(file: File, modifier: Modifier = Modifier, maxSide: Int = 240, contentScale: ContentScale = ContentScale.Crop) {
    var bmp by remember(file) { mutableStateOf<Bitmap?>(null) }
    LaunchedEffect(file, maxSide) { bmp = PhotoCache.load(file, maxSide) }
    Box(modifier.background(Ds.vp), contentAlignment = Alignment.Center) {
        val b = bmp
        if (b != null) Image(b.asImageBitmap(), "Defect photo", Modifier.fillMaxSize(), contentScale = contentScale)
        else Icon(Icons.Outlined.PhotoCamera, null, Modifier.size(22.dp), tint = Ds.vpMuted)
    }
}

@Composable
fun Thumb(file: File, modifier: Modifier = Modifier) = CapturePhoto(file, modifier.size(64.dp).clip(Ds.rSm))
