#!/usr/bin/env python3
"""Ingest project files into the memory table.

Walks configured directories, skips unchanged files (mtime + content_hash),
chunks and embeds new/changed content.

Usage:
    python ingest_files.py [--dirs DIR1 [DIR2 ...]] [--extensions .py .ts .md]

Defaults:
    dirs:       ~/Projects, ~/Documents/notes (override with --dirs)
    extensions: .py .ts .js .go .rs .md .txt .yaml .yml .json .toml .sh
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path
from datetime import datetime, timezone

# Ensure we can import from src/
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from claude_rag.core import (
    _acquire_lock, _release_lock, chunk_text, upsert_chunks, ChunkMetadata,
    DB_DIR, LOG_DIR, _content_hash, _ensure_dirs, _now_iso,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Default project directories to index
DEFAULT_DIRS = [
    str(Path.home() / "Projects"),
    str(Path.home() / "Documents" / "notes"),
]

DEFAULT_EXTENSIONS = {".py", ".ts", ".js", ".go", ".rs", ".md", ".txt",
                      ".yaml", ".yml", ".json", ".toml", ".sh"}


def _file_stats(path: Path) -> tuple[float, str]:
    """Return (mtime, content_hash) for a file."""
    stat = path.stat()
    h = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return stat.st_mtime, h


def _load_index() -> dict[str, dict]:
    """Load the per-file index from disk.

    Index format: {abs_path: {"mtime": float, "hash": str}}
    """
    index_file = DB_DIR / ".file_index.json"
    if index_file.exists():
        try:
            return json.loads(index_file.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_index(index: dict[str, dict]) -> None:
    index_file = DB_DIR / ".file_index.json"
    index_file.write_text(json.dumps(index, indent=2))


def _walk_files(directories: list[str], extensions: set[str]) -> list[Path]:
    """Walk directories and return files matching the extension filter."""
    files: list[Path] = []
    for dir_str in directories:
        dir_path = Path(dir_str).expanduser()
        if not dir_path.is_dir():
            logger.warning("Directory not found, skipping: %s", dir_str)
            continue

        for ext in extensions:
            files.extend(dir_path.rglob(f"*{ext}"))

    # Deduplicate and filter out non-files
    seen = set()
    result = []
    for f in sorted(files):
        if f.is_file() and str(f) not in seen:
            # Skip common noise directories
            parts = [p.lower() for p in f.parts]
            if any(skip in parts for skip in ["node_modules", ".git", "__pycache__",
                                               "venv", ".venv", ".tox", "dist", "build"]):
                continue
            seen.add(str(f))
            result.append(f)

    return result


def ingest(directories: list[str] | None = None,
           extensions: set[str] | None = None) -> dict:
    """Main ingestion logic. Returns summary stats."""

    directories = directories or DEFAULT_DIRS
    extensions = extensions or DEFAULT_EXTENSIONS

    _ensure_dirs()
    lock = _acquire_lock("ingest_files")

    try:
        files = _walk_files(directories, extensions)
        index = _load_index()

        new_count = 0
        updated_count = 0
        skipped_count = 0

        # Collect chunks and metadata for batch upsert
        all_chunks: list[str] = []
        all_metadata: list[ChunkMetadata] = []

        for filepath in files:
            abs_path = str(filepath.resolve())
            mtime, content_hash = _file_stats(filepath)

            # Check if file is unchanged
            prev = index.get(abs_path, {})
            if prev.get("mtime") == mtime and prev.get("hash") == content_hash:
                skipped_count += 1
                continue

            # Read and chunk the file
            try:
                text = filepath.read_text(errors="replace")
            except OSError as e:
                logger.warning("Cannot read %s: %s", filepath, e)
                continue

            if len(text.strip()) < 50:  # skip tiny files
                skipped_count += 1
                continue

            chunks = chunk_text(text)
            is_update = abs_path in index

            for i, chunk in enumerate(chunks):
                meta = ChunkMetadata(
                    id=f"file:{abs_path}:{i}",
                    source_type="project_file",
                    project=Path(abs_path).parent.name,
                    path=abs_path,
                    mtime=mtime,
                    content_hash=content_hash,
                    timestamp=_now_iso(),
                )
                all_chunks.append(chunk)
                all_metadata.append(meta)

            # Update index
            index[abs_path] = {"mtime": mtime, "hash": content_hash}

            if is_update:
                updated_count += 1
            else:
                new_count += 1

        # Batch upsert all chunks at once (upsert_chunks creates the table if needed)
        if all_chunks:
            upsert_chunks("memory", all_chunks, all_metadata)

        _save_index(index)
        logger.info("Ingestion complete: %d new, %d updated, %d skipped",
                     new_count, updated_count, skipped_count)

        return {
            "new": new_count,
            "updated": updated_count,
            "skipped": skipped_count,
            "total_files": len(files),
        }

    finally:
        _release_lock("ingest_files")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest project files into memory table")
    parser.add_argument("--dirs", nargs="+", help="Directories to walk (overrides defaults)")
    parser.add_argument("--extensions", nargs="+", help="File extensions to index")
    args = parser.parse_args()

    stats = ingest(directories=args.dirs, extensions=set(args.extensions or DEFAULT_EXTENSIONS))
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
