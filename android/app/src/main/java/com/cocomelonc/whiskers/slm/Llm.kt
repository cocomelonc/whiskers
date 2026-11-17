package com.cocomelonc.whiskers.slm

import android.util.Log
import java.io.ByteArrayOutputStream

/** Independent Whiskers llama.cpp binding; calls stay on one worker thread. */
object Llm {
    private var handle = 0L
    val loaded get() = handle != 0L

    // the native side calls back with the raw utf-8 bytes of each token
    fun interface Piece { fun onPiece(bytes: ByteArray): Boolean }

    private external fun nativeLoad(path: String, threads: Int, nctx: Int): Long
    private external fun nativeCount(h: Long, text: String): Int
    private external fun nativeGenerate(h: Long, prompt: String, maxTokens: Int, cb: Piece, grammar: String): ByteArray?
    private external fun nativeFree(h: Long)

    /** 4 threads: fastest on the test phone (big cores only; more threads fight the little ones) */
    fun load(path: String, threads: Int = 4, ctx: Int = CTX): Boolean {
        if (handle != 0L) return true
        System.loadLibrary("whiskers_llm")
        val t = System.currentTimeMillis()
        handle = nativeLoad(path, threads, ctx)
        Log.i("slm", "load ${if (loaded) "ok" else "failed"} in ${System.currentTimeMillis() - t} ms")
        return loaded
    }

    fun count(text: String): Int = nativeCount(handle, text)

    /**
     * generate after [prompt] until the end-of-turn token or [maxTokens]. [onText] gets the text
     * so far after every token and may return false to stop. tokens can split a utf-8 character,
     * so bytes are collected and decoded whole each time.
     */
    fun generate(prompt: String, maxTokens: Int, grammar: String = "", onText: (String) -> Boolean = { true }): String? {
        val buf = ByteArrayOutputStream()
        val out = nativeGenerate(handle, prompt, maxTokens, { b -> buf.write(b); onText(buf.toString(Charsets.UTF_8.name())) }, grammar) ?: return null
        return String(out, Charsets.UTF_8)
    }

    fun free() {
        if (handle != 0L) nativeFree(handle)
        handle = 0L
    }

    // Bounded family evidence and a compact selection; one independent context per app.
    const val CTX = 4096
}
