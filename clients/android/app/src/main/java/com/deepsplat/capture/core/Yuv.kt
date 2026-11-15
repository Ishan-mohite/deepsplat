package com.deepsplat.capture.core

import java.nio.ByteBuffer

/**
 * YUV_420_888 planes (any row and pixel stride) to NV21 (Y plane, then interleaved V/U), which
 * android.graphics.YuvImage turns into a JPEG without EXIF orientation.
 */
fun yuv420ToNv21(
    width: Int, height: Int,
    y: ByteBuffer, yRowStride: Int, yPixelStride: Int,
    u: ByteBuffer, v: ByteBuffer, uvRowStride: Int, uvPixelStride: Int,
    out: ByteArray,
) {
    require(out.size >= width * height * 3 / 2)
    var o = 0
    val yb = y.duplicate()
    if (yPixelStride == 1 && yRowStride == width) {
        yb.position(0); yb.get(out, 0, width * height); o = width * height
    } else {
        val row = ByteArray(yRowStride)
        for (r in 0 until height) {
            yb.position(r * yRowStride)
            val len = minOf(yRowStride, yb.remaining())
            yb.get(row, 0, len)
            if (yPixelStride == 1) {
                System.arraycopy(row, 0, out, o, width); o += width
            } else for (c in 0 until width) out[o++] = row[c * yPixelStride]
        }
    }
    val ub = u.duplicate(); val vb = v.duplicate()
    for (r in 0 until height / 2) {
        val base = r * uvRowStride
        for (c in 0 until width / 2) {
            val i = base + c * uvPixelStride
            out[o++] = vb.get(i)
            out[o++] = ub.get(i)
        }
    }
}

/** NV21 turned by 180 degrees in place. */
fun rotateNv21By180(nv21: ByteArray, width: Int, height: Int) {
    val ySize = width * height
    var i = 0; var j = ySize - 1
    while (i < j) { val t = nv21[i]; nv21[i] = nv21[j]; nv21[j] = t; i++; j-- }
    val pairs = ySize / 4
    i = 0; j = pairs - 1
    while (i < j) {
        val a = ySize + 2 * i; val b = ySize + 2 * j
        val t0 = nv21[a]; val t1 = nv21[a + 1]
        nv21[a] = nv21[b]; nv21[a + 1] = nv21[b + 1]
        nv21[b] = t0; nv21[b + 1] = t1
        i++; j--
    }
}

/**
 * Sharpness of the image centre: variance of a 4-neighbour Laplacian on every 4th luma pixel.
 * Used to keep the sharpest of a short burst for the defect photo.
 */
fun lumaSharpness(nv21: ByteArray, width: Int, height: Int, step: Int = 4): Double {
    val x0 = width / 4; val x1 = width * 3 / 4; val y0 = height / 4; val y1 = height * 3 / 4
    var sum = 0.0; var sum2 = 0.0; var n = 0
    var yy = y0 + step
    while (yy < y1 - step) {
        var xx = x0 + step
        val row = yy * width
        while (xx < x1 - step) {
            val c = nv21[row + xx].toInt() and 0xFF
            val l = (nv21[row + xx - step].toInt() and 0xFF) + (nv21[row + xx + step].toInt() and 0xFF) +
                (nv21[row - step * width + xx].toInt() and 0xFF) + (nv21[row + step * width + xx].toInt() and 0xFF) - 4 * c
            sum += l; sum2 += l.toDouble() * l; n++
            xx += step
        }
        yy += step
    }
    if (n == 0) return 0.0
    val mean = sum / n
    return sum2 / n - mean * mean
}
