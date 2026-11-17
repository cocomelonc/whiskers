"""Local llama.cpp chat endpoint; deliberately separate from the Ollama worker."""
import json
import os
import time
import urllib.request

from slm.protocol import messages, render_selection, selection_schema


class WhiskersSLM:
    def __init__(self):
        self.url = os.getenv("WHISKERS_SLM_URL", "http://127.0.0.1:8082").rstrip("/")
        self.model = os.getenv("WHISKERS_SLM_MODEL", "whiskers-ti-0.6b-v0.1")
        self.enabled = os.getenv("WHISKERS_SLM_ENABLED", "0") == "1"

    def status(self):
        result = {"enabled": self.enabled, "model": self.model, "ready": False,
                  "mode": "model-selected source excerpts"}
        if self.enabled:
            try:
                with urllib.request.urlopen(self.url + "/v1/models", timeout=3) as response:
                    payload = json.loads(response.read())
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    raise ValueError("Invalid models response")
                models = payload["data"]
                result["ready"] = any(isinstance(m, dict) and m.get("id") == self.model for m in models)
                if not result["ready"]:
                    result["reason"] = "Configured model alias is not loaded"
            except (OSError, ValueError):
                result["reason"] = "Local SLM server is unavailable"
        return result

    def answer(self, question, evidence):
        if not self.enabled:
            raise RuntimeError("Whiskers SLM is disabled; enable it after loading its own checkpoint")
        body = {"model": self.model, "messages": messages(question, evidence),
                "stream": False, "temperature": 0, "max_tokens": 128,
                "response_format": {"type": "json_schema", "schema": selection_schema(evidence)},
                "chat_template_kwargs": {"enable_thinking": False}}
        request = urllib.request.Request(self.url + "/v1/chat/completions",
                                         data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
        start = time.monotonic()
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read())
        if not isinstance(payload, dict):
            raise ValueError("Invalid SLM response")
        # An absent/mismatched alias must not be presented as our trained checkpoint.
        if payload.get("model") != self.model:
            raise ValueError("SLM server returned a different model alias")
        choice = payload["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ValueError("SLM response was incomplete")
        result = render_selection(choice["message"]["content"], evidence)
        return {**result, "supplied_evidence": evidence, "model": self.model, "elapsed_ms": round(1000 * (time.monotonic() - start)),
                "usage": payload.get("usage", {})}
