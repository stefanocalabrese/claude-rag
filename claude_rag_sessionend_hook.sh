#!/usr/bin/env bash
# SessionEnd hook for Claude Code — fires after each session ends.
#
# Claude Code delivers hook data as a JSON object on STDIN (not as $1), e.g.
#   {"session_id":"...","transcript_path":"/abs/path.jsonl","cwd":"...",
#    "hook_event_name":"SessionEnd","reason":"clear"}
# We read stdin once, pull out transcript_path, and spawn ingest_transcript.py
# as a detached background process so it never delays the shell.
#
# Install: register in ~/.claude/settings.json under hooks.SessionEnd.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGDIR="${HOME}/.local/share/claude-rag/logs"
INBOX="${LOGDIR}/sessions"
MIN_CONTENT=200

# Read the entire stdin payload ONCE (stdin can only be consumed once).
PAYLOAD="$(cat)"

# Extract transcript_path from the JSON (system python3 is fine — no deps needed).
SESSION_JSONL="$(printf '%s' "$PAYLOAD" \
    | /usr/bin/python3 -c 'import sys,json; print(json.load(sys.stdin).get("transcript_path",""))' \
    2>/dev/null || true)"

if [ -z "$SESSION_JSONL" ] || [ ! -f "$SESSION_JSONL" ]; then
    echo "[claude-rag] No valid transcript_path in hook payload, skipping" >&2
    exit 0
fi

# Quick content check — skip trivial sessions.
CONTENT_SIZE=$(wc -c < "$SESSION_JSONL" 2>/dev/null || echo "0")
if [ "$CONTENT_SIZE" -lt "$MIN_CONTENT" ]; then
    echo "[claude-rag] Session too small (${CONTENT_SIZE} bytes), skipping" >&2
    exit 0
fi

# Copy to inbox (archival) and run ingest detached.
mkdir -p "$INBOX" "$LOGDIR"
COPIED="${INBOX}/$(basename "$SESSION_JSONL")"
cp "$SESSION_JSONL" "$COPIED" 2>/dev/null || true

# Fire-and-forget: detach the ingest (use the project venv, not system python3).
PYTHON_BIN="${SCRIPT_DIR}/.venv/bin/python"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="python3"  # fallback if venv missing
nohup "$PYTHON_BIN" "${SCRIPT_DIR}/ingest_transcript.py" "$COPIED" \
    >> "${LOGDIR}/hook.log" 2>&1 &

echo "[claude-rag] Transcript ingest spawned (PID $!, $(basename "$SESSION_JSONL"))" >&2
exit 0
