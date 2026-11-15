package com.deepsplat.capture.core

import kotlin.math.abs

object Pose {
    /** OpenGL column-major float[16] (ARCore Pose.toMatrix) to row-major. */
    fun rowMajor(columnMajor: FloatArray): List<Float> {
        require(columnMajor.size == 16)
        return List(16) { i -> columnMajor[(i % 4) * 4 + i / 4] }
    }

    fun requireValid(rowMajor: List<Float>) {
        if (rowMajor.size != 16 || rowMajor.any { !it.isFinite() }) throw CaptureException("A pose must contain 16 finite values.")
        val bottom = rowMajor.subList(12, 16)
        if (abs(bottom[0]) > 1e-4 || abs(bottom[1]) > 1e-4 || abs(bottom[2]) > 1e-4 || abs(bottom[3] - 1f) > 1e-4) {
            throw CaptureException("A pose must be a rigid transform.")
        }
    }

    /** Camera turned 180 degrees about its viewing axis: camera X and Y flip (C · diag(-1, -1, 1, 1)). */
    fun rotated180(rowMajor: List<Float>): List<Float> =
        rowMajor.mapIndexed { i, v -> if (i % 4 == 0 || i % 4 == 1) -v else v }
}

/** Intrinsics of the image turned by 180 degrees (continuous pixel coordinates, u' = W - u). */
fun Intrinsics.rotated180(): Intrinsics = copy(cx = width - cx, cy = height - cy)

/**
 * The iPhone app stores images that become upright portrait after a 90 degree clockwise turn: the back
 * camera sensor is mounted like Android's SENSOR_ORIENTATION 90. Phones with SENSOR_ORIENTATION 270
 * deliver the image upside down relative to that, so we turn image, intrinsics and pose by 180 degrees.
 */
fun needsHalfTurn(sensorOrientation: Int): Boolean = ((sensorOrientation % 360) + 360) % 360 == 270
