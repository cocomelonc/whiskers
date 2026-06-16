#!/usr/bin/env python3
# copyright (c) 2026 cocomelonc
# author: cocomelonc
"""whiskers - local-LLM enrichment worker.

Tags every Malpedia family with capabilities + target sectors using a small
local model via Ollama (default qwen3:1.7b on CPU). Writes to the `tags` table
in whiskers.db. resumable: re-running continues where it left off.

setup:
    ollama serve                  # in another terminal
    ollama pull qwen3:1.7b
    python worker.py              # tag the delta (any untagged / freshly-added families)
    python worker.py --limit 20   # try a small batch first
    python worker.py --status     # show coverage (corpus / tagged / delta), no tagging
    python worker.py --full       # re-tag the whole corpus with the current model
    python worker.py --embed      # build semantic-search vectors (GPU feature)
    python worker.py --watch 3600 # stay running; tag new families every hour
"""

import argparse
import array
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("WHISKERS_DB", "whiskers.db")
OLLAMA  = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
MODEL   = os.getenv("OLLAMA_MODEL", "qwen3:1.7b")
EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

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
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read())["message"]["content"]
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = (json.loads(e.read()).get("error") or "")[:300]
        except Exception:
            pass
        raise RuntimeError(f"ollama HTTP {e.code}: {detail or e.reason}") from None


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


def _pending(limit: int, full: bool = False) -> list[sqlite3.Row]:
    """Families still to process.

    Normal: those with no tag at all (the delta).
    Full:   those not yet tagged by the CURRENT model - i.e. untagged OR tagged by
            another model. Re-running --full resumes (re-tagged rows are skipped)."""
    with closing(_db()) as c:
        if full:
            return c.execute(
                "SELECT name, data FROM families WHERE name NOT IN "
                "(SELECT name FROM tags WHERE model = ?) LIMIT ?",
                (MODEL, limit),
            ).fetchall()
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


# -- embeddings (GPU feature: --embed) -----------------------------

def _embed_vec(text: str) -> bytes:
    """Embed text via Ollama; return raw float32 bytes (normalized at load time)."""
    body = json.dumps({"model": EMBED_MODEL, "prompt": text[:2000]}).encode()
    req = urllib.request.Request(
        f"{OLLAMA}/api/embeddings", data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            vec = json.loads(r.read())["embedding"]
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = (json.loads(e.read()).get("error") or "")[:300]
        except Exception:
            pass
        raise RuntimeError(f"ollama HTTP {e.code}: {detail or e.reason}") from None
    return array.array("f", vec).tobytes()


def _unembedded(limit: int) -> list[sqlite3.Row]:
    with closing(_db()) as c:
        c.execute("CREATE TABLE IF NOT EXISTS embeddings(name TEXT PRIMARY KEY, vec BLOB)")
        return c.execute(
            "SELECT name, data FROM families "
            "WHERE name NOT IN (SELECT name FROM embeddings) LIMIT ?",
            (limit,),
        ).fetchall()


def embed_pass(limit: int, log_every: int) -> int:
    """Generate embeddings for families that don't have one yet. Resumable."""
    rows = _unembedded(limit)
    total = len(rows)
    if not total:
        return 0
    print(f"[=^..^=] embedding {total} famil{'y' if total == 1 else 'ies'} "
          f"with {EMBED_MODEL} (resumable, Ctrl-C safe)")

    live = sys.stdout.isatty()
    done, t0, fails = 0, time.time(), 0
    for r in rows:
        name = r["name"]
        meta = json.loads(r["data"])
        text = f"{meta.get('common_name') or ''} {meta.get('description') or ''}".strip() or name
        try:
            blob = _embed_vec(text)
            fails = 0
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            if live:
                sys.stdout.write("\r" + " " * 90 + "\r")
            print(f"  ! {name}: {exc}")
            fails += 1
            if fails >= 10:
                print(f"\n[=^..^=] aborting: {fails} embed calls failed in a row - "
                      f"check `ollama run {EMBED_MODEL}` / `ollama pull {EMBED_MODEL}`.")
                break
            continue
        with closing(_db()) as c:
            c.execute("INSERT OR REPLACE INTO embeddings(name, vec) VALUES(?,?)", (name, blob))
            c.commit()
        done += 1
        if done % log_every == 0:
            rate = done / (time.time() - t0)
            eta = _fmt_dur((total - done) / rate if rate else 0)
            if live:
                sys.stdout.write("\r" + " " * 90 + "\r")
            print(f"  ✓ {done:>5}/{total}  {done / total * 100:4.1f}%  {rate:.2f}/s  eta {eta:>6}  | {name}")
        if live:
            sys.stdout.write(_progress_line(done, total, t0, name))
            sys.stdout.flush()

    if live:
        sys.stdout.write("\n")
    print(f"[=^..^=] done: embedded {done} in {_fmt_dur(time.time() - t0)}")
    return done


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


def _counts() -> tuple[int, int, int]:
    """(corpus total, tagged, untagged delta)."""
    with closing(_db()) as c:
        total = c.execute("SELECT COUNT(*) FROM families").fetchone()[0]
        tagged = c.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        delta = c.execute(
            "SELECT COUNT(*) FROM families WHERE name NOT IN (SELECT name FROM tags)"
        ).fetchone()[0]
    return total, tagged, delta


def tag_pass(limit: int, log_every: int, full: bool = False) -> int:
    """Tag up to `limit` pending families. Returns how many were processed."""
    rows = _pending(limit, full)
    total = len(rows)
    if not total:
        return 0
    verb = "re-tagging" if full else "tagging"
    print(f"[=^..^=] {verb} {total} famil{'y' if total == 1 else 'ies'} "
          f"with {MODEL} (resumable, Ctrl-C safe)")

    live = sys.stdout.isatty()   # \r progress only on a real terminal, not in a logfile
    done, t0, llm_fails = 0, time.time(), 0
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
                llm_fails = 0
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                if live:
                    sys.stdout.write("\r" + " " * 90 + "\r")
                print(f"  ! {name}: {exc}")
                llm_fails += 1
                if llm_fails >= 10:
                    print(f"\n[=^..^=] aborting: {llm_fails} model calls failed in a row - "
                          f"the model/Ollama is unhealthy, not your data.\n"
                          f"[=^..^=] test it:  ollama run {MODEL} \"hi\"\n"
                          f"[=^..^=] then:     curl {OLLAMA}/api/tags   (is it up?)\n"
                          f"[=^..^=] re-pull:  ollama pull {MODEL}\n"
                          f"[=^..^=] already-done tags are saved; fix the model and re-run.")
                    break
                continue
            _save(name, caps, secs)
        done += 1

        # permanent record every --log-every (also the only output when piped to a file)
        if done % log_every == 0:
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
    print(f"[=^..^=] done: tagged {done} in {_fmt_dur(time.time() - t0)}")
    return done


def main() -> None:
    ap = argparse.ArgumentParser(description="whiskers local-LLM tagger (tags new/untagged families)")
    ap.add_argument("--limit", type=int, default=1_000_000, help="max families this run")
    ap.add_argument("--log-every", type=int, default=10, help="progress log cadence")
    ap.add_argument("--status", action="store_true", help="show tag coverage and exit")
    ap.add_argument("--full", action="store_true",
                    help="re-tag the whole corpus with the current model (overwrites; resumable)")
    ap.add_argument("--embed", action="store_true",
                    help="generate semantic-search embeddings (GPU feature; uses OLLAMA_EMBED_MODEL)")
    ap.add_argument("--watch", type=int, default=0, metavar="SECONDS",
                    help="stay running; process freshly-added families every N seconds")
    args = ap.parse_args()

    total, tagged, delta = _counts()

    # --status: just report coverage, no Ollama needed.
    if args.status:
        with closing(_db()) as c:
            embedded = c.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] \
                if c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='embeddings'").fetchone() else 0
        print(f"[=^..^=] corpus={total}  tagged={tagged}  delta={delta}  embedded={embedded}")
        with closing(_db()) as c:
            for model, n in c.execute(
                "SELECT model, COUNT(*) FROM tags GROUP BY model ORDER BY 2 DESC"
            ):
                print(f"    {n:>5}  {model}")
        return

    models = _check_ollama()
    if models is None:
        sys.exit(
            f"[=^..^=] Ollama not reachable at {OLLAMA}.\n"
            f"    Start it: `ollama serve`  then  `ollama pull {MODEL}`"
        )

    # --embed: GPU feature - build semantic-search vectors, then exit.
    if args.embed:
        if not any(m == EMBED_MODEL or m.startswith(EMBED_MODEL.split(":")[0]) for m in models):
            print(f"[=^..^=] note: {EMBED_MODEL} not pulled. Run: ollama pull {EMBED_MODEL}")
        if args.watch:
            print(f"[=^..^=] embed watch: every {args.watch}s (Ctrl-C to stop)")
            while True:
                if embed_pass(args.limit, args.log_every) == 0:
                    print(f"[=^..^=] embeddings up to date - next check in {args.watch}s")
                time.sleep(args.watch)
        if embed_pass(args.limit, args.log_every) == 0:
            print("[=^..^=] nothing to embed - all families already vectorized. =^..^=")
        return

    base = MODEL.split(":")[0]
    if not any(m == MODEL or m.startswith(base) for m in models):
        print(f"[=^..^=] note: {MODEL} not pulled yet ({models}). Run: ollama pull {MODEL}")

    if args.full:
        pending = len(_pending(10_000_000, full=True))
        print(f"[=^..^=] full re-tag with {MODEL}: {pending}/{total} pending "
              f"(families already on {MODEL} are skipped)")
    else:
        print(f"[=^..^=] corpus={total} · tagged={tagged} · delta={delta}")

    # --watch: keep tagging whatever the corpus refresh adds over time.
    if args.watch:
        print(f"[=^..^=] watch mode: checking every {args.watch}s (Ctrl-C to stop)")
        while True:
            if tag_pass(args.limit, args.log_every, args.full) == 0:
                print(f"[=^..^=] up to date - next check in {args.watch}s")
            time.sleep(args.watch)

    # one-shot.
    if tag_pass(args.limit, args.log_every, args.full) == 0:
        reason = ("all families already tagged by this model" if args.full
                  else "delta is 0, all families already enriched")
        print(f"[=^..^=] nothing to tag - {reason}. =^..^=")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[=^..^=] paused - progress saved, re-run to resume. =^..^=")
