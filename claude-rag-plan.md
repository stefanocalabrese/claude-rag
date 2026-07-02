# Local RAG + Memory System for Claude Code — Build Plan

A local-first retrieval system running on macOS (Apple Silicon), serving two
purposes from one shared stack:

1. **Memory** — a searchable long-term archive of everything you do with Claude
   (project files, Claude Code session transcripts, exported claude.ai chats).
2. **Knowledge** — a classic RAG over documentation, books, manuals, and
   reference material you collect.

Everything runs locally. No external API calls, no daemons beyond what you
launch, no cloud dependency.

---

## 1. Architecture at a glance

```
┌─────────────────────────────────────────────────────────────┐
│                         Claude Code                         │
│                    (spawns MCP server via stdio)            │
└───────────────────────────────┬─────────────────────────────┘
                                 │ stdio
                    ┌────────────▼─────────────┐
                    │       MCP server         │
                    │  search_memory()         │
                    │  search_knowledge()      │
                    │  search_all()            │
                    │  search_project()        │
                    │  search_recent()         │
                    └─────┬──────────────┬─────┘
                          │              │
              embed query │              │ vector search
                          ▼              ▼
              ┌───────────────────┐  ┌──────────────────────────┐
              │     LM Studio     │  │        LanceDB           │
              │ localhost:1234/v1 │  │  ~/.local/share/         │
              │  (embeddings)     │  │    claude-rag/lancedb/   │
              └───────────────────┘  │                          │
                                     │  table: memory           │
                                     │  table: knowledge        │
                                     └──────────────────────────┘
                          ▲                       ▲
                          │                       │
        ┌─────────────────┴───────┐   ┌───────────┴───────────────┐
        │     INGESTION (memory)  │   │   INGESTION (knowledge)   │
        │                         │   │                           │
        │  ingest_files.py        │   │  ingest_knowledge.py      │
        │  ingest_transcript.py   │   │  (PDF-aware, on-demand)   │
        │  ingest_export.py       │   │                           │
        └─────────────────────────┘   └───────────────────────────┘
                ▲             ▲
                │             │
        SessionEnd hook   claude-rag CLI
        (archive)         (on-demand sync + maintenance)
```

---

## 2. Core components

### Stack
- **LM Studio** — embedding provider, OpenAI-compatible endpoint at
  `http://localhost:1234/v1/embeddings`. An **embedding** model must be loaded
  (not a chat model).
- **LanceDB** — embedded vector DB. No server; lives as files on disk inside
  the MCP/ingest processes.
- **MCP server** — local Python (FastMCP) process, stdio transport, launched by
  Claude Code.
- **CLAUDE.md** — unchanged. Stays as always-loaded live instructions. The RAG
  is the searchable long-term archive, NOT a replacement for working memory.

### Storage layout
```
~/.local/share/claude-rag/
├── lancedb/                 # the database (one folder = the DB)
│   ├── memory.lance/        # table: your Claude history
│   └── knowledge.lance/     # table: docs/books/manuals
├── knowledge-inbox/         # drop PDFs/docs here to be ingested
├── exports-inbox/           # drop exported claude.ai chats here
├── locks/                   # lock files to serialize writers
└── logs/                    # ingestion + maintenance logs
```

Persistence is automatic: writes go to disk immediately, survive restarts,
and the folder *is* the database (copy it to back up, move it to relocate).

---

## 3. Two collections, one stack

| Axis            | `memory` table                          | `knowledge` table                      |
|-----------------|-----------------------------------------|----------------------------------------|
| Sources         | project files, transcripts, exports     | docs, books, manuals, reference PDFs   |
| Nature          | personal, changing, you-specific        | external, static, authored by others   |
| Chunking        | exchange-level / small structured       | larger, heading-aware, with overlap    |
| Update cadence  | on-demand (`claude-rag`; hook archives) | on-demand (`claude-rag` / manual run)  |
| Retrieval       | "what was I doing / working on"         | "how does X work per the docs"         |

Kept as **two separate tables** in the same LanceDB folder: independent
compaction/versioning, no cross-contamination, clean per-collection queries.

---

## 4. Metadata schema (every chunk)

| Field           | Purpose                                                       |
|-----------------|---------------------------------------------------------------|
| `id`            | stable unique id (e.g. hash of path + chunk index)            |
| `vector`        | embedding (dim set by chosen LM Studio model)                 |
| `text`          | the chunk text, stored in-table (self-contained retrieval)    |
| `source_type`   | `project_file` / `transcript` / `claude_ai_export` / `knowledge` |
| `project`       | project name (for memory scoping)                             |
| `path`          | originating file path                                         |
| `title`         | doc/book/session title where available                        |
| `mtime`         | source modified time (incremental skip)                       |
| `content_hash`  | dedup / change detection                                      |
| `timestamp`     | ingestion time (for `search_recent`)                          |

---

## 5. Ingestion

### Shared core (one library)
`chunk → embed (LM Studio) → upsert (LanceDB)` with consistent dedup and
metadata. All entry points use this. Idempotent: re-running only adds new or
changed content (mtime + content_hash check).

### Memory entry points
- **`ingest_files.py`** — walks your project directories; indexes configured
  extensions; skips unchanged files.
- **`ingest_transcript.py`** — ingests a single Claude Code session JSONL from
  `~/.claude/projects/...`. Filters noise (tool-call spam, retries, dead ends);
  chunks meaningful exchanges. Skips trivial sessions (min-content threshold).
- **`ingest_export.py`** — ingests claude.ai chats you drop in
  `exports-inbox/`.

### Knowledge entry point
- **`ingest_knowledge.py <path>`** — PDF-aware. Extracts text with
  `pymupdf4llm` (structure-preserving), heading-aware chunking, larger chunks
  with overlap. Run manually when you add a book/manual. Writes to `knowledge`
  table.

---

## 6. Automation

**Design choice:** no background scheduler. Capture is event-driven (a
lightweight hook) and all heavy work is on-demand via the `claude-rag` command.
Nothing runs at 03:00; you refresh the index when you want to.

### SessionEnd hook (transcript archive)
- Reads the hook payload from **stdin** (Claude Code passes `transcript_path`
  as JSON on stdin, not as an argument).
- **Fire-and-forget**: copies the transcript to the sessions inbox and spawns a
  detached ingest, returning immediately so it never delays your shell.
- Skips trivial sessions below a content threshold. If LM Studio is down the
  immediate ingest simply fails; the archived copy is swept by the next
  `claude-rag sync`.

### `claude-rag` command (on-demand sync + maintenance)
- Installed as a symlink on `PATH` (`/opt/homebrew/bin/claude-rag`).
- **Ensures LM Studio is ready first**: if the server is unreachable it launches
  the app, starts the server, and loads the embedding model (guarded — a blind
  `lms load` on an already-loaded model spawns a duplicate instance).
- `claude-rag sync` (default) ingests project dirs + exports + sessions +
  knowledge inboxes, then runs LanceDB maintenance:
  - `table.optimize(cleanup_older_than=timedelta(days=7))` — compacts small
    files and prunes versions older than 7 days.
- Also `claude-rag search <query>` and `claude-rag status`.

### Concurrency safety
- A **lock file** in `locks/` serializes writers so a manual run and a
  hook-triggered ingest can't write simultaneously.
- Maintenance never runs in the retrieval/request path (no startup-time
  compaction in the MCP server).

---

## 7. Retrieval (MCP tools)

| Tool                              | Behavior                                       |
|-----------------------------------|------------------------------------------------|
| `search_memory(query, k)`         | semantic search over your Claude history       |
| `search_knowledge(query, k)`      | semantic search over docs/books/manuals        |
| `search_all(query, k)`            | both tables, results labeled by `source`       |
| `search_project(project, query)`  | memory scoped to one project (metadata filter) |
| `search_recent(query, days)`      | time-filtered memory ("what was I doing…")     |

Each result returns chunk text + source metadata, so Claude knows the origin of
every hit.

### Indexing note
- Under ~50–100k vectors LanceDB does exact brute-force search (always accurate,
  no index needed).
- Past that, build an ANN index (IVF-PQ or HNSW) — one call, stored alongside
  the table.

---

## 8. Maintenance strategy (compaction + cleanup)

Runs as part of every `claude-rag sync`, zero separate attention:
1. **Inline** — `optimize()` at the end of each sync (compacts + prunes in one
   pass; covers the common case for free).
2. **Safety net** — a threshold-based check (fragment count / version count)
   that only does real work when thresholds are crossed.

Caveats:
- Don't compact mid-write → lock file prevents overlap.
- Cleanup is destructive to history → 7-day retention keeps recent rollback
  points while reclaiming space.

---

## 9. How to use it

Every ingest path works the same underneath: **detect new/changed → chunk →
embed via LM Studio → upsert into the right table**. "Adding a document" is
always one of two gestures: drop it in an inbox (picked up on the next
`claude-rag sync`) or run the matching ingester (immediate). Content-hash dedup
means you can re-run any of these freely without creating duplicates.

> **LM Studio:** the `claude-rag` command handles this for you — it launches LM
> Studio, starts the server, and loads the embedding model on demand. Only the
> raw `ingest_*.py` scripts and the SessionEnd hook assume LM Studio is already
> up; if it isn't, they fail cleanly and `claude-rag sync` catches up later.

### 9.1 Adding to the KNOWLEDGE collection (docs, books, manuals)

On-demand path. Both options land in the `knowledge` table.

**Option A — drop in the inbox, sync later:**
```bash
cp ~/Downloads/some-manual.pdf ~/.local/share/claude-rag/knowledge-inbox/
```
`claude-rag sync` scans the inbox, ingests anything new, and embeds it — no
separate command needed the next time you sync.

**Option B — run it now (immediate):**
```bash
# single file
.venv/bin/python ingest_knowledge.py ~/Downloads/some-manual.pdf

# whole folder
.venv/bin/python ingest_knowledge.py ~/Documents/manuals/
```
PDF-aware (text extraction + heading-aware chunking); also handles markdown and
text. Idempotent — unchanged files are skipped via content hash.

### 9.2 Adding to the MEMORY collection (your Claude work)

Mostly automatic, with manual overrides available.

**Project files** — `claude-rag sync` walks your configured directories
(skipping hidden/noise dirs). To index a specific set immediately:
```bash
claude-rag sync ~/code ~/Documents/notes
```

**Claude Code transcripts** — the SessionEnd hook archives each finished session
to the sessions inbox; `claude-rag sync` ingests them. (Trivial sessions below
the content threshold are skipped.)

**Exported claude.ai chats** — drop them in the exports inbox:
```bash
cp ~/Downloads/my-export.json ~/.local/share/claude-rag/exports-inbox/
```
Ingested on the next `claude-rag sync`, or run immediately:
```bash
.venv/bin/python ingest_export.py
```

### 9.3 Searching (from Claude Code)

Once the MCP server is registered, the tools are available to Claude in any
session. Invoke naturally ("search my project memory for…", "check the manuals
for…") or rely on Claude to call them. Tools:

| Tool                              | Use when you want…                        |
|-----------------------------------|-------------------------------------------|
| `search_memory(query, k)`         | your past Claude work, any project        |
| `search_knowledge(query, k)`      | docs / books / manuals                    |
| `search_all(query, k)`            | both, results labeled by source           |
| `search_project(project, query)`  | memory scoped to one project              |
| `search_recent(query, days)`      | "what was I doing last week"              |

### 9.4 Operational cheatsheet

```bash
# Ensure LM Studio + model, then ingest everything and optimize
claude-rag                                    # (= claude-rag sync)

# Search from the terminal / check status
claude-rag search "how does dedup work"
claude-rag status

# Add a book/manual right now (LM Studio must already be up)
.venv/bin/python ingest_knowledge.py <path-to-pdf-or-folder>

# Inboxes (drop now, ingested on the next `claude-rag sync`)
~/.local/share/claude-rag/knowledge-inbox/    # docs/books/manuals
~/.local/share/claude-rag/exports-inbox/      # claude.ai exports

# Logs (what sync / hooks did)
~/.local/share/claude-rag/logs/
```

### 9.5 Supported file types

Currently planned: **PDF, markdown, text, code**. Want `.epub` (common for
books) or `.docx` manuals? Those need extra extractors — flag it and they'll be
wired into the ingesters.

---

## 10. Deliverables (built & verified)

- [x] Shared core: embed (LM Studio) + chunk + upsert + dedup
- [x] `ingest_files.py` (project files → memory; skips hidden/noise dirs)
- [x] `ingest_transcript.py` (Claude Code sessions → memory)
- [x] `ingest_export.py` (claude.ai exports → memory)
- [x] `ingest_knowledge.py` (PDF-aware → knowledge)
- [x] MCP server (`search_memory` / `search_knowledge` / `search_all` /
      `search_project` / `search_recent` + `table_stats`)
- [x] SessionEnd hook (fire-and-forget transcript archive, stdin payload)
- [x] `claude-rag` CLI (on-demand LM Studio ensure + sync/search/status)
- [x] `claude mcp add` registration
- [x] README + `HOW-TO-USE.md`

---

## 11. Open inputs (fill in before scaffolding)

1. **Project directories** to index, plus file extensions beyond `.md`
   (e.g. `~/code`, `~/projects`, `~/Documents/notes`; extensions `.py`, `.ts`,
   `.txt`, …).
2. **LM Studio embedding model identifier** — exact string as LM Studio lists
   it (e.g. `text-embedding-nomic-embed-text-v1.5`). Sets the vector dimension.
3. **Inbox locations** — defaults: `~/.local/share/claude-rag/knowledge-inbox/`
   and `~/.local/share/claude-rag/exports-inbox/` (override if you prefer).

---

## 12. Hardware & memory budget

**Target machine:** MacBook Pro, Apple Silicon, **64 GB unified memory**.

The embedding model and your main LLM are co-resident in LM Studio. LM Studio
supports multiple loaded models simultaneously, so running an embedder alongside
the LLM is fine — the only real constraint is RAM, and embedders are tiny.

### Co-running with Qwen3 30B-A3B (MoE)

| Component                     | Footprint (approx)        |
|-------------------------------|---------------------------|
| Qwen3 30B-A3B @ Q4            | ~17–19 GB                 |
| Qwen3 30B-A3B @ Q8            | ~25–33 GB                 |
| Embedding model               | < 1 GB (often 200–600 MB) |
| macOS + apps + Claude Code    | ~10–14 GB                 |

At **Q4**: ~18 + 1 + 12 ≈ **31 GB used, ~33 GB free**. Comfortable. Even at Q8,
both models + system still fit with room to spare.

### Notes specific to Apple Silicon

- **VRAM allocation limit** — macOS caps GPU-claimable unified memory (default
  ~67–75% of total, i.e. ~43–48 GB of 64). Both models' weights count against
  this GPU budget. At Q4 you're nowhere near the ceiling. Only if running Q8 +
  large context + embedder simultaneously would you approach it; raise it with
  `sudo sysctl iogpu.wired_limit_mb` if ever needed (not required for this
  setup).
- **MoE helps contention** — only ~3.3B of 30B params are active per token, so
  Qwen3 30B-A3B is light on compute. The brief overlap when the SessionEnd hook
  fires an embedding mid-inference is barely noticeable.
- **Confirm two models stay resident** — in LM Studio settings, ensure it
  doesn't auto-unload one model when loading another. You want both loaded, not
  swapping.

### Why contention is a non-issue here

- **Fire-and-forget hook** — per-session transcript archive runs detached; never
  blocks inference.
- **On-demand batch** — bulk file embedding happens only when you run
  `claude-rag`, so you choose a moment when you're not inferencing. Nothing
  competes in the background.

---

## 13. Design principles

- **Local-first** — no external calls; LM Studio + LanceDB both on-device.
- **Two collections, one stack** — memory vs. knowledge stay logically distinct,
  share machinery.
- **RAG is the archive, not the working memory** — CLAUDE.md stays as live
  instructions; RAG holds retrievable long-term knowledge.
- **On-demand maintenance** — `claude-rag` does the upkeep when you run it; no
  background scheduler.
- **Self-contained chunks** — chunk text stored in-table for simple, robust
  retrieval.
- **Fire-and-forget capture** — the SessionEnd hook never slows your workflow.
