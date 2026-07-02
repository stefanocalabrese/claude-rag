#!/usr/bin/env python3
"""Ingest knowledge documents (PDFs, markdown, text) into the knowledge table.

PDF-aware: extracts text with pymupdf4llm preserving structure (headings,
paragraphs). Also handles markdown and plain text.

Usage:
    python ingest_knowledge.py <path-to-pdf>        # single file
    python ingest_knowledge.py <path-to-folder>     # all supported files in folder

Supported: .pdf, .md, .txt, .rst
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
    DB_DIR, LOG_DIR, _content_hash, _ensure_dirs, _now_iso, DEFAULT_CHUNK_OVERLAP,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SUPPORTED_EXTENSIONS = {".pdf", ".md", ".txt", ".rst"}
KNOWLEDGE_CHUNK_SIZE = 1024  # larger chunks for docs (heading-aware chunking)


def _extract_text_pdf(filepath: Path) -> str:
    """Extract text from PDF using pymupdf4llm (structure-preserving)."""
    try:
        import pymupdf4llm  # type: ignore[import-not-found]
    except ImportError:
        logger.error("pymupdf4llm not installed. Run: pip install pymupdf4llm")
        raise

    try:
        text = pymupdf4llm.to_markdown(str(filepath))
        return text
    except Exception as e:
        logger.error("Failed to extract PDF text from %s: %s", filepath, e)
        raise


def _extract_text_markdown(filepath: Path) -> str:
    """Read markdown file."""
    return filepath.read_text(errors="replace")


def _extract_text_plain(filepath: Path) -> str:
    """Read plain text file."""
    return filepath.read_text(errors="replace")


def _extract_heading_aware_chunks(text: str, chunk_size: int = KNOWLEDGE_CHUNK_SIZE) -> list[str]:
    """Split text into chunks that respect heading boundaries.

    For PDFs converted to markdown, headings (## , ###) are natural chunk
    boundaries. This tries to break at heading levels rather than mid-section.
    """
    lines = text.split("\n")

    # Find heading positions (lines starting with #)
    heading_indices = [i for i, line in enumerate(lines) if line.strip().startswith("#")]

    if not heading_indices:
        # No headings — fall back to simple chunking
        return chunk_text(text, chunk_size=chunk_size)

    # Group lines by heading section
    sections: list[list[str]] = []
    current_section: list[str] = []

    for i, line in enumerate(lines):
        if i in heading_indices and current_section:
            sections.append(current_section)
            current_section = [line]
        else:
            current_section.append(line)

    if current_section:
        sections.append(current_section)

    # Chunk each section individually (preserves heading context)
    all_chunks: list[str] = []
    for section_lines in sections:
        section_text = "\n".join(section_lines)

        # If section fits in one chunk, keep it whole
        if len(section_text) <= chunk_size:
            all_chunks.append(section_text.strip())
            continue

        # Otherwise, split by sub-headings within the section
        sub_headings = [i for i, line in enumerate(section_lines)
                        if line.strip().startswith("##") and not line.strip().startswith("#")]

        if len(sub_headings) <= 1:
            # No meaningful sub-headings, use simple chunking
            chunks = chunk_text(section_text, chunk_size=chunk_size, overlap=DEFAULT_CHUNK_OVERLAP)
            all_chunks.extend(chunks)
        else:
            # Split by sub-headings
            section_chunks = []
            for j, sub_h in enumerate(sub_headings):
                start = sub_headings[j] if j > 0 else 0
                end = sub_headings[j + 1] if j + 1 < len(sub_headings) else len(section_lines)
                sub_section = section_lines[start:end]
                sub_text = "\n".join(sub_section).strip()
                if sub_text:
                    section_chunks.append(sub_text)

            # Handle any remaining lines after last sub-heading
            if len(section_lines) > sub_headings[-1]:
                remaining = section_lines[sub_headings[-1]:]
                sub_text = "\n".join(remaining).strip()
                if sub_text:
                    section_chunks.append(sub_text)

            all_chunks.extend(c for c in section_chunks if len(c.strip()) > 10)

    return [c for c in all_chunks if c]


def _get_file_type(filepath: Path) -> str:
    """Determine file type from extension."""
    return filepath.suffix.lower()


def ingest(file_path: str | Path) -> dict:
    """Ingest a single knowledge document. Returns summary stats."""
    file_path = Path(file_path).expanduser()

    if not file_path.exists():
        logger.error("File not found: %s", file_path)
        return {"error": str(file_path), "ingested": 0}

    file_type = _get_file_type(file_path)
    if file_type not in SUPPORTED_EXTENSIONS:
        logger.warning("Unsupported file type %s for %s (supported: %s)",
                       file_type, file_path, SUPPORTED_EXTENSIONS)
        return {"error": f"Unsupported type: {file_type}", "ingested": 0}

    # Extract text
    if file_type == ".pdf":
        text = _extract_text_pdf(file_path)
    elif file_type in (".md", ".rst"):
        text = _extract_text_markdown(file_path)
    else:
        text = _extract_text_plain(file_path)

    if not text.strip():
        logger.info("Empty content in %s (skipped)", file_path)
        return {"file": str(file_path), "ingested": 0, "reason": "empty content"}

    # Heading-aware chunking
    chunks = _extract_heading_aware_chunks(text)

    if not chunks:
        return {"file": str(file_path), "ingested": 0, "reason": "no chunks produced"}

    _ensure_dirs()
    lock = _acquire_lock("ingest_knowledge")

    try:
        all_metadata: list[ChunkMetadata] = []

        for i, chunk in enumerate(chunks):
            meta = ChunkMetadata(
                id=f"knowledge:{file_path.name}:{i}",
                source_type="knowledge",
                project="",  # knowledge is cross-project
                path=str(file_path),
                title=file_path.stem,
                mtime=file_path.stat().st_mtime,
                content_hash=_content_hash(chunk),
                timestamp=_now_iso(),
            )
            all_metadata.append(meta)

        from claude_rag.core import upsert_chunks
        upsert_chunks("knowledge", chunks, all_metadata)

        logger.info("Ingested %d chunks from %s (%s)", len(chunks), file_path.name, file_type)

        return {
            "file": str(file_path),
            "type": file_type,
            "chunks": len(chunks),
        }

    finally:
        _release_lock("ingest_knowledge")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest knowledge documents (PDF-aware)")
    parser.add_argument("path", help="Path to PDF, markdown, or text file (or folder)")
    args = parser.parse_args()

    path_obj = Path(args.path).expanduser()

    if path_obj.is_dir():
        # Ingest all supported files in directory
        files: list[Path] = []
        for ext in SUPPORTED_EXTENSIONS:
            files.extend(path_obj.rglob(f"*{ext}"))

        if not files:
            logger.info("No supported files found in %s", path_obj)
            print(f"No supported files ({SUPPORTED_EXTENSIONS}) in {path_obj}")
            return

        files.sort()
        results = []
        for filepath in files:
            result = ingest(filepath)
            results.append(result)
            print(json.dumps(result, indent=2))

        total_chunks = sum(r.get("chunks", 0) for r in results if "error" not in r)
        print(f"\nTotal: {len(files)} files, {total_chunks} chunks")

    else:
        result = ingest(path_obj)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    import json  # needed for main() print statements
    main()
