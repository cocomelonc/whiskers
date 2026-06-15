import asyncio
import functools
import json
import logging
import os
import re
import sqlite3
import urllib.error
import urllib.request
from collections import Counter
from contextlib import asynccontextmanager, closing
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from malpediaclient import Client
from pydantic import BaseModel

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("whiskers")

_raw_key = os.getenv("MALPEDIA_API_KEY", "").strip()
MALPEDIA_API_KEY = "" if _raw_key in ("", "your_malpedia_api_key_here") else _raw_key
AUTHENTICATED = bool(MALPEDIA_API_KEY)

DB_PATH = os.getenv("WHISKERS_DB", "whiskers.db")
WINDOW_DAYS = {"day": 1, "week": 7, "month": 30}
KEEP_REPORTS = 60  # history cap per stored monitor run

# Local LLM (Ollama) - same model the worker uses; CPU-friendly, on-demand only.
OLLAMA_HOST  = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:1.7b")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")


def _ollama(prompt: str, max_tokens: int = 320, temperature: float = 0.2) -> str:
    """Single-shot local LLM call. Raises urllib errors if Ollama is down."""
    body = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,   # qwen3: skip reasoning, else huge <think> on CPU
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())["message"]["content"].strip()

_client: Client | None = None


def _mp() -> Client:
    global _client
    if _client is None:
        _client = Client(apitoken=MALPEDIA_API_KEY) if AUTHENTICATED else Client()
        log.info("[=^..^=] malpedia client ready (authenticated=%s)", AUTHENTICATED)
    return _client


def _is_auth_error(exc: Exception) -> bool:
    m = str(exc)
    return "403" in m or "401" in m or "Authentication" in m


# -- database ------------------------------------------------------

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(_db()) as c:
        c.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS families(
                name TEXT PRIMARY KEY,
                common_name TEXT,
                updated TEXT,
                data TEXT
            );
            CREATE TABLE IF NOT EXISTS reports(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created TEXT, window TEXT, count INTEGER,
                summary TEXT, families TEXT
            );
            CREATE TABLE IF NOT EXISTS tags(
                name TEXT PRIMARY KEY,
                capabilities TEXT,   -- JSON array
                sectors TEXT,        -- JSON array
                model TEXT,
                tagged_at TEXT
            );
            CREATE TABLE IF NOT EXISTS briefs(
                name TEXT PRIMARY KEY,
                text TEXT, model TEXT, created TEXT
            );
            CREATE TABLE IF NOT EXISTS embeddings(
                name TEXT PRIMARY KEY,
                vec BLOB           -- float32 vector (see worker.py --embed)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(
                name, common_name, aliases, description, attribution, refs,
                tokenize='porter unicode61'
            );
            """
        )
        c.commit()


def _corpus_size() -> int:
    with closing(_db()) as c:
        return c.execute("SELECT COUNT(*) FROM families").fetchone()[0]


def refresh_corpus() -> dict[str, int]:
    """Pull every family from Malpedia and rebuild the local corpus + FTS index."""
    fams = _mp().get_families()
    if not isinstance(fams, dict):
        log.warning("[=^..^=] unexpected get_families payload")
        return {"total": 0}

    frows, srows = [], []
    for name, meta in fams.items():
        if not isinstance(meta, dict):
            continue
        common  = meta.get("common_name") or ""
        aliases = meta.get("alt_names") or []
        desc    = meta.get("description") or ""
        attrib  = meta.get("attribution") or []
        refs    = (meta.get("urls") or []) + (meta.get("library_entries") or [])
        frows.append((name, common, meta.get("updated") or "", json.dumps(meta)))
        srows.append((
            name, common,
            " ".join(map(str, aliases)),
            desc,
            " ".join(map(str, attrib)),
            " ".join(map(str, refs)),
        ))

    with closing(_db()) as c:
        c.execute("DELETE FROM families")
        c.execute("DELETE FROM search")
        c.executemany(
            "INSERT INTO families(name,common_name,updated,data) VALUES(?,?,?,?)", frows
        )
        c.executemany(
            "INSERT INTO search(name,common_name,aliases,description,attribution,refs) "
            "VALUES(?,?,?,?,?,?)", srows
        )
        c.commit()
    log.info("[=^..^=] corpus refreshed: %d families indexed", len(frows))
    return {"total": len(frows)}


# -- tags (local-LLM enrichment) -----------------------------------

def _tags(caps_json: str | None, secs_json: str | None) -> tuple[list, list]:
    try:
        caps = json.loads(caps_json) if caps_json else []
    except Exception:
        caps = []
    try:
        secs = json.loads(secs_json) if secs_json else []
    except Exception:
        secs = []
    return caps, secs


# -- full-text search ----------------------------------------------

def search_corpus(q: str, limit: int = 40) -> list[dict[str, Any]]:
    terms = re.findall(r"\w+", q.lower())
    if not terms:
        return []
    match = " ".join(f"{t}*" for t in terms)
    with closing(_db()) as c:
        try:
            rows = c.execute(
                "SELECT s.name AS name, s.common_name AS common_name, "
                "snippet(search, 3, '<<', '>>', '…', 10) AS snippet, "
                "f.updated AS updated, f.data AS data, "
                "t.capabilities AS caps, t.sectors AS secs "
                "FROM search s JOIN families f ON f.name = s.name "
                "LEFT JOIN tags t ON t.name = s.name "
                "WHERE search MATCH ? ORDER BY bm25(search) LIMIT ?",
                (match, limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            log.warning("[=^..^=] FTS query error: %s", exc)
            return []

    out = []
    for r in rows:
        meta = json.loads(r["data"])
        caps, secs = _tags(r["caps"], r["secs"])
        out.append({
            "name": r["name"],
            "common_name": r["common_name"],
            "aliases": meta.get("alt_names") or [],
            "attribution": meta.get("attribution") or [],
            "capabilities": caps,
            "sectors": secs,
            "snippet": r["snippet"],
            "updated": r["updated"],
        })
    return out


# -- semantic search (embeddings; vectors precomputed by worker.py --embed) ----

_EMB_NAMES: list[str] = []
_EMB_MAT: np.ndarray | None = None   # (N, D), L2-normalized


def load_embeddings() -> None:
    """Load precomputed vectors into memory (normalized). Cheap; no model needed."""
    global _EMB_NAMES, _EMB_MAT
    with closing(_db()) as c:
        rows = c.execute("SELECT name, vec FROM embeddings").fetchall()
    if not rows:
        _EMB_NAMES, _EMB_MAT = [], None
        return
    _EMB_NAMES = [r["name"] for r in rows]
    mat = np.vstack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    _EMB_MAT = mat / np.where(norms == 0, 1.0, norms)
    log.info("[=^..^=] embeddings loaded: %d vectors", len(_EMB_NAMES))


def _embed_query(text: str) -> np.ndarray:
    """Embed a short query string via Ollama (tiny model, fine on CPU)."""
    body = json.dumps({"model": OLLAMA_EMBED_MODEL, "prompt": text}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/embeddings", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        v = np.array(json.loads(r.read())["embedding"], dtype=np.float32)
    n = np.linalg.norm(v)
    return v / n if n else v


def _results_for(names: list[str]) -> list[dict[str, Any]]:
    """Build result cards for an ordered list of family names (preserving order)."""
    if not names:
        return []
    rows_by: dict[str, sqlite3.Row] = {}
    with closing(_db()) as c:
        q = ",".join("?" * len(names))
        for r in c.execute(
            f"SELECT f.name, f.common_name, f.updated, f.data, "
            f"t.capabilities AS caps, t.sectors AS secs FROM families f "
            f"LEFT JOIN tags t ON t.name = f.name WHERE f.name IN ({q})", names
        ):
            rows_by[r["name"]] = r
    out = []
    for name in names:
        r = rows_by.get(name)
        if not r:
            continue
        meta = json.loads(r["data"])
        caps, secs = _tags(r["caps"], r["secs"])
        out.append({
            "name": r["name"], "common_name": r["common_name"],
            "aliases": meta.get("alt_names") or [],
            "attribution": meta.get("attribution") or [],
            "capabilities": caps, "sectors": secs, "updated": r["updated"],
        })
    return out


def semantic_search(q: str, limit: int = 40) -> list[dict[str, Any]]:
    if _EMB_MAT is None:
        return []
    scores = _EMB_MAT @ _embed_query(q)
    top = np.argsort(-scores)[:limit]
    return _results_for([_EMB_NAMES[i] for i in top])


def similar_families(name: str, limit: int = 8) -> list[dict[str, Any]]:
    if _EMB_MAT is None or name not in _EMB_NAMES:
        return []
    i = _EMB_NAMES.index(name)
    scores = _EMB_MAT @ _EMB_MAT[i]
    scores[i] = -1.0   # drop self
    top = np.argsort(-scores)[:limit]
    return _results_for([_EMB_NAMES[j] for j in top])


# -- reports -------------------------------------------------------

def _report_summary(fams: list[dict], window: str) -> str:
    n = len(fams)
    if n == 0:
        return f"Nothing updated in the last {window}. Malpedia is quiet. =^..^="
    actors: Counter = Counter()
    for f in fams:
        for a in f.get("attribution") or []:
            actors[str(a)] += 1
    top = ", ".join(a for a, _ in actors.most_common(3)) or "various / unattributed"
    notable = ", ".join(f["name"] for f in fams[:5])
    return (
        f"{n} famil{'y' if n == 1 else 'ies'} updated in the last {window}. "
        f"Most active actors: {top}. "
        f"Notable: {notable}{'…' if n > 5 else ''}."
    )


def window_report(window: str) -> dict[str, Any]:
    days = WINDOW_DAYS.get(window, 1)
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with closing(_db()) as c:
        rows = c.execute(
            "SELECT f.name, f.common_name, f.updated, f.data, "
            "t.capabilities AS caps, t.sectors AS secs FROM families f "
            "LEFT JOIN tags t ON t.name = f.name "
            "WHERE f.updated >= ? ORDER BY f.updated DESC",
            (cutoff,),
        ).fetchall()

    fams = []
    for r in rows:
        meta = json.loads(r["data"])
        caps, secs = _tags(r["caps"], r["secs"])
        fams.append({
            "name": r["name"],
            "common_name": r["common_name"],
            "updated": r["updated"],
            "aliases": meta.get("alt_names") or [],
            "attribution": meta.get("attribution") or [],
            "capabilities": caps,
            "sectors": secs,
            "description": meta.get("description") or "",
            "urls": meta.get("urls") or [],
        })
    return {
        "window": window,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(fams),
        "summary": _report_summary(fams, window),
        "families": fams,
    }


def find_by_actor(actor: str, limit: int = 300) -> list[dict[str, Any]]:
    """Every family attributed to `actor`, from the local corpus (no API call)."""
    like = f'%"{actor}"%'  # attribution is stored as a JSON list of names
    with closing(_db()) as c:
        rows = c.execute(
            "SELECT f.name, f.common_name, f.updated, f.data, "
            "t.capabilities AS caps, t.sectors AS secs FROM families f "
            "LEFT JOIN tags t ON t.name = f.name "
            "WHERE f.data LIKE ? ORDER BY f.updated DESC LIMIT ?",
            (like, limit),
        ).fetchall()
    out = []
    for r in rows:
        meta = json.loads(r["data"])
        attrib = [str(a) for a in (meta.get("attribution") or [])]
        if actor not in attrib:  # confirm exact match (LIKE is just a prefilter)
            continue
        caps, secs = _tags(r["caps"], r["secs"])
        out.append({
            "name": r["name"],
            "common_name": r["common_name"],
            "updated": r["updated"],
            "aliases": meta.get("alt_names") or [],
            "attribution": meta.get("attribution") or [],
            "capabilities": caps,
            "sectors": secs,
            "description": meta.get("description") or "",
        })
    return out


def store_report(window: str) -> None:
    """Compile a window report and persist it if anything changed."""
    rep = window_report(window)
    if rep["count"] == 0:
        log.info("[=^..^=] %s monitor: nothing new, skipping", window)
        return
    with closing(_db()) as c:
        c.execute(
            "INSERT INTO reports(created, window, count, summary, families) "
            "VALUES(?,?,?,?,?)",
            (rep["generated"], window, rep["count"], rep["summary"],
             json.dumps([f["name"] for f in rep["families"]])),
        )
        c.execute(
            "DELETE FROM reports WHERE id NOT IN "
            "(SELECT id FROM reports ORDER BY id DESC LIMIT ?)",
            (KEEP_REPORTS,),
        )
        c.commit()
    log.info("[=^..^=] %s monitor: stored report (%d families)", window, rep["count"])


# -- scheduled monitor ---------------------------------------------

async def monitor(window: str) -> None:
    log.info("[=^..^=] %s monitor tick…", window)
    try:
        if window == "day":            # refresh corpus once per day
            await asyncio.to_thread(refresh_corpus)
        await asyncio.to_thread(store_report, window)
    except Exception as exc:  # noqa: BLE001
        log.error("[=^..^=] %s monitor failed: %s", window, exc)


# -- family detail (yara + samples) --------------------------------

@functools.lru_cache(maxsize=256)
def _family_detail(name: str) -> dict[str, Any]:
    client = _mp()
    data = client.get_family(name)
    if not isinstance(data, dict):
        return {}

    yara: list[dict[str, Any]] = []
    try:
        raw = client.get_yara(name)
        if isinstance(raw, dict):
            for tlp, rules in raw.items():
                if isinstance(rules, dict):
                    for fn, text in rules.items():
                        yara.append({"tlp": tlp, "file": fn, "rule": text})
    except Exception as exc:  # noqa: BLE001
        log.info("[=^..^=] no yara for %s (%s)", name, exc)
    data["yara"] = yara

    data["samples_locked"] = False
    try:
        s = client.list_samples(name)
        data["samples"] = s if isinstance(s, list) else []
    except Exception as exc:  # noqa: BLE001
        data["samples"] = []
        if _is_auth_error(exc):
            data["samples_locked"] = True
    data["authenticated"] = AUTHENTICATED
    return data


# -- app -----------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if _corpus_size() == 0:
        log.info("[=^..^=] empty corpus - seeding from malpedia (one-time)…")
        await asyncio.to_thread(refresh_corpus)
    else:
        log.info("[=^..^=] corpus has %d families - refreshing in background", _corpus_size())
        asyncio.create_task(asyncio.to_thread(refresh_corpus))
    load_embeddings()

    sched = AsyncIOScheduler(timezone="UTC")
    sched.add_job(monitor, "cron", args=["day"],   hour=8,  id="daily")
    sched.add_job(monitor, "cron", args=["week"],  hour=8,  day_of_week="mon", id="weekly")
    sched.add_job(monitor, "cron", args=["month"], hour=8,  day=1, id="monthly")
    sched.start()
    log.info("[=^..^=] monitor scheduled (daily/weekly/monthly @ 08:00 UTC)")
    try:
        yield
    finally:
        sched.shutdown(wait=False)


app = FastAPI(title="whiskers", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/api/search")
def search(q: str = Query(..., min_length=1)):
    log.info("[=^..^=] full-text hunt: %s", q.strip())
    results = search_corpus(q.strip())
    return {"query": q.strip(), "results": results, "count": len(results)}


@app.get("/api/semantic")
def semantic(q: str = Query(..., min_length=1)):
    if _EMB_MAT is None:
        raise HTTPException(status_code=503, detail="no embeddings yet - run: python worker.py --embed")
    log.info("[=^..^=] semantic hunt: %s", q.strip())
    try:
        results = semantic_search(q.strip())
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=503, detail=f"embed model unavailable at {OLLAMA_HOST}: {exc}")
    return {"query": q.strip(), "results": results, "count": len(results), "mode": "semantic"}


@app.get("/api/similar/{name:path}")
def similar(name: str):
    return {"name": name, "families": similar_families(name)}


@app.get("/api/report")
def report(window: str = Query("day")):
    if window not in WINDOW_DAYS:
        raise HTTPException(status_code=400, detail="window must be day|week|month")
    log.info("[=^..^=] report requested: last %s", window)
    return window_report(window)


@app.get("/api/actor/{name:path}")
def actor(name: str):
    actor = name.strip()
    log.info("[=^..^=] actor pivot: %s", actor)
    fams = find_by_actor(actor)
    return {"actor": actor, "count": len(fams), "families": fams}


@app.get("/api/reports")
def reports_history(limit: int = Query(20, le=KEEP_REPORTS)):
    with closing(_db()) as c:
        rows = c.execute(
            "SELECT created, window, count, summary FROM reports "
            "ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return {"reports": [dict(r) for r in rows]}


@app.get("/api/by-tag")
def by_tag(tag: str = Query(..., min_length=1), kind: str = Query("capability")):
    col = "capabilities" if kind == "capability" else "sectors"
    log.info("[=^..^=] tag pivot: %s=%s", kind, tag)
    like = f'%"{tag}"%'
    with closing(_db()) as c:
        rows = c.execute(
            f"SELECT f.name, f.common_name, f.updated, f.data, "
            f"t.capabilities AS caps, t.sectors AS secs FROM tags t "
            f"JOIN families f ON f.name = t.name "
            f"WHERE t.{col} LIKE ? ORDER BY f.updated DESC LIMIT 300",
            (like,),
        ).fetchall()
    fams = []
    for r in rows:
        caps, secs = _tags(r["caps"], r["secs"])
        pool = caps if kind == "capability" else secs
        if tag not in pool:
            continue
        meta = json.loads(r["data"])
        fams.append({
            "name": r["name"],
            "common_name": r["common_name"],
            "updated": r["updated"],
            "aliases": meta.get("alt_names") or [],
            "attribution": meta.get("attribution") or [],
            "capabilities": caps,
            "sectors": secs,
            "description": meta.get("description") or "",
        })
    return {"tag": tag, "kind": kind, "count": len(fams), "families": fams}


@app.get("/api/tags")
def tags_overview():
    caps: Counter = Counter()
    secs: Counter = Counter()
    with closing(_db()) as c:
        tagged = c.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        total = c.execute("SELECT COUNT(*) FROM families").fetchone()[0]
        for row in c.execute("SELECT capabilities, sectors FROM tags"):
            cc, ss = _tags(row["capabilities"], row["sectors"])
            caps.update(cc)
            secs.update(ss)
    return {
        "tagged": tagged,
        "total": total,
        "capabilities": dict(caps.most_common()),
        "sectors": dict(secs.most_common()),
    }


@app.get("/api/family/{name:path}")
def family(name: str):
    log.info("[=^..^=] fetching family profile: %s", name)
    try:
        data = _family_detail(name)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "404" in msg or "No Family" in msg:
            raise HTTPException(status_code=404, detail=f"Family '{name}' not found")
        raise HTTPException(status_code=502, detail=f"malpedia fetch failed: {exc}")
    if not data:
        raise HTTPException(status_code=404, detail=f"Family '{name}' not found")
    # attach tags on a copy (don't poison the lru_cache of _family_detail)
    with closing(_db()) as c:
        row = c.execute(
            "SELECT capabilities, sectors FROM tags WHERE name = ?", (name,)
        ).fetchone()
    caps, secs = _tags(row["capabilities"], row["sectors"]) if row else ([], [])
    return {**data, "capabilities": caps, "sectors": secs}


# -- local-LLM features (on-demand, CPU) ---------------------------

def _family_context(name: str) -> str | None:
    """Assemble a compact text context for a family from the local corpus + tags.
    Fully offline - no Malpedia round-trip."""
    with closing(_db()) as c:
        frow = c.execute("SELECT data FROM families WHERE name = ?", (name,)).fetchone()
        trow = c.execute("SELECT capabilities, sectors FROM tags WHERE name = ?", (name,)).fetchone()
    if not frow:
        return None
    m = json.loads(frow["data"])
    caps, secs = _tags(trow["capabilities"], trow["sectors"]) if trow else ([], [])
    parts = [
        f"Family: {name}" + (f" ({m.get('common_name')})" if m.get("common_name") else ""),
        f"Aliases: {', '.join(m.get('alt_names') or []) or '-'}",
        f"Attribution: {', '.join(str(a) for a in (m.get('attribution') or [])) or 'unattributed'}",
        f"Capabilities: {', '.join(caps) or '-'} | Sectors: {', '.join(secs) or '-'}",
        f"Description: {(m.get('description') or '').strip()[:1800] or 'none'}",
    ]
    return "\n".join(parts)


def _llm_error(exc: Exception) -> HTTPException:
    if isinstance(exc, urllib.error.URLError):
        return HTTPException(
            status_code=503,
            detail=f"local LLM unavailable - is ollama running? ({OLLAMA_HOST})",
        )
    return HTTPException(status_code=502, detail=f"LLM error: {exc}")


class YaraReq(BaseModel):
    rule: str


class AskReq(BaseModel):
    name: str
    question: str


@app.get("/api/brief/{name:path}")
def brief(name: str, refresh: bool = Query(False)):
    if not refresh:
        with closing(_db()) as c:
            row = c.execute("SELECT text FROM briefs WHERE name = ?", (name,)).fetchone()
        if row:
            return {"name": name, "brief": row["text"], "model": OLLAMA_MODEL, "cached": True}

    ctx = _family_context(name)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Family '{name}' not in corpus")
    log.info("[=^..^=] brief requested: %s", name)
    prompt = (
        "You are a senior SOC analyst. Write a concise threat brief for the malware "
        "family below. Be specific and practical. Do NOT invent facts beyond the data.\n\n"
        f"{ctx}\n\n"
        "Write plain text, under 110 words, exactly:\n"
        "1) One sentence - what it is.\n"
        "2) One sentence - primary risk / impact.\n"
        "3) 'Detection:' then 2-3 short bullet hunting ideas grounded in the above."
    )
    try:
        text = _ollama(prompt, max_tokens=320)
    except Exception as exc:  # noqa: BLE001
        raise _llm_error(exc)
    with closing(_db()) as c:
        c.execute(
            "INSERT OR REPLACE INTO briefs(name, text, model, created) VALUES(?,?,?,?)",
            (name, text, OLLAMA_MODEL,
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        c.commit()
    return {"name": name, "brief": text, "model": OLLAMA_MODEL, "cached": False}


@functools.lru_cache(maxsize=128)
def _explain_yara(rule: str) -> str:
    prompt = (
        "Explain this YARA rule for a SOC analyst in plain English. Cover: "
        "(1) what it matches, (2) notable strings/conditions, (3) false-positive risk. "
        "Be concise, under 90 words. Do not output YARA, only the explanation.\n\n"
        f"RULE:\n{rule[:2800]}"
    )
    return _ollama(prompt, max_tokens=260)


@app.post("/api/explain-yara")
def explain_yara(req: YaraReq):
    if not req.rule.strip():
        raise HTTPException(status_code=400, detail="empty rule")
    log.info("[=^..^=] yara explain (%d chars)", len(req.rule))
    try:
        return {"explanation": _explain_yara(req.rule.strip())}
    except Exception as exc:  # noqa: BLE001
        raise _llm_error(exc)


@functools.lru_cache(maxsize=256)
def _ask_cached(name: str, question: str) -> str:
    ctx = _family_context(name)
    if ctx is None:
        raise KeyError(name)
    prompt = (
        "Answer the analyst's question using ONLY the data about this malware family "
        "below. If the answer is not in the data, say 'Not stated in Malpedia data.' "
        "Be concise (under 80 words).\n\n"
        f"{ctx}\n\n"
        f"Question: {question}\nAnswer:"
    )
    return _ollama(prompt, max_tokens=240)


@app.post("/api/ask")
def ask(req: AskReq):
    q = req.question.strip()
    if not q:
        raise HTTPException(status_code=400, detail="empty question")
    log.info("[=^..^=] ask %s: %s", req.name, q)
    try:
        return {"name": req.name, "question": q, "answer": _ask_cached(req.name, q)}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Family '{req.name}' not in corpus")
    except Exception as exc:  # noqa: BLE001
        raise _llm_error(exc)


if __name__ == "__main__":
    import uvicorn

    # 0.0.0.0 = reachable from other devices on the LAN (override via env).
    host = os.getenv("WHISKERS_HOST", "0.0.0.0")
    port = int(os.getenv("WHISKERS_PORT", "8000"))
    log.info("[=^..^=] serving on http://%s:%d (LAN-accessible)", host, port)
    uvicorn.run(app, host=host, port=port)
