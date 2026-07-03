# claude-rag

Local-first RAG + memory system for Claude Code. Runs 100% on-device (macOS,
Apple Silicon). **No background daemon** — you drive it with the `claude-rag`
command, which starts LM Studio and loads the embedding model on demand.

## What it does

Two collections from one shared stack:

- **Memory** — searchable archive of your Claude Code sessions, project files, exported claude.ai chats
- **Knowledge** — classic RAG over docs, books, manuals (PDF-aware)

## Prerequisites

1. **LM Studio** with an embedding model **downloaded** (`text-embedding-nomic-embed-text-v1.5`). It need not be running — `claude-rag` launches it, starts the server, and loads the model on demand.
2. Python 3.11+ and [`uv`](https://github.com/astral-sh/uv)

## Setup

```bash
cd /Users/stefano/Projects/claude-rag
uv venv --python 3.12
uv pip install -e ".[dev]"

# Install the command on your PATH
ln -sf "$PWD/claude-rag" /opt/homebrew/bin/claude-rag

# Register the MCP server with Claude Code (venv python + real server path)
claude mcp add claude-rag --scope user -- \
  "$PWD/.venv/bin/python" "$PWD/src/claude_rag/mcp_server.py"
```

The embedding model defaults to `text-embedding-nomic-embed-text-v1.5` in
`core.py`; override with `CLAUDE_RAG_LM_STUDIO_EMBEDDING_MODEL` if you use a
different one.

## Usage

```bash
claude-rag                 # ensure LM Studio + model, ingest everything, optimize
claude-rag sync <dir>...   # sync specific dirs
claude-rag search <query>  # semantic search from the terminal
claude-rag status          # LM Studio + table status
```

Inside Claude Code, the MCP tools (`search_memory`, `search_knowledge`,
`search_all`, `search_project`, `search_recent`) are available in every session.

Direct ingest scripts (need the venv + LM Studio already up):

```bash
.venv/bin/python ingest_files.py [--dirs <dir>...]
.venv/bin/python ingest_knowledge.py <path-to-pdf-or-folder>
```

See **[HOW-TO-USE.md](HOW-TO-USE.md)** for the full guide.

## Storage layout (runtime, not in repo)

```
~/.local/share/claude-rag/
├── lancedb/memory.lance/       # memory table
├── lancedb/knowledge.lance/    # knowledge table
├── knowledge-inbox/            # drop PDFs/docs here (ingested on next `claude-rag sync`)
├── exports-inbox/              # drop claude.ai exports here
├── logs/sessions/              # transcripts archived by the SessionEnd hook
├── locks/                      # writer serialization
└── logs/                       # ingestion + sync logs
```

## Architecture

Claude Code spawns the MCP server over stdio. Search tools embed the query via LM
Studio and run vector search over LanceDB. Ingestion (files, transcripts,
exports, knowledge) shares one core — chunk → embed → upsert with mtime +
content-hash dedup. The `claude-rag` command orchestrates LM Studio startup +
ingest + maintenance on demand; the SessionEnd hook archives transcripts to the
sessions inbox for the next sync. Two tables (`memory`, `knowledge`) live in one
LanceDB folder with independent compaction.

## Guides

- [HOW-TO-USE.md](HOW-TO-USE.md) — practical guide: setup, the `claude-rag` command, search tools, adding content, troubleshooting
- [claude-rag-plan.md](claude-rag-plan.md) — full architecture and design plan
- [CHANGELOG.md](CHANGELOG.md) — release history

## Files

| File | Purpose |
|------|---------|
| `src/claude_rag/core.py` | Shared library: chunk → embed → upsert, dedup, search |
| `src/claude_rag/mcp_server.py` | FastMCP server (6 tools: 5 search + `table_stats`) |
| `claude-rag` | On-demand CLI: ensure LM Studio + model, then sync/search/status |
| `ingest_files.py` | Project files → memory (skips hidden/noise dirs) |
| `ingest_transcript.py` | Claude Code sessions → memory |
| `ingest_export.py` | claude.ai exports → memory |
| `ingest_knowledge.py` | PDF-aware docs → knowledge |
| `claude_rag_sessionend_hook.sh` | SessionEnd hook (archives transcripts) |
