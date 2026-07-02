#!/usr/bin/env python3
"""Ingest a Claude Code session transcript (JSONL) into the memory table.

Filters out noise (tool-call spam, retries, dead ends), chunks meaningful
exchanges. Skips trivial sessions below a content threshold.

Usage:
    python ingest_transcript.py <path-to-jsonl>          # single file
    python ingest_transcript.py <path-to-dir>             # all JSONL in dir
    python ingest_transcript.py                           # latest session auto-detected

The SessionEnd hook calls this with the just-finished session's JSONL path.
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

# Thresholds
MIN_CONTENT_CHARS = 200       # skip sessions with less meaningful content
MAX_TOOL_CALLS_PER_TURN = 15  # above this, likely noise (retry storms)


def _is_noise_turn(turn: dict) -> bool:
    """Heuristic: is this turn mostly noise (tool retries, system messages)?"""
    role = turn.get("role", "")

    # System/tool retries are noise
    if role == "tool" and turn.get("type") == "tool_result":
        content = str(turn.get("content", ""))
        if "error" in content.lower() and "retry" in content.lower():
            return True

    # Very short assistant turns with no substantive text
    if role == "assistant":
        content = str(turn.get("content", ""))
        # Strip tool calls from content for length check
        text_only = _extract_text_from_turn(turn)
        if len(text_only.strip()) < 10:
            return True

    return False


def _extract_text_from_turn(turn: dict) -> str:
    """Extract human-readable text from a turn, stripping tool calls."""
    content = turn.get("content", "")

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    parts.append(f"[Tool: {block.get('name', '')}]")
                elif block.get("type") == "tool_result":
                    result = block.get("content", "")
                    if isinstance(result, list):
                        parts.extend(str(r.get("text", "")) for r in result if isinstance(r, dict))
                    else:
                        parts.append(str(result)[:200])  # truncate long results
        return " ".join(parts)

    return str(content)[:500]


def _extract_exchanges(jsonl_path: Path) -> list[str]:
    """Parse a JSONL transcript and extract meaningful exchanges.

    Returns a list of exchange strings, each combining user + assistant turns.
    """
    lines = jsonl_path.read_text().splitlines()

    exchanges: list[str] = []
    current_exchange_parts: list[str] = []
    turn_count = 0
    tool_call_count = 0

    for line in lines:
        if not line.strip():
            continue

        try:
            turn = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Skip noise turns
        if _is_noise_turn(turn):
            tool_call_count += 1
            continue

        role = turn.get("role", "")
        text = _extract_text_from_turn(turn)

        if not text.strip():
            continue

        turn_count += 1

        # Group user + assistant turns into exchanges
        if role in ("user", "assistant"):
            current_exchange_parts.append(f"[{role}]: {text}")

        # Flush exchange when we have both user and assistant, or on large exchanges
        if len(current_exchange_parts) >= 2:
            exchange = "\n\n".join(current_exchange_parts)
            if len(exchange.strip()) >= MIN_CONTENT_CHARS:
                exchanges.append(exchange)
            current_exchange_parts = []

        # Reset tool call counter periodically to avoid false positives
        if turn_count % 20 == 0:
            tool_call_count = 0

    # Flush remaining
    if current_exchange_parts:
        exchange = "\n\n".join(current_exchange_parts)
        if len(exchange.strip()) >= MIN_CONTENT_CHARS:
            exchanges.append(exchange)

    return exchanges


def ingest(jsonl_path: str | Path, project: str = "") -> dict:
    """Ingest a single transcript file. Returns summary stats."""
    jsonl_path = Path(jsonl_path).expanduser()

    if not jsonl_path.exists():
        logger.error("Transcript file not found: %s", jsonl_path)
        return {"error": str(jsonl_path), "ingested": 0}

    exchanges = _extract_exchanges(jsonl_path)

    if not exchanges:
        logger.info("No meaningful content in %s (skipped)", jsonl_path)
        return {"file": str(jsonl_path), "ingested": 0, "reason": "no meaningful content"}

    _ensure_dirs()
    lock = _acquire_lock("ingest_transcript")

    try:
        all_chunks: list[str] = []
        all_metadata: list[ChunkMetadata] = []

        for i, exchange in enumerate(exchanges):
            chunks = chunk_text(exchange)

            for j, chunk in enumerate(chunks):
                meta = ChunkMetadata(
                    id=f"transcript:{jsonl_path.name}:{i}:{j}",
                    source_type="transcript",
                    project=project or jsonl_path.parent.name,
                    path=str(jsonl_path),
                    title=jsonl_path.stem,
                    mtime=jsonl_path.stat().st_mtime,
                    content_hash=_content_hash(exchange),
                    timestamp=_now_iso(),
                )
                all_chunks.append(chunk)
                all_metadata.append(meta)

        if all_chunks:
            from claude_rag.core import upsert_chunks
            upsert_chunks("memory", all_chunks, all_metadata)

        logger.info("Ingested %d exchanges (%d chunks) from %s",
                     len(exchanges), len(all_chunks), jsonl_path.name)

        return {
            "file": str(jsonl_path),
            "exchanges": len(exchanges),
            "chunks": len(all_chunks),
        }

    finally:
        _release_lock("ingest_transcript")


def find_latest_session() -> Path | None:
    """Auto-detect the most recent Claude Code session JSONL.

    Searches ~/.claude/projects/*/ for .jsonl files.
    """
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.is_dir():
        return None

    jsonl_files = sorted(projects_dir.rglob("*.jsonl"),
                         key=lambda p: p.stat().st_mtime, reverse=True)

    # Filter out empty or tiny files
    for f in jsonl_files:
        if f.stat().st_size > 1000:
            return f

    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Claude Code session transcript")
    parser.add_argument("path", nargs="?", help="Path to JSONL file or directory (auto-detect if omitted)")
    parser.add_argument("--project", default="", help="Project name for metadata")
    args = parser.parse_args()

    path = args.path

    if not path:
        # Auto-detect latest session
        detected = find_latest_session()
        if detected:
            path = str(detected)
            logger.info("Auto-detected latest session: %s", detected)
        else:
            logger.error("No transcript found. Provide a path or run from Claude Code.")
            sys.exit(1)

    # If directory, ingest all JSONL files
    path_obj = Path(path).expanduser()
    if path_obj.is_dir():
        results = []
        for jsonl in sorted(path_obj.rglob("*.jsonl")):
            result = ingest(jsonl, project=args.project)
            results.append(result)
        print(f"Ingested {len(results)} transcripts")
        return

    result = ingest(path, project=args.project)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
