package com.cocomelonc.whiskers

import org.json.JSONArray
import org.json.JSONObject

data class Passage(val id: String, val text: String, val source: String)
data class Card(val id: String, val name: String, val updated: String, val source: String,
                val passages: List<Passage>, val canonicalEvidence: String, val digest: String, val rules: JSONArray)
data class Answer(val text: String, val passages: List<Passage>, val abstained: Boolean)

object EvidenceEngine {
    const val unknown = "Not stated in the supplied Malpedia evidence."
    fun sourceOnlyReason(question: String): String? {
        val q = question.lowercase()
        if (Regex("\\b(my|our)\\s+(sample|file|device|machine|laptop|computer|network|incident|host)\\b|\\bi found\\b|\\buploaded\\b").containsMatchIn(q))
            return "A family reference record contains no observations of the analyst's file, device or incident."
        val live = Regex("\\b(current|live|active|today)\\b|right now").containsMatchIn(q)
        val infrastructure = Regex("\\b(c2|ip|address|addresses|domain|domains|campaign|operator)\\b").containsMatchIn(q)
        return if (live && infrastructure) "A dated family reference record does not establish live infrastructure or current campaign activity." else null
    }
    fun cards(json: String): List<Card> {
        val rows = JSONArray(json)
        return (0 until rows.length()).map { index ->
            val row = rows.getJSONObject(index)
            val evidence = row.getJSONArray("evidence")
            val passages = (0 until evidence.length()).map { i -> evidence.getJSONObject(i).let { Passage(it.getString("id"), it.getString("text"), it.getString("source")) } }
            Card(row.getString("id"), row.getString("name"), row.getString("updated"), row.getString("source"), passages,
                row.getString("canonical_evidence"), row.getString("evidence_sha256"), row.getJSONArray("rules"))
        }
    }

    // Match Python json.dumps(ensure_ascii=True, separators=(",", ":")) for a string.
    fun quote(text: String): String = buildString {
        append('"')
        for (c in text) when (c) {
            '"' -> append("\\\""); '\\' -> append("\\\\")
            '\n' -> append("\\n"); '\r' -> append("\\r"); '\t' -> append("\\t")
            '\b' -> append("\\b"); '\u000c' -> append("\\f")
            else -> if (c.code < 32 || c.code > 126) append("\\u%04x".format(c.code)) else append(c)
        }
        append('"')
    }

    fun userPayload(question: String, card: Card) = "{\"evidence\":${card.canonicalEvidence},\"question\":${quote(question)}}"
    fun prompt(system: String, question: String, card: Card): String {
        val payload = userPayload(question, card).replace("<|", "\\u003c|")
        return "<|im_start|>system\n$system<|im_end|>\n<|im_start|>user\n$payload<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    }

    private fun literal(text: String) = "\"" + text.replace("\\", "\\\\").replace("\"", "\\\"") + "\""
    fun grammar(card: Card): String {
        val ids = card.passages.joinToString(" | ") { literal(quote(it.id)) }
        return "root ::= " + literal("{\"abstain\":") + " (" + literal("true,\"evidence_ids\":[]}") + " | " +
            literal("false,\"evidence_ids\":[") + " id (" + literal(",") + " id){0,3} " + literal("]}") + ")\nid ::= $ids\n"
    }

    fun answer(raw: String, card: Card): Answer {
        val value = JSONObject(raw.trim())
        check(value.keys().asSequence().toSet() == setOf("abstain", "evidence_ids")) { "Invalid SLM answer format" }
        val flag = value.get("abstain")
        check(flag is Boolean) { "Invalid abstention" }
        val array = value.getJSONArray("evidence_ids")
        check(array.length() <= 4) { "Too many passages" }
        val ids = (0 until array.length()).map { check(array.get(it) is String); array.getString(it) }
        check(ids.distinct().size == ids.size) { "Repeated source passage" }
        check((flag && ids.isEmpty()) || (!flag && ids.isNotEmpty())) { "Inconsistent evidence selection" }
        val lookup = card.passages.associateBy { it.id }
        val chosen = ids.map { lookup[it] ?: error("Unknown source passage") }
        return Answer(if (flag) unknown else chosen.joinToString("\n\n") { it.text }, chosen, flag)
    }
}
