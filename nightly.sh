#!/usr/bin/env bash
# Nightly maintenance for claude-rag (invoked by com.clauderag.nightly launchd agent).
#
# Steps: ensure LM Studio server + embedding model are up, ingest project files,
# claude.ai exports, and the knowledge-inbox, then compact + prune old versions.
#
# Steps are independent — a failure in one is logged but does not abort the rest
# (so a bad file never blocks nightly maintenance). Pass directories as arguments
# to override the default project dirs (used for quick manual test runs).

set -uo pipefail  # deliberately NOT -e: each step must run even if a prior fails

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

PY="${REPO_DIR}/.venv/bin/python"
LMS="${HOME}/.lmstudio/bin/lms"
LOG="${HOME}/.local/share/claude-rag/logs/nightly.log"
MODEL="${CLAUDE_RAG_LM_STUDIO_EMBEDDING_MODEL:-text-embedding-nomic-embed-text-v1.5}"
KNOWLEDGE_INBOX="${HOME}/.local/share/claude-rag/knowledge-inbox"

if [ "$#" -gt 0 ]; then
    PROJECT_DIRS=("$@")
else
    PROJECT_DIRS=("${HOME}/Projects" "${HOME}/Documents/notes")
fi

mkdir -p "$(dirname "$LOG")"
echo "=== nightly run $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG"

# 1. Ensure LM Studio server + embedding model are up.
#    Guarded load: only load if not already loaded (a blind `lms load` spawns a
#    DUPLICATE instance every run).
if [ -x "$LMS" ]; then
    "$LMS" server start >/dev/null 2>&1 || true
    "$LMS" ps 2>/dev/null | grep -q "$MODEL" || "$LMS" load "$MODEL" -y >/dev/null 2>&1 || true
fi

# 2. Ingest — each step independent.
"$PY" ingest_files.py --dirs "${PROJECT_DIRS[@]}" >> "$LOG" 2>&1 || echo "[nightly] ingest_files failed" >> "$LOG"
"$PY" ingest_export.py                            >> "$LOG" 2>&1 || echo "[nightly] ingest_export failed" >> "$LOG"
"$PY" ingest_knowledge.py "$KNOWLEDGE_INBOX"      >> "$LOG" 2>&1 || echo "[nightly] ingest_knowledge failed" >> "$LOG"

# 3. Maintenance: compact + prune versions older than 7 days.
"$PY" - >> "$LOG" 2>&1 <<'PYEOF' || echo "[nightly] optimize failed" >> "$LOG"
from claude_rag.core import optimize_table, table_exists
for t in ("memory", "knowledge"):
    if table_exists(t):
        optimize_table(t, cleanup_older_than_days=7)
PYEOF

echo "=== nightly done $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG"
