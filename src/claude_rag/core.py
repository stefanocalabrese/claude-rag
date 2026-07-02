"""Shared core: chunk → embed (LM Studio) → upsert (LanceDB), with dedup."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import lancedb
from openai import OpenAI
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (override via env vars)
# ---------------------------------------------------------------------------

DB_DIR = Path(os.environ.get("CLAUDE_RAG_DB", str(Path.home() / ".local" / "share" / "claude-rag" / "lancedb")))
LM_STUDIO_URL = os.environ.get("CLAUDE_RAG_LM_STUDIO_URL", "http://localhost:1234/v1")
# Default embedding model; override with CLAUDE_RAG_LM_STUDIO_EMBEDDING_MODEL.
# Must match the model id LM Studio exposes at /v1/models.
LM_STUDIO_MODEL = os.environ.get("CLAUDE_RAG_LM_STUDIO_EMBEDDING_MODEL", "text-embedding-nomic-embed-text-v1.5")
LOCK_DIR = Path(os.environ.get("CLAUDE_RAG_LOCKS", str(Path.home() / ".local" / "share" / "claude-rag" / "locks")))
LOG_DIR = Path(os.environ.get("CLAUDE_RAG_LOGS", str(Path.home() / ".local" / "share" / "claude-rag" / "logs")))

# Chunking defaults
DEFAULT_CHUNK_SIZE = 512       # tokens (approx chars for rough splitting)
DEFAULT_CHUNK_OVERLAP = 64     # overlap between chunks

# ---------------------------------------------------------------------------
# Metadata schema — every chunk carries these fields
# ---------------------------------------------------------------------------


class ChunkMetadata(BaseModel):
    id: str = ""
    source_type: str  # project_file | transcript | claude_ai_export | knowledge
    project: str = ""
    path: str = ""
    title: str = ""
    mtime: float = 0.0
    content_hash: str = ""
    timestamp: str = ""  # ISO-8601 UTC


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_dirs() -> None:
    """Create runtime directories if they don't exist."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _make_chunk_id(source_type: str, path: str, index: int) -> str:
    """Stable unique id from source path + chunk index."""
    raw = f"{source_type}:{path}:{index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _acquire_lock(name: str) -> Path:
    """Acquire a lock file. Returns the path (caller must manage lifecycle)."""
    _ensure_dirs()
    lock = LOCK_DIR / f"{name}.lock"
    lock.write_text(f"{os.getpid()}\n{time.time()}")
    return lock


def _release_lock(name: str) -> None:
    """Remove the lock file."""
    lock = LOCK_DIR / f"{name}.lock"
    if lock.exists():
        lock.unlink()


def _get_db_connection() -> lancedb.DBConnection:
    """Connect to the LanceDB instance."""
    _ensure_dirs()
    return lancedb.connect(str(DB_DIR))


def _get_table(db: lancedb.DBConnection, table_name: str) -> lancedb.table.Table | None:
    """Get an existing table, or return None if it doesn't exist."""
    if table_name in db.table_names():
        return db.open_table(table_name)
    return None


# ---------------------------------------------------------------------------
# Embedding via LM Studio (OpenAI-compatible endpoint)
# ---------------------------------------------------------------------------


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Send texts to LM Studio and return embeddings.

    Raises ConnectionError if the endpoint is unreachable.
    """
    if not LM_STUDIO_MODEL:
        raise ValueError("CLAUDE_RAG_LM_STUDIO_EMBEDDING_MODEL env var must be set")

    client = OpenAI(
        base_url=LM_STUDIO_URL,
        api_key="not-needed",  # LM Studio doesn't require auth
    )

    response = client.embeddings.create(
        model=LM_STUDIO_MODEL,
        input=texts,
    )

    embeddings = [item.embedding for item in response.data]
    logger.info("Embedded %d texts (model=%s)", len(embeddings), LM_STUDIO_MODEL)
    return embeddings


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def chunk_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE,
               overlap: int = DEFAULT_CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks by character count.

    For production use, consider a token-aware splitter (e.g. from
    langchain or tiktoken). This is a simple character-based fallback.
    """
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]

        # Try to break at a sentence or line boundary for cleaner chunks
        if end < len(text):
            for delimiter in ["\n\n", "\n", ". ", " ", ";"]:
                last_break = chunk.rfind(delimiter)
                if last_break > chunk_size * 0.5:  # at least halfway through
                    chunk = chunk[:last_break + len(delimiter)]
                    break

        chunks.append(chunk.strip())
        start = end - overlap

    return [c for c in chunks if c]


# ---------------------------------------------------------------------------
# Upsert to LanceDB
# ---------------------------------------------------------------------------

# Schema: vector (fixed-size list of float), text, and metadata columns.
VECTOR_DIM_KEY = "_vector_dim"  # stored in table config until we know the dim


def _infer_vector_dim(embeddings: list[list[float]]) -> int:
    return len(embeddings[0]) if embeddings else 384


def upsert_chunks(table_name: str, chunks: list[str],
                  metadata_list: list[ChunkMetadata]) -> None:
    """Upsert chunk texts and their pre-computed embeddings into a LanceDB table.

    The caller must compute embeddings before calling this function.
    Each metadata entry corresponds to the chunk at the same index.

    Idempotent: rows with matching `id` are updated, not duplicated.
    """
    if not chunks:
        return

    embeddings = embed_texts(chunks)
    dim = _infer_vector_dim(embeddings)

    db = _get_db_connection()

    # Build records for LanceDB
    records = []
    for text, meta, vec in zip(chunks, metadata_list, embeddings):
        record = {
            "vector": vec,
            "text": text,
            # Metadata columns
            "id": meta.id,
            "source_type": meta.source_type,
            "project": meta.project,
            "path": meta.path,
            "title": meta.title,
            "mtime": meta.mtime,
            "content_hash": meta.content_hash,
            "timestamp": meta.timestamp,
        }
        records.append(record)

    if table_name in db.table_names():
        table = db.open_table(table_name)
        # Delete existing rows with matching ids, then add all (true upsert)
        existing_ids = [r["id"] for r in records]
        if existing_ids:
            id_list = ", ".join(f"'{eid}'" for eid in existing_ids)
            table.delete(f"id IN ({id_list})")
        table.add(records)
    else:
        # Create new table with vector column
        db.create_table(table_name, data=records)

    logger.info("Upserted %d chunks into table '%s' (dim=%d)", len(chunks), table_name, dim)


# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------


def search_table(table_name: str, query: str, k: int = 10,
                 filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Semantic search over a LanceDB table.

    Embeds the query via LM Studio, runs a vector search (applying any
    equality filters), and re-ranks the candidates by cosine distance. Each
    returned row carries a ``vector_distance`` field (0.0 = identical,
    higher = less similar) plus the stored text and metadata columns.
    """
    db = _get_db_connection()

    if table_name not in db.table_names():
        logger.warning("Table '%s' does not exist", table_name)
        return []

    table = db.open_table(table_name)

    # Build an equality filter expression (SQL-style) for LanceDB.
    where = None
    if filters:
        conditions = []
        for key, value in filters.items():
            if isinstance(value, str):
                escaped = value.replace("'", "''")  # escape quotes for SQL
                conditions.append(f"{key} = '{escaped}'")
            elif isinstance(value, (int, float)):
                conditions.append(f"{key} = {value}")
            else:
                continue  # skip unsupported filter types
        where = " AND ".join(conditions) if conditions else None

    # Embed the query once; reuse the vector for the search and the re-rank.
    query_vec = embed_texts([query])[0]

    search = table.search(query_vec).limit(k * 3)
    if where:
        search = search.where(where)

    try:
        results = search.to_list()
    except Exception:
        logger.warning("Search failed for table '%s'", table_name, exc_info=True)
        return []

    if not results:
        return []

    # Re-rank by cosine distance and attach it so callers can surface a score.
    norm_q = sum(a * a for a in query_vec) ** 0.5
    scored: list[tuple[float, dict]] = []
    for row in results:
        vec_data = row.get("vector")
        if vec_data is None:
            continue

        vec = list(vec_data)  # numpy array / Arrow list -> plain list
        dot = sum(a * b for a, b in zip(query_vec, vec))
        norm_v = sum(b * b for b in vec) ** 0.5
        if norm_q == 0 or norm_v == 0:
            continue

        distance = 1.0 - (dot / (norm_q * norm_v))
        row["vector_distance"] = distance
        scored.append((distance, row))

    # Sort by distance (lower = more similar) and take top k.
    scored.sort(key=lambda x: x[0])
    return [row for _, row in scored[:k]]


def table_exists(table_name: str) -> bool:
    """Check if a LanceDB table exists."""
    db = _get_db_connection()
    return table_name in db.table_names()


def get_table_stats(table_name: str) -> dict[str, Any]:
    """Return basic stats about a table (row count, etc.)."""
    db = _get_db_connection()
    if table_name not in db.table_names():
        return {"exists": False}

    table = db.open_table(table_name)
    stats = {
        "exists": True,
        "row_count": table.count_rows(),
    }

    # Try to get version info for compaction status
    try:
        versions = table.versions()
        stats["version_count"] = len(versions)
    except Exception:
        pass

    return stats


def optimize_table(table_name: str, cleanup_older_than_days: int = 7) -> None:
    """Compact the table and prune versions older than ``cleanup_older_than_days``.

    LanceDB's ``optimize`` merges small fragments (reclaiming space from
    deleted/updated rows) and cleans up stale versions in a single pass.
    """
    db = _get_db_connection()
    if table_name not in db.table_names():
        return

    table = db.open_table(table_name)
    try:
        table.optimize(cleanup_older_than=timedelta(days=cleanup_older_than_days))
        logger.info("Optimized table '%s' (pruned versions >%d days)",
                    table_name, cleanup_older_than_days)
    except Exception as e:
        logger.warning("Optimization for '%s' failed: %s", table_name, e)
