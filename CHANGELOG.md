# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Local-first RAG + memory for Claude Code, operated on demand via the `claude-rag`
command (no background scheduler). Wired up end-to-end and verified against a
live LM Studio (`text-embedding-nomic-embed-text-v1.5`, 768-dim).

### Added
- `src/claude_rag/core.py` — shared library: chunking, embedding via LM Studio, LanceDB upsert with mtime + content_hash dedup, vector search with cosine re-ranking
- `src/claude_rag/mcp_server.py` — FastMCP server, 6 tools: `search_memory`, `search_knowledge`, `search_all`, `search_project`, `search_recent`, `table_stats`
- `claude-rag` — on-demand CLI: ensures LM Studio is running with the embedding model loaded (launches the app, starts the server, guarded model load to avoid duplicate instances), then `sync` (ingest + optimize) / `search` / `status`
- `ingest_files.py` — project file ingestion (configurable dirs/extensions; skips hidden and heavy/generated dirs)
- `ingest_transcript.py` — Claude Code session JSONL ingestion (noise filtering, exchange extraction)
- `ingest_export.py` — claude.ai chat export ingestion (inbox or single file)
- `ingest_knowledge.py` — PDF-aware ingestion (pymupdf4llm), heading-aware chunking, folder mode
- `claude_rag_sessionend_hook.sh` — SessionEnd hook: archives the transcript (reads the hook payload from stdin) for the next sync
- `pyproject.toml`, `.gitignore`

### Changed
- On-demand model: the `claude-rag` command replaces a scheduled nightly job — nothing runs in the background
- `ingest_files.py` skips any dot-prefixed path component (`.git`, `.venv`, `.remember`, `.claude`, …) so private/tooling state is never embedded
- Default embedding model `text-embedding-nomic-embed-text-v1.5` baked into `core.py` (env-overridable via `CLAUDE_RAG_LM_STUDIO_EMBEDDING_MODEL`)
- Environment managed with `uv` (Python 3.12 venv)

### Fixed
- Packaging: invalid setuptools build backend; declared the missing `fastmcp` dependency
- `search_table`: embed the query and search by vector (was passing a raw string LanceDB can't embed); actually apply the metadata filter (was built then ignored, silently breaking `search_project`); attach a real relevance score (was always 1.0)
- `optimize_table`, `list_tables()`, and the FastMCP constructor updated to current LanceDB/FastMCP APIs
- `search_recent` no longer filters out every chunk that has a project set
- Removed `create_table(..., data=[])` that crashed the first ingest in every ingest script
- `ingest_knowledge.py` folder mode no longer crashes computing totals over `Path` objects
- SessionEnd hook reads `transcript_path` from stdin JSON (Claude Code does not pass it as `$1`) and uses the project venv
- `claude-rag` cold-start unbound-variable crash (Unicode ellipsis abutting `$MODEL`)

### Removed
- Scheduled launchd nightly agent (`nightly.sh`, `com.clauderag.nightly.plist`)
