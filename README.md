# whiskers  =^..^=

> low-noise threat intel - a minimalist micro TI / SOC context panel powered by local AI + Malpedia API.

**whiskers** is a single-purpose tool for malware-family context. Search the whole Malpedia
corpus full-text, read a tactical card per family, pivot by threat actor or capability, pull
recent-activity reports, export detection content, and - optionally - enrich and explain it all
with a **local LLM running on your own CPU/GPU**. No cloud, no build step, no Node.    

Tactical, fast, offline-friendly. Subtle cat vibes in the logs.     

---

## Philosophy

The backend is one `main.py`, the frontend is single `static/index.html` (vanilla JS + Tailwind via CDN). Storage is SQLite with FTS5 - a file, not a daemon. The only moving parts you add are optional: Malpedia membership for samples and YARA, and `ollama` for the AI features.    

---

## Tech stack

`Python 3.10+` · `FastAPI` · `malpediaclient` · `SQLite + FTS5` (stdlib) · `APScheduler` ·
vanilla `HTML5 / JS` + `Tailwind` (CDN). Local LLM via `Ollama`. No NPM, no bundler.     

```bash
main.py            single-file FastAPI backend + scheduler
worker.py          background local-LLM tagger (standalone)
static/index.html  the entire frontend
whiskers.db        SQLite corpus + FTS5 index + tags/briefs (auto-created)
.env               keys and config
```

---

## Quick start

```bash
pip install -r requirements.txt
echo "MALPEDIA_API_KEY=your_token_here" > .env   # optional, see below
uvicorn main:app --reload or python3 main.py
```

Open `http://127.0.0.1:8000`. On first launch whiskers seeds the local corpus from Malpedia
(`~3700` families, a few seconds), then refreshes it in the background on every start.     

To expose it to other devices on your LAN, run it directly - it binds to `0.0.0.0:8000`:     

```bash
python main.py        # then visit http://<your-ip>:8000 from any device
```

Host and port are overridable with `WHISKERS_HOST` / `WHISKERS_PORT`. Binding to `0.0.0.0`
discover your Malpedia member features to everyone on the network - only do it on a
trusted LAN.    

---

## Malpedia access

Malpedia is an invite-only community knowledge base. whiskers works **anonymously** for search, family context, and public (`tlp_white`) YARA. A member API token in `.env` unlocks the rest:   

```python
MALPEDIA_API_KEY=...
```

With a token, the family card gains **sample listings** and the full set of `TLP`-restricted YARA
rules. Without one, those sections show a friendly "membership required" note and everything else
keeps working. The header badge reflects your state - `● member` or `○ anonymous`.    

---

## Features

**Full-text search** - FTS5 with bm25 ranking across family names, aliases, descriptions, threat
actors, and report references. Search by concept (`amsi bypass`), by actor (`TA505`), or by a
citation domain - matched terms are highlighted in the result snippet.    

**Family card** - canonical name, aliases, description, attribution, references, the full YARA
rule text, and known samples. Threat-actor and capability tags are clickable.    

**Pivots** - click an actor to see their entire arsenal across platforms; click a capability or
sector tag to list every family that shares it. Both run as instant local queries.    

**Activity reports** - one click pulls families updated on Malpedia in the last day, week, or
month, rendered as full cards with an executive summary. A background monitor (daily / weekly /
monthly at 08:00 UTC) compiles and stores the same reports automatically.     

**Detection export** - download a family's YARA rules as a ready-to-deploy `.yar`, or copy all
sample SHA256 hashes to the clipboard for a SIEM/EDR hunt. Also a one-click "copy as Markdown"
brief for dropping a family into a ticket.    

---

## Local LLM (optional, CPU-friendly but GPU is better)

whiskers leans on a small local model through [Ollama](https://ollama.com) for two things:
on-demand analysis in the UI, and background enrichment via the worker. Everything stays on your
machine - prompts and corpus never leave the host, which matters for a SOC.      

For example:    

```bash
ollama serve
ollama pull qwen3:1.7b # <- CPU-friendly in my case (Thinkpad X1 nano with no GPU)
```

Point whiskers at it (defaults shown):    

```python
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen3:1.7b
```

### On-demand features (in the UI)

These appear on the family card and run only when you click - a few seconds on CPU.    

- **🧠 brief** - a tight SOC brief: what it is, primary risk, and two or three detection ideas. Cached after first generation.    
- **🧠 explain rule** - plain-English explanation of a YARA rule and its false-positive risk; turns opaque auto-generated rules into something readable.    
- **ask this family** - ask a question answered *only* from that family's data; it refuses ("Not stated in Malpedia data") rather than guess.   

If Ollama isn't running, these report it inline and the rest of whiskers is unaffected.    

### Background tagging worker

`worker.py` classifies every family into closed-vocabulary **capabilities** (ransomware, stealer,
loader, …) and **target sectors** (finance, healthcare, government, …), writing to the corpus.     
Those tags power the capability/sector chips and pivots.     

```bash
python worker.py                # tag everything still untagged
python worker.py --limit 20     # try a small batch first
```

It is resumable and `Ctrl-C` safe - it only processes families not yet tagged, so you can stop and
restart freely. On a terminal you get a live progress bar with ETA; piped to a file it prints
periodic checkpoint lines instead. To run the full corpus unattended:      

```bash
nohup python worker.py --log-every 25 > tag.log 2>&1 &
tail -f tag.log
```

The worker is fully decoupled from the web app - run it whenever, the UI picks up tags as they land.    

---

## Choosing an Ollama model

The tagging and analysis tasks are *constrained* - classification and short, grounded summaries -
so a small instruction-tuned model is the right tool. Reasoning models (qwen3) are run with
thinking disabled (`think: false`); without that flag they emit huge `<think>` blocks that take
minutes per call on CPU.

CPU inference scales roughly with parameter count, so it is a quality-versus-time trade-off:

| Model | ~per family | Full corpus (~3700) | Notes |
| --- | --- | --- | --- |
| `qwen3:1.7b` | ~7 s | ~7 h | default - fastest usable |
| `gemma2:2b` | ~8 s | ~8 h | comparable quality, slightly slower |
| `llama3.2:3b` | ~12 s | ~13 h | noticeably better tags |
| `qwen3:4b` | ~16 s | ~17 h | better still, slowest of these |

Stay on `qwen3:1.7b` to get coverage fast; switch to `llama3.2:3b` for a real quality bump
overnight. Heavier models (8B–70B) are best saved for a GPU - the schema and UI don't change, you
just set `OLLAMA_MODEL` and re-run.     

---

## Configuration

All via `.env` (or shell environment):    

```bash
MALPEDIA_API_KEY   Malpedia member token  (optional)
OLLAMA_HOST        Ollama endpoint        (default http://localhost:11434)
OLLAMA_MODEL       model for AI features  (default qwen3:1.7b)
WHISKERS_DB        SQLite path            (default whiskers.db)
WHISKERS_HOST      bind host              (default 0.0.0.0)
WHISKERS_PORT      bind port              (default 8000)
```

---

## Credits

Powered by **[cocomelonc](https://cocomelonc.github.io/)**. Inspired by and built on the **[Malpedia](https://malpedia.caad.fkie.fraunhofer.de/)** community knowledge base - all family intelligence comes from their work and contributors.    

whiskers - low-noise threat intel - meow =^..^=    
