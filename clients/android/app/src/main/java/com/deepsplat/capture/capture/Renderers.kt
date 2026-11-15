package com.deepsplat.capture.capture

import android.opengl.GLES11Ext
import android.opengl.GLES20
import com.google.ar.core.Coordinates2d
import com.google.ar.core.Frame
import com.google.ar.core.PointCloud
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.FloatBuffer

private fun floats(n: Int): FloatBuffer = ByteBuffer.allocateDirect(n * 4).order(ByteOrder.nativeOrder()).asFloatBuffer()

private fun program(vertex: String, fragment: String): Int {
    fun shader(type: Int, src: String) = GLES20.glCreateShader(type).also {
        GLES20.glShaderSource(it, src); GLES20.glCompileShader(it)
        val ok = IntArray(1); GLES20.glGetShaderiv(it, GLES20.GL_COMPILE_STATUS, ok, 0)
        check(ok[0] != 0) { "Shader: " + GLES20.glGetShaderInfoLog(it) }
    }
    return GLES20.glCreateProgram().also {
        GLES20.glAttachShader(it, shader(GLES20.GL_VERTEX_SHADER, vertex))
        GLES20.glAttachShader(it, shader(GLES20.GL_FRAGMENT_SHADER, fragment))
        GLES20.glLinkProgram(it)
    }
}

/** Draws the ARCore camera texture full screen. */
class BackgroundRenderer {
    var textureId = 0
        private set
    private var prog = 0
    private var aPos = 0
    private var aTex = 0
    private val quad = floats(8).apply { put(floatArrayOf(-1f, -1f, 1f, -1f, -1f, 1f, 1f, 1f)); position(0) }
    private val tex = floats(8)

    fun create() {
        val t = IntArray(1)
        GLES20.glGenTextures(1, t, 0)
        textureId = t[0]
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, textureId)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR)
        prog = program(
            """
            attribute vec4 a_Position; attribute vec2 a_TexCoord; varying vec2 v_TexCoord;
            void main() { gl_Position = a_Position; v_TexCoord = a_TexCoord; }
            """.trimIndent(),
            """
            #extension GL_OES_EGL_image_external : require
            precision mediump float; varying vec2 v_TexCoord; uniform samplerExternalOES u_Texture;
            void main() { gl_FragColor = texture2D(u_Texture, v_TexCoord); }
            """.trimIndent(),
        )
        aPos = GLES20.glGetAttribLocation(prog, "a_Position")
        aTex = GLES20.glGetAttribLocation(prog, "a_TexCoord")
    }

    fun draw(frame: Frame) {
        if (frame.hasDisplayGeometryChanged()) {
            quad.position(0); tex.position(0)
            frame.transformCoordinates2d(Coordinates2d.OPENGL_NORMALIZED_DEVICE_COORDINATES, quad, Coordinates2d.TEXTURE_NORMALIZED, tex)
            quad.position(0); tex.position(0)
        }
        if (frame.timestamp == 0L) return
        GLES20.glDisable(GLES20.GL_DEPTH_TEST)
        GLES20.glDepthMask(false)
        GLES20.glActiveTexture(GLES20.GL_TEXTURE0)
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, textureId)
        GLES20.glUseProgram(prog)
        GLES20.glVertexAttribPointer(aPos, 2, GLES20.GL_FLOAT, false, 0, quad)
        GLES20.glVertexAttribPointer(aTex, 2, GLES20.GL_FLOAT, false, 0, tex)
        GLES20.glEnableVertexAttribArray(aPos); GLES20.glEnableVertexAttribArray(aTex)
        GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP, 0, 4)
        GLES20.glDisableVertexAttribArray(aPos); GLES20.glDisableVertexAttribArray(aTex)
        GLES20.glDepthMask(true)
    }
}

/** The feature points ARCore tracks, as soft dots in the pick colour: shows people what the phone "sees". */
class PointCloudRenderer {
    private var prog = 0
    private var aPos = 0
    private var uMvp = 0
    private var uColor = 0
    private var uSize = 0
    private var lastTimestamp = 0L
    private var count = 0
    private var buffer = floats(4 * 4096)

    fun create() {
        prog = program(
            """
            uniform mat4 u_Mvp; uniform float u_Size; attribute vec4 a_Point; varying float v_Conf;
            void main() {
              gl_Position = u_Mvp * vec4(a_Point.xyz, 1.0);
              v_Conf = a_Point.w;
              gl_PointSize = u_Size * clamp(2.0 / gl_Position.w, 0.6, 1.6);
            }
            """.trimIndent(),
            """
            precision mediump float; uniform vec4 u_Color; varying float v_Conf;
            void main() {
              vec2 d = gl_PointCoord - vec2(0.5);
              float r = dot(d, d);
              if (r > 0.25) discard;
              float a = smoothstep(0.25, 0.08, r) * mix(0.35, 0.95, clamp(v_Conf, 0.0, 1.0));
              gl_FragColor = vec4(u_Color.rgb, u_Color.a * a);
            }
            """.trimIndent(),
        )
        aPos = GLES20.glGetAttribLocation(prog, "a_Point")
        uMvp = GLES20.glGetUniformLocation(prog, "u_Mvp")
        uColor = GLES20.glGetUniformLocation(prog, "u_Color")
        uSize = GLES20.glGetUniformLocation(prog, "u_Size")
    }

    /** Returns the number of points drawn. */
    fun draw(cloud: PointCloud, mvp: FloatArray, density: Float): Int {
        if (cloud.timestamp != lastTimestamp) {
            lastTimestamp = cloud.timestamp
            val src = cloud.points
            count = src.remaining() / 4
            if (buffer.capacity() < src.remaining()) buffer = floats(src.remaining() * 2)
            buffer.clear(); buffer.put(src); buffer.flip()
        }
        if (count == 0) return 0
        GLES20.glEnable(GLES20.GL_BLEND)
        GLES20.glBlendFunc(GLES20.GL_SRC_ALPHA, GLES20.GL_ONE_MINUS_SRC_ALPHA)
        GLES20.glUseProgram(prog)
        GLES20.glUniformMatrix4fv(uMvp, 1, false, mvp, 0)
        // --pick #7EA2FF
        GLES20.glUniform4f(uColor, 0x7E / 255f, 0xA2 / 255f, 1f, 1f)
        GLES20.glUniform1f(uSize, 5.5f * density)
        buffer.position(0)
        GLES20.glVertexAttribPointer(aPos, 4, GLES20.GL_FLOAT, false, 16, buffer)
        GLES20.glEnableVertexAttribArray(aPos)
        GLES20.glDrawArrays(GLES20.GL_POINTS, 0, count)
        GLES20.glDisableVertexAttribArray(aPos)
        GLES20.glDisable(GLES20.GL_BLEND)
        return count
    }
}
