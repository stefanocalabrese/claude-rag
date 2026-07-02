# claude-rag

Local-first RAG + memory system for Claude Code. Runs 100% on-device (macOS, Apple Silicon).

## What it does

Two collections from one shared stack:

- **Memory** — searchable archive of your Claude Code sessions, project files, exported claude.ai chats
- **Knowledge** — classic RAG over docs, books, manuals (PDF-aware)

## Prerequisites

1. **LM Studio** running with an embedding model loaded at `http://localhost:1234/v1/embeddings`
2. Python 3.11+

## Setup

```bash
cd claude-rag
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Set the embedding model (must match what's loaded in LM Studio)
export CLAUDE_RAG_LM_STUDIO_EMBEDDING_MODEL=text-embedding-nomic-embed-text-v1.5
```

## Storage layout (runtime, not in repo)

```
~/.local/share/claude-rag/
├── lancedb/memory.lance/       # memory table
├── lancedb/knowledge.lance/    # knowledge table
├── knowledge-inbox/            # drop PDFs/docs here (nightly pickup)
├── exports-inbox/             # drop claude.ai exports here (nightly pickup)
├── locks/                      # writer serialization
└── logs/                       # ingestion + maintenance logs
```

## Usage

### Ingestion (manual, immediate)

```bash
# Project files → memory
python ingest_files.py

# Claude Code transcript → memory
python ingest_transcript.py <path-to-jsonl>

# Exported claude.ai chats → memory
python ingest_export.py

# PDF/book/manual → knowledge (PDF-aware)
python ingest_knowledge.py <path-to-pdf-or-folder>
```

### Inboxes (lazy, nightly pickup)

Drop files in the inbox directories — picked up by the launchd agent at 03:00.

### Claude Code MCP registration

```bash
claude mcp add claude-rag python /Users/stefano/Projects/claude-rag/src/claude_rag/mcp_server.py
```

### Nightly automation (launchd)

```bash
# Install the launchd agent
cp com.clauderag.nightly.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.clauderag.nightly.plist
```

## Guides

- [HOW-TO-USE.md](HOW-TO-USE.md) — practical guide: setup, search tools, adding content, troubleshooting
- [claude-rag-plan.md](claude-rag-plan.md) — full architecture and design plan
- [CHANGELOG.md](CHANGELOG.md) — release history

## Architecture

## Files

| File | Purpose |
|------|---------|
| `src/claude_rag/core.py` | Shared library: chunk → embed → upsert, dedup, search |
| `src/claude_rag/mcp_server.py` | FastMCP server with 5 search tools |
| `ingest_files.py` | Project files → memory |
| `ingest_transcript.py` | Claude Code sessions → memory |
| `ingest_export.py` | claude.ai exports → memory |
| `ingest_knowledge.py` | PDF-aware docs → knowledge |
| `claude_rag_sessionend_hook.sh` | SessionEnd hook (fire-and-forget) |
| `com.clauderag.nightly.plist` | launchd agent (nightly ingest + maintenance) |
