#!/usr/bin/env python3
"""whiskers - local-LLM enrichment worker.

Tags every Malpedia family with capabilities + target sectors using a small
local model via Ollama (default qwen3:1.7b on CPU). Writes to the `tags` table
in whiskers.db. resumable: re-running continues where it left off.

Setup:
    ollama serve                  # in another terminal
    ollama pull qwen3:1.7b
    python worker.py              # tag everything still untagged
    python worker.py --limit 20   # try a small batch first
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
from contextlib import closing
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("WHISKERS_DB", "whiskers.db")
OLLAMA  = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
MODEL   = os.getenv("OLLAMA_MODEL", "qwen3:1.7b")

# closed vocabularies - the model must pick only from these.
CAPABILITIES = [
    "ransomware", "wiper", "stealer", "loader", "downloader", "dropper",
    "backdoor", "rat", "banker", "botnet", "keylogger", "rootkit", "bootkit",
    "miner", "worm", "ddos", "proxy", "spyware", "exploit", "webshell",
]
SECTORS = [
    "finance", "healthcare", "government", "military", "energy", "telecom",
    "retail", "technology", "education", "manufacturing", "media",
    "transportation", "legal", "hospitality", "critical-infrastructure",
]
CAP_SET, SEC_SET = set(CAPABILITIES), set(SECTORS)


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _ollama_chat(prompt: str) -> str:
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": "json",
        "think": False,   # disable qwen3 reasoning - else it emits huge <think> blocks (minutes on CPU)
        "options": {"temperature": 0},
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA}/api/chat", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())["message"]["content"]


def classify(name: str, desc: str) -> tuple[list[str], list[str]]:
    prompt = (
        "Classify this malware family. /no_think\n\n"
        f"Name: {name}\n"
        f"Description: {desc[:1500]}\n\n"
        'Return JSON: {"capabilities": [...], "sectors": [...]}\n'
        f"- capabilities: choose ONLY from {CAPABILITIES}\n"
        f"- sectors: targeted industries, choose ONLY from {SECTORS}, "
        "or [] if none are clearly mentioned\n"
        "Use only the allowed values. JSON only."
    )
    raw = _ollama_chat(prompt)
    try:
        obj = json.loads(raw)
    except Exception:
        return [], []
    caps = [c for c in obj.get("capabilities", []) if c in CAP_SET]
    secs = [s for s in obj.get("sectors", []) if s in SEC_SET]
    # dedupe, preserve order
    return list(dict.fromkeys(caps)), list(dict.fromkeys(secs))


def _untagged(limit: int) -> list[sqlite3.Row]:
    with closing(_db()) as c:
        return c.execute(
            "SELECT name, data FROM families "
            "WHERE name NOT IN (SELECT name FROM tags) LIMIT ?",
            (limit,),
        ).fetchall()


def _save(name: str, caps: list, secs: list) -> None:
    with closing(_db()) as c:
        c.execute(
            "INSERT OR REPLACE INTO tags(name, capabilities, sectors, model, tagged_at) "
            "VALUES(?,?,?,?,?)",
            (name, json.dumps(caps), json.dumps(secs), MODEL,
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        c.commit()


def _fmt_dur(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _progress_line(done: int, total: int, t0: float, name: str) -> str:
    elapsed = time.time() - t0
    rate = done / elapsed if elapsed else 0
    eta = (total - done) / rate if rate else 0
    frac = done / total if total else 0
    width = 22
    fill = int(frac * width)
    bar = "█" * fill + "░" * (width - fill)
    nm = (name[:26] + "…") if len(name) > 27 else name
    return (f"\r  [{bar}] {frac * 100:5.1f}%  {done}/{total}  "
            f"{rate:.2f}/s  eta {_fmt_dur(eta):>6}  {nm:<27}")


def _check_ollama() -> list[str] | None:
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=5) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="whiskers local-LLM tagger")
    ap.add_argument("--limit", type=int, default=1_000_000, help="max families this run")
    ap.add_argument("--log-every", type=int, default=10, help="progress log cadence")
    args = ap.parse_args()

    models = _check_ollama()
    if models is None:
        sys.exit(
            f"[=^..^=] Ollama not reachable at {OLLAMA}.\n"
            f"    Start it: `ollama serve`  then  `ollama pull {MODEL}`"
        )
    base = MODEL.split(":")[0]
    if not any(m == MODEL or m.startswith(base) for m in models):
        print(f"[=^..^=] note: {MODEL} not pulled yet ({models}). Run: ollama pull {MODEL}")

    rows = _untagged(args.limit)
    total = len(rows)
    if not total:
        print("[=^..^=] nothing to tag - all families already enriched. =^..^=")
        return
    print(f"[=^..^=] tagging {total} families with {MODEL} (resumable, Ctrl-C safe)")

    live = sys.stdout.isatty()   # \r progress only on a real terminal, not in a logfile
    done, t0 = 0, time.time()
    for r in rows:
        name = r["name"]
        meta = json.loads(r["data"])
        desc = (meta.get("description") or "").strip()
        if len(desc) < 25:
            _save(name, [], [])          # nothing to classify; mark processed
            caps, secs = [], []
        else:
            try:
                caps, secs = classify(name, desc)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                if live:
                    sys.stdout.write("\r" + " " * 90 + "\r")
                print(f"  ! {name}: {exc}")
                continue
            _save(name, caps, secs)
        done += 1

        # permanent record every --log-every (also the only output when piped to a file)
        if done % args.log_every == 0:
            rate = done / (time.time() - t0)
            eta = _fmt_dur((total - done) / rate if rate else 0)
            tagstr = (", ".join(caps + [f"⌖{s}" for s in secs])) or "-"
            if live:
                sys.stdout.write("\r" + " " * 90 + "\r")
            print(f"  ✓ {done:>5}/{total}  {done / total * 100:4.1f}%  {rate:.2f}/s  eta {eta:>6}  | {name}: {tagstr}")

        # live one-line bar, refreshed every family
        if live:
            sys.stdout.write(_progress_line(done, total, t0, name))
            sys.stdout.flush()

    if live:
        sys.stdout.write("\n")
    print(f"[=^..^=] done: tagged {done} families in {_fmt_dur(time.time() - t0)}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[=^..^=] paused - progress saved, re-run to resume. =^..^=")
