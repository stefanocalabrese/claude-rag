# How to Use claude-rag

A practical guide to getting started and using the system day-to-day.

## Quick Start (one-time setup)

### 1. Install dependencies

```bash
cd claude-rag
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Set up LM Studio

1. Open LM Studio
2. Download an embedding model (e.g., `nomic-embed-text` or `text-embedding-nomic-embed-text-v1.5`)
3. Load the embedding model (not a chat model — you need the embeddings endpoint)
4. Make sure it loads on startup so hooks and nightly jobs don't hit a cold endpoint

### 3. Configure the embedding model name

```bash
export CLAUDE_RAG_LM_STUDIO_EMBEDDING_MODEL=text-embedding-nomic-embed-text-v1.5
```

Add this to your shell profile (`~/.zshrc`) so it persists.

### 4. Register the MCP server with Claude Code

```bash
claude mcp add claude-rag python /Users/stefano/Projects/claude-rag/src/claude_rag/mcp_server.py
```

This makes the search tools available to Claude in every session.

### 5. (Optional) Install nightly automation

```bash
cp com.clauderag.nightly.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.clauderag.nightly.plist
```

This runs `ingest_files.py` + `ingest_export.py` + LanceDB maintenance every night at 03:00.

## Using the Search Tools (from Claude Code)

Once the MCP server is registered, Claude can call these tools naturally:

| Tool | Use for... |
|------|-----------|
| `search_memory("how did I set up auth?")` | Your past Claude work across all projects |
| `search_knowledge("how does Stripe webhook signing work?")` | Docs, books, manuals you've ingested |
| `search_all("pagination patterns")` | Both collections at once |
| `search_project("claude-rag", "vector search")` | Memory scoped to one project |
| `search_recent("what was I working on", days=7)` | Recent activity only |

You don't need to invoke them manually — Claude will call them when relevant. But you can also ask directly: "search my memory for..." or "check the manuals for...".

## Adding Content

### Project files (automatic)

The nightly run indexes your configured project directories. To force an immediate index:

```bash
python ingest_files.py
```

By default it walks `~/Projects` and `~/Documents/notes`, indexing `.py .ts .js .go .rs .md .txt .yaml .yml .json .toml .sh` files. Customize with:

```bash
python ingest_files.py --dirs ~/code ~/notes --extensions .go .rs .md
```

### Claude Code sessions (automatic)

The SessionEnd hook captures transcripts after each session. No action needed. Trivial sessions (under 200 chars of meaningful content) are skipped.

To manually ingest a transcript:

```bash
python ingest_transcript.py ~/.claude/projects/abc-123/session.jsonl
```

Or auto-detect the latest session:

```bash
python ingest_transcript.py
```

### Claude.ai exports (lazy or immediate)

**Lazy** — drop exported JSONs in the inbox:

```bash
cp ~/Downloads/my-chat.json ~/.local/share/claude-rag/exports-inbox/
```

Picked up nightly, or run immediately:

```bash
python ingest_export.py
```

**Immediate** — single file:

```bash
python ingest_export.py ~/Downloads/my-chat.json
```

### Knowledge documents (manual)

**Single file:**

```bash
python ingest_knowledge.py ~/Downloads/api-reference.pdf
```

**Whole folder:**

```bash
python ingest_knowledge.py ~/Documents/manuals/
```

Supported: `.pdf` (via pymupdf4llm), `.md`, `.txt`, `.rst`. PDFs get structure-preserving text extraction with heading-aware chunking.

## Operational Cheatsheet

```bash
# Check table stats (row counts, versions)
claude mcp add claude-rag python /Users/stefano/Projects/claude-rag/src/claude_rag/mcp_server.py
# Then in Claude: "what's the size of my memory table?"

# Re-index everything right now
python ingest_files.py && python ingest_export.py

# Add a book/manual immediately
python ingest_knowledge.py ~/Downloads/some-book.pdf

# Check logs
tail -f ~/.local/share/claude-rag/logs/nightly.log
tail -f ~/.local/share/claude-rag/logs/hook.log

# Uninstall nightly automation
launchctl unload ~/Library/LaunchAgents/com.clauderag.nightly.plist

# Remove MCP server registration
claude mcp remove claude-rag
```

## Troubleshooting

### "No memory table found"

You haven't run any ingestion yet. Run `python ingest_files.py` or drop something in an inbox and wait for the nightly run.

### "Connection refused" on embeddings

LM Studio isn't running or the embedding model isn't loaded. Check:
1. LM Studio is open and running
2. An **embedding** model (not a chat model) is loaded
3. The endpoint is at `http://localhost:1234/v1/embeddings`
4. The env var `CLAUDE_RAG_LM_STUDIO_EMBEDDING_MODEL` matches the model name in LM Studio

### "pymupdf4llm not installed"

```bash
pip install pymupdf4llm
```

### Nightly job didn't run

Check if it's loaded: `launchctl list | grep clauderag`. If not, reload it. Check the log: `cat ~/.local/share/claude-rag/logs/nightly.log`.

### Hook didn't capture a session

Check the hook log: `cat ~/.local/share/claude-rag/logs/hook.log`. Sessions under 200 chars of meaningful content are skipped by design.

## Customization

### Change project directories

```bash
python ingest_files.py --dirs ~/code ~/work ~/personal
```

### Change embedding model

```bash
export CLAUDE_RAG_LM_STUDIO_EMBEDDING_MODEL=your-model-name-here
```

### Change database location

```bash
export CLAUDE_RAG_DB=/path/to/your/db
```

### Change LM Studio endpoint

```bash
export CLAUDE_RAG_LM_STUDIO_URL=http://localhost:1234/v1
```
