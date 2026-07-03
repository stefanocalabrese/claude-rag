# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Local-first RAG + memory system for Claude Code running on macOS (Apple Silicon). Two collections in one stack:

- **Memory** — searchable archive of Claude Code sessions, project files, exported claude.ai chats.
- **Knowledge** — classic RAG over docs, books, manuals, reference material (PDF-aware).

Everything runs locally: LM Studio for embeddings (`localhost:1234/v1`), LanceDB for vector storage. No cloud, no daemons beyond what you launch.

## Architecture

```
Claude Code (MCP client via stdio)
        │ stdio
   MCP server (FastMCP, Python)
  ┌─────┴───────┐
search_* tools  │ vector search
        │       ▼
   LM Studio (embeddings)   LanceDB (~/.local/share/claude-rag/lancedb/)
                              ├── memory.lance    (Claude history)
                              └── knowledge.lance (docs/books/manuals)
```

Two LanceDB tables share one database folder. Independent compaction/versioning, no cross-contamination.

## Key files

| File | Purpose |
|------|---------|
| `src/claude_rag/core.py` | Shared library: chunk → embed (LM Studio) → upsert (LanceDB), dedup, search |
| `src/claude_rag/mcp_server.py` | FastMCP server — `search_memory`, `search_knowledge`, `search_all`, `search_project`, `search_recent` |
| `ingest_files.py` | Walk project dirs → memory table (skips hidden/noise dirs) |
| `ingest_transcript.py` | Ingest a single Claude Code session JSONL → memory (SessionEnd hook) |
| `ingest_export.py` | Ingest claude.ai exports from inbox → memory |
| `ingest_knowledge.py` | PDF-aware ingestion → knowledge table (manual, `<path>`) |
| `claude-rag` | On-demand CLI: ensure LM Studio + model, then ingest/optimize; also `search`/`status` (symlinked onto PATH) |
| `claude_rag_sessionend_hook.sh` | SessionEnd hook (fire-and-forget bash script) |
| `claude-rag-plan.md` | Full architecture and design plan — read before structural changes |
| `HOW-TO-USE.md` | Practical usage guide: setup, search tools, troubleshooting |
| `CHANGELOG.md` | Release history |

## Storage layout (runtime, not in repo)

```
~/.local/share/claude-rag/
├── lancedb/memory.lance/       # memory table
├── lancedb/knowledge.lance/    # knowledge table
├── knowledge-inbox/            # drop PDFs/docs here (ingested on next `claude-rag sync`)
├── exports-inbox/              # drop claude.ai exports here (ingested on next `claude-rag sync`)
├── logs/sessions/              # transcripts archived by the SessionEnd hook
├── locks/                      # lock files for writer serialization
└── logs/                       # ingestion + sync logs
```

## Common commands

### `claude-rag` CLI (on-demand — the primary entry point)

Installed as a symlink on PATH (`/opt/homebrew/bin/claude-rag` → repo). Ensures
LM Studio is running with the embedding model loaded (launching the app if
needed) before anything that embeds. There is **no background scheduler** — you
run it when you want to refresh the index.

```bash
claude-rag                 # sync: ingest ~/Projects + ~/Documents/notes + inboxes, then optimize
claude-rag sync <dir>...   # sync specific dirs instead of the defaults
claude-rag search <query>  # semantic search across memory + knowledge
claude-rag status          # LM Studio + table status
```

### Ingestion (direct scripts, need the venv + LM Studio already up)

```bash
.venv/bin/python ingest_files.py [--dirs <dir>...]        # project files → memory
.venv/bin/python ingest_transcript.py <jsonl-or-dir>       # session transcript(s) → memory
.venv/bin/python ingest_export.py                          # claude.ai exports inbox → memory
.venv/bin/python ingest_knowledge.py <pdf-or-folder>       # PDF/doc → knowledge
```

### Inboxes (swept by `claude-rag sync`)

```bash
~/.local/share/claude-rag/knowledge-inbox/    # docs/books/manuals
~/.local/share/claude-rag/exports-inbox/      # claude.ai exports
~/.local/share/claude-rag/logs/sessions/      # transcripts archived by the SessionEnd hook
```

### Claude Code MCP registration

Register the MCP server using the project venv and the real server path
(`src/claude_rag/mcp_server.py`). The venv is required — the server imports
`fastmcp`/`lancedb`, which only exist inside it:

```bash
claude mcp add claude-rag -- \
  <repo-root>/.venv/bin/python <repo-root>/src/claude_rag/mcp_server.py
```

## Prerequisites

- **LM Studio** with the embedding model downloaded (not a chat model), serving the OpenAI-compatible API at `http://localhost:1234/v1`. `claude-rag` starts the app/server and loads the model on demand, so it need not be running beforehand — but the model must be downloaded.
- **LanceDB** installed (in the venv). Data lives as files on disk.

## Hardware target

MacBook Pro, Apple Silicon, 64 GB unified memory. Embedding models are <1 GB; co-run with the main LLM in LM Studio without contention.

## Design principles

- **Local-first** — no external API calls; LM Studio + LanceDB both on-device.
- **Two collections, one stack** — memory vs knowledge share machinery but stay in separate tables.
- **RAG is the archive, not working memory** — CLAUDE.md stays as live instructions; RAG holds retrievable long-term knowledge.
- **On-demand maintenance** — `claude-rag` runs ingest + `optimize()` + 7-day version pruning when you invoke it; no background scheduler.
- **Fire-and-forget capture** — the SessionEnd hook archives transcripts without slowing your workflow (detached subprocess, returns immediately).
- **Idempotent ingestion** — mtime + content_hash dedup; re-run freely, no duplicates.

## Current status

Working end-to-end (verified 2026-07-02). Environment is `uv`-managed
(`.venv`, Python 3.12); deps installed via `uv pip install -e ".[dev]"`.
Embeddings served by LM Studio model `text-embedding-nomic-embed-text-v1.5`
(768-dim) — set as the default in `core.py`, overridable via
`CLAUDE_RAG_LM_STUDIO_EMBEDDING_MODEL`. Operated on demand via the `claude-rag`
CLI (no launchd scheduler). The MCP server is registered with Claude Code and
the SessionEnd hook archives transcripts to the sessions inbox. Verified: real
ingest (memory + knowledge), semantic search, project filter, idempotent dedup,
hidden-dir exclusion, `optimize_table`, and all 6 MCP tools over stdio.
