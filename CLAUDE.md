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
| `ingest_files.py` | Walk project dirs → memory table (nightly + manual) |
| `ingest_transcript.py` | Ingest a single Claude Code session JSONL → memory (SessionEnd hook) |
| `ingest_export.py` | Ingest claude.ai exports from inbox → memory (nightly + manual) |
| `ingest_knowledge.py` | PDF-aware ingestion → knowledge table (manual, `<path>`) |
| `claude_rag_sessionend_hook.sh` | SessionEnd hook (fire-and-forget bash script) |
| `com.clauderag.nightly.plist` | launchd agent (nightly ingest + maintenance at 03:00) |
| `claude-rag-plan.md` | Full architecture and design plan — read before structural changes |
| `HOW-TO-USE.md` | Practical usage guide: setup, search tools, troubleshooting |
| `CHANGELOG.md` | Release history |

## Storage layout (runtime, not in repo)

```
~/.local/share/claude-rag/
├── lancedb/memory.lance/       # memory table
├── lancedb/knowledge.lance/    # knowledge table
├── knowledge-inbox/            # drop PDFs/docs here (nightly pickup)
├── exports-inbox/              # drop claude.ai exports here (nightly pickup)
├── locks/                      # lock files for writer serialization
└── logs/                       # ingestion + maintenance logs
```

## Common commands

### Ingestion (manual, immediate)

```bash
# Re-index project files → memory
python ingest_files.py

# Ingest a single transcript or folder of transcripts → memory
python ingest_transcript.py <path-to-jsonl-or-dir>

# Ingest exported claude.ai chats from inbox → memory
python ingest_export.py

# Ingest a PDF/book/manual → knowledge (PDF-aware, heading-aware chunking)
python ingest_knowledge.py <path-to-pdf-or-folder>
```

### Inboxes (lazy, nightly pickup via launchd)

```bash
~/.local/share/claude-rag/knowledge-inbox/    # docs/books/manuals
~/.local/share/claude-rag/exports-inbox/      # claude.ai exports
```

### Claude Code MCP registration

After scaffolding, register the MCP server:

```bash
claude mcp add claude-rag python <repo-root>/mcp_server.py
```

## Prerequisites

- **LM Studio** running with an **embedding model loaded** (not a chat model) at `http://localhost:1234/v1/embeddings`. The embedder must be loaded on startup — hooks and nightly jobs hit a cold endpoint otherwise.
- **LanceDB** installed (`pip install lancedb`). Data lives as files on disk.
- **macOS launchd** for the nightly agent (files + exports + maintenance at 03:00).

## Hardware target

MacBook Pro, Apple Silicon, 64 GB unified memory. Embedding models are <1 GB; co-run with the main LLM in LM Studio without contention.

## Design principles

- **Local-first** — no external API calls; LM Studio + LanceDB both on-device.
- **Two collections, one stack** — memory vs knowledge share machinery but stay in separate tables.
- **RAG is the archive, not working memory** — CLAUDE.md stays as live instructions; RAG holds retrievable long-term knowledge.
- **Passive maintenance** — nightly `optimize()` + 7-day version pruning; you don't think about it.
- **Fire-and-forget capture** — hooks never slow your workflow (detached subprocess, returns immediately).
- **Idempotent ingestion** — mtime + content_hash dedup; re-run freely, no duplicates.

## Current status

Scaffolded — all deliverables from `claude-rag-plan.md §10` are written. Syntax-checked and passing. Next steps: install deps (`pip install -e ".[dev]"`), load embedding model in LM Studio, test ingestion pipeline end-to-end.
