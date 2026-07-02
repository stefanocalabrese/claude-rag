#!/usr/bin/env python3
"""Ingest exported claude.ai chats from the exports inbox.

Scans ~/.local/share/claude-rag/exports-inbox/ for JSON files, parses them
into exchanges, chunks and embeds into the memory table.

Usage:
    python ingest_export.py                  # scan inbox (default)
    python ingest_export.py <path-to-json>   # single file
    python ingest_export.py --inbox <dir>    # custom inbox directory
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from claude_rag.core import (
    _acquire_lock, _release_lock, chunk_text, upsert_chunks, ChunkMetadata,
    DB_DIR, LOG_DIR, _content_hash, _ensure_dirs, _now_iso,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DEFAULT_INBOX = Path.home() / ".local" / "share" / "claude-rag" / "exports-inbox"


def _parse_claude_ai_export(data: dict) -> list[str]:
    """Parse a claude.ai export JSON into exchange strings.

    Expected format (varies by export version):
    {
        "conversation": [
            {"role": "user", "content": "..."},
            {"role": "assistant", "content": "..."}
        ]
    }
    or a flat list of messages.
    """
    exchanges: list[str] = []

    # Try common export formats
    messages = data.get("conversation") or data.get("messages") or data.get("history", [])

    if not isinstance(messages, list):
        logger.warning("Unexpected export format: no messages array found")
        return []

    # Group into user-assistant pairs
    current_parts: list[str] = []

    for msg in messages:
        if not isinstance(msg, dict):
            continue

        role = (msg.get("role") or msg.get("from", "")).lower()
        content = msg.get("content") or msg.get("text", "")

        if not content:
            continue

        role_label = "user" if role in ("user", "human") else "assistant"
        current_parts.append(f"[{role_label}]: {content}")

        # Flush on assistant messages (complete exchange)
        if role_label == "assistant" and len(current_parts) >= 2:
            exchange = "\n\n".join(current_parts)
            exchanges.append(exchange)
            current_parts = []

    # Flush remaining
    if current_parts:
        exchange = "\n\n".join(current_parts)
        exchanges.append(exchange)

    return exchanges


def ingest(file_path: str | Path, inbox_dir: Path | None = None) -> dict:
    """Ingest a single export file. Returns summary stats."""
    file_path = Path(file_path).expanduser()

    if not file_path.exists():
        logger.error("Export file not found: %s", file_path)
        return {"error": str(file_path), "ingested": 0}

    try:
        data = json.loads(file_path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.error("Cannot parse %s: %s", file_path, e)
        return {"error": str(file_path), "ingested": 0, "parse_error": str(e)}

    exchanges = _parse_claude_ai_export(data)
    if not exchanges:
        logger.info("No meaningful content in %s (skipped)", file_path)
        return {"file": str(file_path), "ingested": 0, "reason": "no exchanges found"}

    _ensure_dirs()
    lock = _acquire_lock("ingest_export")

    try:
        all_chunks: list[str] = []
        all_metadata: list[ChunkMetadata] = []

        for i, exchange in enumerate(exchanges):
            chunks = chunk_text(exchange)

            for j, chunk in enumerate(chunks):
                meta = ChunkMetadata(
                    id=f"export:{file_path.name}:{i}:{j}",
                    source_type="claude_ai_export",
                    project="",  # exports are cross-project
                    path=str(file_path),
                    title=file_path.stem,
                    mtime=file_path.stat().st_mtime,
                    content_hash=_content_hash(exchange),
                    timestamp=_now_iso(),
                )
                all_chunks.append(chunk)
                all_metadata.append(meta)

        if all_chunks:
            from claude_rag.core import upsert_chunks
            upsert_chunks("memory", all_chunks, all_metadata)

        logger.info("Ingested %d exchanges (%d chunks) from %s",
                     len(exchanges), len(all_chunks), file_path.name)

        return {
            "file": str(file_path),
            "exchanges": len(exchanges),
            "chunks": len(all_chunks),
        }

    finally:
        _release_lock("ingest_export")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest claude.ai chat exports")
    parser.add_argument("path", nargs="?", help="Path to export JSON (scans inbox if omitted)")
    parser.add_argument("--inbox", default=str(DEFAULT_INBOX), help="Inbox directory")
    args = parser.parse_args()

    inbox_dir = Path(args.inbox)

    if args.path:
        # Single file mode
        result = ingest(args.path, inbox_dir=inbox_dir)
        print(json.dumps(result, indent=2))
    else:
        # Scan inbox mode
        if not inbox_dir.is_dir():
            logger.info("Inbox directory does not exist, creating: %s", inbox_dir)
            inbox_dir.mkdir(parents=True, exist_ok=True)

        json_files = sorted(inbox_dir.glob("*.json"))
        if not json_files:
            logger.info("No export files in inbox (%s)", inbox_dir)
            print(f"No files found in {inbox_dir}")
            return

        results = []
        for json_file in json_files:
            result = ingest(json_file, inbox_dir=inbox_dir)
            results.append(result)

        ingested = sum(r.get("ingested", 0) for r in results if "error" not in r)
        print(f"Ingested {ingested} total chunks from {len(results)} files")


if __name__ == "__main__":
    main()
