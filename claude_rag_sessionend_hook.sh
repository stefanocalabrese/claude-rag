#!/usr/bin/env bash
# SessionEnd hook for Claude Code — fires after each session ends.
# Spawns ingest_transcript.py as a detached background process so it never
# delays the shell. Skips trivial sessions (< 200 chars of meaningful content).
#
# Install: add this to your Claude Code hooks configuration.
# The hook receives the session JSONL path as $1.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INBOX="${HOME}/.local/share/claude-rag/logs/sessions"
MIN_CONTENT=200

SESSION_JSONL="${1:-}"

if [ -z "$SESSION_JSONL" ]; then
    echo "[claude-rag] No session path provided, skipping" >&2
    exit 0
fi

if [ ! -f "$SESSION_JSONL" ]; then
    echo "[claude-rag] Session file not found: $SESSION_JSONL" >&2
    exit 0
fi

# Quick content check — skip trivial sessions
CONTENT_SIZE=$(wc -c < "$SESSION_JSONL" 2>/dev/null || echo "0")
if [ "$CONTENT_SIZE" -lt "$MIN_CONTENT" ]; then
    echo "[claude-rag] Session too small (${CONTENT_SIZE} bytes), skipping" >&2
    exit 0
fi

# Ensure inbox directory exists
mkdir -p "$INBOX"

# Copy to inbox (for archival) and run ingest detached
COPIED="${INBOX}/$(basename "$SESSION_JSONL")"
cp "$SESSION_JSONL" "$COPIED" 2>/dev/null || true

# Fire-and-forget: detach the ingest process (use the project venv, not system python3)
PYTHON_BIN="${SCRIPT_DIR}/.venv/bin/python"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="python3"  # fallback if venv missing
nohup "$PYTHON_BIN" "${SCRIPT_DIR}/ingest_transcript.py" "$COPIED" \
    >> "${HOME}/.local/share/claude-rag/logs/hook.log" 2>&1 &

echo "[claude-rag] Transcript ingest spawned (PID $!, session: $(basename "$SESSION_JSONL"))" >&2
