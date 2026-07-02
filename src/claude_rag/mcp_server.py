"""MCP server for claude-rag: search_memory, search_knowledge, search_all,
search_project, search_recent.

Launched by Claude Code via stdio transport.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Ensure the src directory is on the path so we can import claude_rag
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastmcp import FastMCP  # type: ignore[import-untyped]

from claude_rag.core import (
    search_table,
    table_exists,
    get_table_stats,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "claude-rag",
    instructions="Local RAG + memory search for Claude Code",
)

# ---------------------------------------------------------------------------
# Tool: search_memory
# ---------------------------------------------------------------------------


@mcp.tool()
def search_memory(query: str, k: int = 5) -> str:
    """Search your Claude Code work history (project files, transcripts, exports).

    Args:
        query: The search query.
        k: Number of results to return (default 5).
    """
    if not table_exists("memory"):
        return json.dumps({"error": "No memory table found. Run an ingest job first."}, indent=2)

    results = search_table("memory", query, k=k)
    return json.dumps(_format_results(results, source_label="memory"), indent=2)


# ---------------------------------------------------------------------------
# Tool: search_knowledge
# ---------------------------------------------------------------------------


@mcp.tool()
def search_knowledge(query: str, k: int = 5) -> str:
    """Search your knowledge base (docs, books, manuals).

    Args:
        query: The search query.
        k: Number of results to return (default 5).
    """
    if not table_exists("knowledge"):
        return json.dumps({"error": "No knowledge table found. Run ingest_knowledge.py first."}, indent=2)

    results = search_table("knowledge", query, k=k)
    return json.dumps(_format_results(results, source_label="knowledge"), indent=2)


# ---------------------------------------------------------------------------
# Tool: search_all
# ---------------------------------------------------------------------------


@mcp.tool()
def search_all(query: str, k: int = 10) -> str:
    """Search both memory and knowledge tables, results labeled by source.

    Args:
        query: The search query.
        k: Total number of results to return (default 10, split between tables).
    """
    per_table = max(1, k // 2)
    output: list[dict] = []

    for table_name, label in [("memory", "memory"), ("knowledge", "knowledge")]:
        if not table_exists(table_name):
            continue
        results = search_table(table_name, query, k=per_table)
        for r in results:
            r["source"] = label
        output.extend(results)

    # Merge both tables' hits and sort by distance (lower = more similar).
    output.sort(key=lambda x: x.get("vector_distance", 1.0))
    return json.dumps(_format_results(output[:k], source_label=None), indent=2)


# ---------------------------------------------------------------------------
# Tool: search_project
# ---------------------------------------------------------------------------


@mcp.tool()
def search_project(project: str, query: str, k: int = 5) -> str:
    """Search memory scoped to a specific project.

    Args:
        project: Project name (matches the 'project' metadata field).
        query: The search query.
        k: Number of results to return (default 5).
    """
    if not table_exists("memory"):
        return json.dumps({"error": "No memory table found. Run an ingest job first."}, indent=2)

    results = search_table("memory", query, k=k, filters={"project": project})
    return json.dumps(_format_results(results, source_label="memory"), indent=2)


# ---------------------------------------------------------------------------
# Tool: search_recent
# ---------------------------------------------------------------------------


@mcp.tool()
def search_recent(query: str, days: int = 7, k: int = 5) -> str:
    """Search memory filtered to recent ingestions.

    Useful for "what was I working on last week".

    Args:
        query: The search query.
        days: Only look at chunks ingested in the last N days (default 7).
        k: Number of results to return (default 5).
    """
    if not table_exists("memory"):
        return json.dumps({"error": "No memory table found. Run an ingest job first."}, indent=2)

    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Fetch a wider candidate set (no project filter), then keep only recent chunks.
    results = search_table("memory", query, k=max(k * 5, 25))
    recent = [r for r in results if r.get("timestamp", "") >= cutoff]

    return json.dumps(_format_results(recent[:k], source_label="memory"), indent=2)


# ---------------------------------------------------------------------------
# Tool: table_stats (operational)
# ---------------------------------------------------------------------------


@mcp.tool()
def table_stats() -> str:
    """Return stats for both tables (row counts, versions)."""
    return json.dumps({
        "memory": get_table_stats("memory"),
        "knowledge": get_table_stats("knowledge"),
    }, indent=2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_results(results: list[dict], source_label: str | None) -> list[dict]:
    """Format search results for display.

    Returns a clean list with text, source metadata, and relevance score.
    """
    formatted = []
    for r in results:
        entry = {
            "text": r.get("text", ""),
            "score": round(1.0 - r.get("vector_distance", 0), 4),
            "source_type": r.get("source_type", ""),
            "path": r.get("path", ""),
            "project": r.get("project", ""),
            "title": r.get("title", ""),
            "timestamp": r.get("timestamp", ""),
        }
        if source_label:
            entry["source"] = source_label
        elif r.get("source"):
            entry["source"] = r["source"]  # preserve per-row label from search_all
        formatted.append(entry)
    return formatted


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    mcp.run()
