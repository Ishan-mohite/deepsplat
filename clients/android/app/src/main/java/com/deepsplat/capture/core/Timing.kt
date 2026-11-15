package com.deepsplat.capture.core

import kotlin.math.abs

/** Keeps frames at the target rate from a faster camera stream (port of the iOS FrameSampler). */
class FrameSampler(fps: Double = 5.0, sourceFps: Double = 30.0) {
    val fps = fps.coerceIn(1.0, 30.0)
    val sourceFps = maxOf(this.fps, sourceFps)
    var lastKept: Double? = null
        private set

    fun shouldKeep(timestamp: Double): Boolean {
        if (!timestamp.isFinite()) return false
        val last = lastKept
        if (last != null && timestamp < last + 1 / fps - 0.5 / sourceFps) return false
        lastKept = timestamp
        return true
    }

    fun reset() {
        lastKept = null
    }
}

/**
 * Camera and sensor timestamps on Android are usually CLOCK_BOOTTIME (elapsedRealtimeNanos), but some
 * devices deliver CLOCK_MONOTONIC (System.nanoTime) instead. Everything in a capture is put on BOOTTIME.
 * Returns the shift in ns to add to [timestampNs].
 */
fun clockShiftToBoottime(timestampNs: Long, boottimeNs: Long, monotonicNs: Long): Long = when {
    abs(boottimeNs - timestampNs) < 2_000_000_000L -> 0L
    abs(monotonicNs - timestampNs) < 2_000_000_000L -> boottimeNs - monotonicNs
    else -> boottimeNs - timestampNs // unknown clock: align on arrival, the error is the delivery latency
}

/** Resolves the shift once per stream, from the first timestamp seen. */
class ClockMapper(private val now: () -> Pair<Long, Long>) {
    @Volatile private var shift: Long? = null
    val resolvedShiftNs: Long? get() = shift

    fun seconds(timestampNs: Long): Double {
        val s = shift ?: now().let { (boot, mono) -> clockShiftToBoottime(timestampNs, boot, mono) }.also { shift = it }
        return (timestampNs + s) / 1e9
    }
}
