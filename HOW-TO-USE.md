# How to Use claude-rag

A practical guide to getting started and using the system day-to-day.

The primary interface is the **`claude-rag` command**: an on-demand tool that
ensures LM Studio is running with the embedding model loaded, then ingests your
content and maintains the index. There is **no background scheduler** — you run
it when you want to refresh.

## Quick Start (one-time setup)

### 1. Install dependencies (uv)

```bash
cd /Users/stefano/Projects/claude-rag
uv venv --python 3.12
uv pip install -e ".[dev]"
```

### 2. Download the embedding model in LM Studio

1. Open LM Studio.
2. Download an **embedding** model — `text-embedding-nomic-embed-text-v1.5`
   (the default this project expects).

You do **not** need to keep LM Studio running or load the model manually — the
`claude-rag` command launches the app, starts the server, and loads the model on
demand. The model just has to be downloaded.

### 3. Install the `claude-rag` command on your PATH

```bash
ln -sf /Users/stefano/Projects/claude-rag/claude-rag /opt/homebrew/bin/claude-rag
```

Now `claude-rag` works from any directory. (Uses `/opt/homebrew/bin`, which is on
PATH and writable without sudo.)

### 4. Register the MCP server with Claude Code

Use the project venv's Python and the real server path (the server imports
`fastmcp`/`lancedb`, which only exist inside the venv):

```bash
claude mcp add claude-rag --scope user -- \
  /Users/stefano/Projects/claude-rag/.venv/bin/python \
  /Users/stefano/Projects/claude-rag/src/claude_rag/mcp_server.py
```

This makes the search tools available to Claude in every session (user scope =
all projects). Verify with `claude mcp list` (should show `✔ Connected`).

## The `claude-rag` command (primary interface)

```bash
claude-rag                 # sync: ensure LM Studio + model, ingest everything, optimize
claude-rag sync <dir>...   # sync specific dirs instead of the defaults
claude-rag search <query>  # semantic search across memory + knowledge, from the terminal
claude-rag status          # LM Studio + table status
claude-rag help            # usage
```

`sync` (the default) ingests your project dirs (default `~/Projects` and
`~/Documents/notes`, skipping hidden/noise dirs), the exports inbox, archived
session transcripts, and the knowledge inbox — then compacts and prunes old
versions. It's idempotent (content-hash dedup), so run it as often as you like.

If LM Studio is closed, any command that needs embeddings launches it, starts
the server, and loads the model first (~6 s cold start).

## Searching (from Claude Code)

Once the MCP server is registered, Claude can call these tools naturally:

| Tool | Use for... |
|------|-----------|
| `search_memory("how did I set up auth?")` | Your past Claude work across all projects |
| `search_knowledge("how does Stripe webhook signing work?")` | Docs, books, manuals you've ingested |
| `search_all("pagination patterns")` | Both collections at once |
| `search_project("claude-rag", "vector search")` | Memory scoped to one project |
| `search_recent("what was I working on", days=7)` | Recent activity only |

You don't need to invoke them manually — Claude calls them when relevant. You can
also ask directly ("search my memory for…", "check the manuals for…"), or from a
terminal use `claude-rag search "…"`.

## Adding Content

The easy path for everything: **`claude-rag`** (it sweeps all inboxes + project
dirs). The direct scripts below are for one-off immediate ingests and assume LM
Studio is already up (run `claude-rag status` first, or just use `claude-rag`).

### Project files

`claude-rag sync` walks your configured directories. To sync a specific set:

```bash
claude-rag sync ~/code ~/Documents/notes
```

Directly (LM Studio must be up): `.venv/bin/python ingest_files.py --dirs ~/code`
— indexes `.py .ts .js .go .rs .md .txt .yaml .yml .json .toml .sh`, skipping
hidden dirs (`.git`, `.venv`, `.remember`, `.claude`, …) and heavy dirs
(`node_modules`, `dist`, `build`, …).

### Claude Code sessions

The SessionEnd hook archives each finished transcript to
`~/.local/share/claude-rag/logs/sessions/`; the next `claude-rag sync` ingests
them. Trivial sessions (under 200 chars of meaningful content) are skipped.

To ingest a transcript immediately (LM Studio up):

```bash
.venv/bin/python ingest_transcript.py ~/.claude/projects/abc-123/session.jsonl
```

### Claude.ai exports

Drop exported JSONs in the inbox; they're ingested on the next `claude-rag sync`:

```bash
cp ~/Downloads/my-chat.json ~/.local/share/claude-rag/exports-inbox/
```

Immediate: `.venv/bin/python ingest_export.py [~/Downloads/my-chat.json]`

### Knowledge documents (PDFs, books, manuals)

Drop them in the knowledge inbox (ingested on the next `claude-rag sync`):

```bash
cp ~/Downloads/api-reference.pdf ~/.local/share/claude-rag/knowledge-inbox/
```

Immediate (single file or whole folder, LM Studio up):

```bash
.venv/bin/python ingest_knowledge.py ~/Downloads/api-reference.pdf
.venv/bin/python ingest_knowledge.py ~/Documents/manuals/
```

Supported: `.pdf` (via pymupdf4llm, structure-preserving + heading-aware
chunking), `.md`, `.txt`, `.rst`.

## Operational Cheatsheet

```bash
# Ensure LM Studio + model, ingest everything, optimize
claude-rag

# Search / status from the terminal
claude-rag search "how does dedup work"
claude-rag status

# Add a book/manual immediately (LM Studio must be up)
.venv/bin/python ingest_knowledge.py ~/Downloads/some-book.pdf

# Check logs
tail -f ~/.local/share/claude-rag/logs/claude-rag.log   # sync runs
tail -f ~/.local/share/claude-rag/logs/hook.log         # SessionEnd hook

# Remove MCP server registration
claude mcp remove claude-rag

# Uninstall the command
rm /opt/homebrew/bin/claude-rag
```

## Troubleshooting

### "No memory table found" / empty tables

Nothing ingested yet. Run `claude-rag` (or drop files in an inbox and run
`claude-rag sync`).

### LM Studio won't start / model won't load

`claude-rag` prints its progress. If it ends with "LM Studio not ready":
1. Confirm the model `text-embedding-nomic-embed-text-v1.5` is **downloaded** in
   LM Studio (`~/.lmstudio/bin/lms ls | grep nomic`).
2. Confirm the app can launch: `open -a "LM Studio"`.
3. Check the served id matches: `curl -s http://localhost:1234/v1/models`. If it
   differs, set `CLAUDE_RAG_LM_STUDIO_EMBEDDING_MODEL` (see Customization).

### Hook didn't capture a session

Check `~/.local/share/claude-rag/logs/hook.log`. Sessions under 200 chars of
meaningful content are skipped by design. The hook only **archives** the
transcript — run `claude-rag` to actually embed it. Verify the hook is
registered in `~/.claude/settings.json` under `hooks.SessionEnd`.

### "pymupdf4llm not installed"

```bash
uv pip install pymupdf4llm
```

## Customization (environment variables)

```bash
# Use a different embedding model id (must match LM Studio's served id)
export CLAUDE_RAG_LM_STUDIO_EMBEDDING_MODEL=your-model-id

# Change database location
export CLAUDE_RAG_DB=/path/to/your/db

# Change LM Studio endpoint
export CLAUDE_RAG_LM_STUDIO_URL=http://localhost:1234/v1
```

Add exports to `~/.zshrc` to persist them. The embedding model already defaults
to `text-embedding-nomic-embed-text-v1.5` in `core.py`, so the first var is only
needed if you use a different model.
