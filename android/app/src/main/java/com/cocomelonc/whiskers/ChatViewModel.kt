package com.cocomelonc.whiskers

import android.app.Application
import android.net.Uri
import android.os.Handler
import android.os.Looper
import androidx.compose.runtime.*
import androidx.lifecycle.AndroidViewModel
import com.cocomelonc.whiskers.slm.Llm
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

data class ChatMessage(val question: String, val answer: Answer, val seconds: Float, val export: String, val origin: String = "slm_selection")

class ChatViewModel(app: Application) : AndroidViewModel(app) {
    private val worker = Executors.newSingleThreadExecutor()
    private val ui = Handler(Looper.getMainLooper())
    private val stop = AtomicBoolean(false)
    private val modelFile = File(app.filesDir, "whiskers-ti.gguf")
    private val system = app.assets.open("system.txt").bufferedReader().use { it.readText() }
    private val info = runCatching { JSONObject(app.assets.open("model-info.json").bufferedReader().use { it.readText() }) }.getOrDefault(JSONObject())
    val cards = EvidenceEngine.cards(app.assets.open("kit.json").bufferedReader().use { it.readText() }).sortedBy { if (it.id == "win.emotet") "0" else it.name }
    var selected by mutableIntStateOf(0); private set
    val card get() = cards[selected]
    val messages = mutableStateListOf<ChatMessage>()
    var ready by mutableStateOf(false); private set
    var busy by mutableStateOf(false); private set
    var status by mutableStateOf("Preparing local SLM…"); private set
    var error by mutableStateOf(""); private set
    var pending by mutableStateOf(""); private set
    val modelHash get() = info.optString("sha256", "not bundled")

    init { worker.execute { loadModel() } }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().buffered().use { input ->
            val block = ByteArray(1024 * 1024)
            while (true) { val n = input.read(block); if (n < 0) break; digest.update(block, 0, n) }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    private fun loadModel() {
        try {
            val expected = info.optString("sha256")
            check(expected.length == 64) { "Import the Whiskers GGUF after training finishes" }
            if (!modelFile.exists() || sha256(modelFile) != expected) {
                val part = File(modelFile.parentFile, "whiskers-model.part")
                try {
                    ui.post { status = "Installing bundled model…" }
                    getApplication<Application>().assets.open("whiskers-ti-0.6b-Q4_K_M.gguf").use { input -> part.outputStream().use { input.copyTo(it) } }
                    check(sha256(part) == expected) { "Bundled model checksum mismatch" }
                    check(part.renameTo(modelFile)) { "Model installation failed" }
                } finally { part.delete() }
            }
            ui.post { status = "Loading Whiskers SLM…" }
            check(Llm.load(modelFile.absolutePath, threads = 4, ctx = 4096)) { "Could not load the Whiskers GGUF" }
            ui.post { ready = true; status = "On device · Whiskers 0.6B"; error = "" }
        } catch (e: Throwable) {
            ui.post { ready = false; status = "Model not loaded"; error = e.message.orEmpty().take(220) }
        }
    }

    fun select(index: Int) {
        if (!busy) { selected = index; messages.clear(); error = "" }
    }

    fun clear() { if (!busy) messages.clear() }
    fun cancel() { stop.set(true) }

    fun ask(text: String) {
        val question = text.trim().take(600)
        if (!ready || busy || question.isEmpty()) return
        busy = true; pending = question; error = ""; stop.set(false)
        val source = card
        worker.execute {
            val started = System.nanoTime()
            try {
                val reason = EvidenceEngine.sourceOnlyReason(question)
                if (reason != null) {
                    val answer = Answer(EvidenceEngine.unknown, emptyList(), true)
                    val export = JSONObject().put("project", "whiskers-mini-ti").put("family", source.id).put("question", question)
                        .put("answer", answer.text).put("answer_origin", "source_policy").put("policy_reason", reason)
                        .put("supplied_evidence", JSONArray(source.canonicalEvidence)).put("evidence_sha256", source.digest).toString(2)
                    ui.post { messages.add(ChatMessage(question, answer, 0f, export, "source_policy")); busy = false; pending = "" }
                    return@execute
                }
                val prompt = EvidenceEngine.prompt(system, question, source)
                check(Llm.count(prompt) + 160 < 4096) { "This source is too long for the model context" }
                val raw = Llm.generate(prompt, 128, EvidenceEngine.grammar(source)) { !stop.get() } ?: error("Generation failed")
                check(!stop.get()) { "Stopped" }
                val answer = EvidenceEngine.answer(raw, source)
                val elapsed = (System.nanoTime() - started) / 1_000_000_000f
                val export = JSONObject().put("project", "whiskers-mini-ti").put("protocol", "whiskers-evidence-v1")
                    .put("family", source.id).put("question", question).put("answer", answer.text).put("selection", JSONObject(raw))
                    .put("answer_origin", "slm_selection")
                    .put("supplied_evidence", JSONArray(source.canonicalEvidence)).put("evidence_sha256", source.digest)
                    .put("model_sha256", modelHash).put("seconds", elapsed).toString(2)
                ui.post { messages.add(ChatMessage(question, answer, elapsed, export)); busy = false; pending = "" }
            } catch (e: Throwable) {
                ui.post { error = e.message.orEmpty().take(220); busy = false; pending = "" }
            }
        }
    }

    fun importModel(uri: Uri) {
        if (busy) return
        busy = true
        worker.execute {
            val part = File(modelFile.parentFile, "whiskers-model.part")
            try {
                val expected = info.optString("sha256")
                check(expected.length == 64) { "This APK has no released Whiskers model manifest yet" }
                getApplication<Application>().contentResolver.openInputStream(uri)!!.use { input ->
                    part.outputStream().use { output ->
                        val block = ByteArray(1024 * 1024); var total = 0L
                        while (true) { val n = input.read(block); if (n < 0) break; total += n; check(total <= 1024L * 1024 * 1024); output.write(block, 0, n) }
                    }
                }
                check(sha256(part) == expected) { "Choose the Whiskers GGUF matching this APK's model manifest" }
                Llm.free(); check(part.renameTo(modelFile)); loadModel()
            } catch (e: Throwable) { ui.post { error = e.message.orEmpty().take(220) } }
            finally { part.delete(); ui.post { busy = false } }
        }
    }

    override fun onCleared() {
        stop.set(true); worker.execute { Llm.free() }; worker.shutdown(); super.onCleared()
    }
}
