package com.cocomelonc.whiskers

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.cocomelonc.whiskers.slm.Llm
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

/** Real inference, no transport mock. Run after the bundled app has installed its model. */
@RunWith(AndroidJUnit4::class)
class OnDeviceModelTest {
    @Test fun rawHeldOutSelectionsOnRealDevice() {
        val instrument = InstrumentationRegistry.getInstrumentation()
        val target = instrument.targetContext
        val cards = EvidenceEngine.cards(target.assets.open("kit.json").bufferedReader().readText()).associateBy { it.id }
        val system = target.assets.open("system.txt").bufferedReader().readText()
        val cases = JSONArray(instrument.context.assets.open("manual_cases.json").bufferedReader().readText())
        assertEquals("The frozen raw-neural benchmark must contain 20 cases", 20, cases.length())
        val info = JSONObject(target.assets.open("model-info.json").bufferedReader().readText())
        val model = File(target.filesDir, "whiskers-ti.gguf")
        assertTrue("Launch the app first to install the verified bundled model", model.isFile)
        assertTrue(Llm.load(model.absolutePath, threads = 4, ctx = 4096))
        val results = JSONArray()
        var valid = 0
        var exact = 0
        try {
            for (i in 0 until cases.length()) {
                val case = cases.getJSONObject(i)
                val card = cards.getValue(case.getString("family"))
                val question = case.getString("question")
                val started = System.nanoTime()
                val raw = Llm.generate(EvidenceEngine.prompt(system, question, card), 128, EvidenceEngine.grammar(card))
                val row = JSONObject().put("family", card.id).put("question", question).put("raw", raw)
                    .put("seconds", (System.nanoTime() - started) / 1_000_000_000.0)
                try {
                    val answer = EvidenceEngine.answer(raw ?: error("Generation failed"), card)
                    val actual = answer.passages.map { it.id }
                    val expected = case.getJSONArray("ids").let { a -> (0 until a.length()).map { a.getString(it) } }
                    val correct = actual == expected && answer.abstained == expected.isEmpty()
                    valid++
                    if (correct) exact++
                    row.put("valid", true).put("exact", correct).put("answer", answer.text).put("ids", JSONArray(actual)).put("expected_ids", JSONArray(expected))
                } catch (error: Throwable) {
                    row.put("valid", false).put("error", error.message)
                }
                results.put(row)
                File(target.filesDir, "phone-evaluation.json").writeText(JSONObject().put("model_sha256", info.getString("sha256"))
                    .put("model", "whiskers-ti-0.6b-v0.1").put("device", android.os.Build.MODEL)
                    .put("raw_neural_evaluation", true).put("source_policy_applied", false)
                    .put("n", results.length()).put("valid", valid).put("exact", exact).put("results", results).toString(2))
            }
        } finally { Llm.free() }
        assertEquals("All raw outputs must satisfy the evidence contract", cases.length(), valid)
    }
}
