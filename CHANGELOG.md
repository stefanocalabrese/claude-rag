# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial scaffold of the entire codebase
- `src/claude_rag/core.py` — shared library: chunking, embedding via LM Studio, LanceDB upsert with dedup, semantic search
- `src/claude_rag/mcp_server.py` — FastMCP server with 5 tools: `search_memory`, `search_knowledge`, `search_all`, `search_project`, `search_recent`
- `ingest_files.py` — project file ingestion with mtime + content_hash dedup, configurable dirs and extensions
- `ingest_transcript.py` — Claude Code session JSONL ingestion with noise filtering, exchange extraction, auto-detect of latest session
- `ingest_export.py` — claude.ai chat export ingestion from inbox or single file, supports multiple export formats
- `ingest_knowledge.py` — PDF-aware ingestion using pymupdf4llm, heading-aware chunking for markdown/text
- `claude_rag_sessionend_hook.sh` — SessionEnd hook script (fire-and-forget, detached subprocess)
- `com.clauderag.nightly.plist` — launchd agent for nightly ingest + LanceDB maintenance (03:00)
- `pyproject.toml` — project config with dependencies and ruff settings
- `.gitignore` — ignores venv, pycache, OS files
