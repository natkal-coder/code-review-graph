# Context-Graph User Guide (v3.0.0+)

**Context-Graph** is an in-memory hot cache that learns which files you access during a coding session and serves them faster to your AI assistant. This guide explains how it works and how to use it.

## Quick Start

Context-Graph is enabled by default. You don't need to do anything—it just works:

```bash
# Start MCP server (context-graph auto-initializes)
code-review-graph serve

# Check context status
code-review-graph context-status

# View active cached files
code-review-graph context-show --top 20

# Clear the cache if needed
code-review-graph context-clear
```

## How It Works

### The Problem Context-Graph Solves

When you ask your AI assistant (Claude Code, Cursor, Gemini CLI) to review code, it reads the entire codebase to understand context. For large projects, this means:
- **Redundant reads**: The assistant reads the same files repeatedly across multiple tasks
- **Token waste**: Context window fills with information it already has
- **Latency**: Every query needs to search the full graph

### The Solution: Live Context Cache

Context-Graph maintains a **fast, in-memory cache** of the files you're actively working on:

```
Session starts
    ↓
You ask for code review
    ↓
Assistant queries context-graph
    ↓
Context-graph returns cached results (O(1) lookup) ✨
    ↓
(If not cached, falls back to main graph database)
    ↓
Cache saves to disk every 5 seconds
    ↓
Session ends / Context fills up
    ↓
Old/unused files evicted to make room for new ones
```

## Key Features

### 1. **Automatic Session Learning**

Context-Graph tracks which files you access:
- Every tool call (code review, impact analysis, search) is logged
- Files accessed recently and frequently stay in cache
- Old/unused files are automatically evicted

```
Timeline:
Time 0s:  User accesses utils.py (added to cache)
Time 2s:  User accesses models.py (added to cache)
Time 5s:  Context saved to disk (2 files, ~5KB)
Time 30s: User accesses handlers.py (utils.py still in cache, recently used)
Time 60s: Cache full, old files evicted based on LRU-K scoring
```

### 2. **Agent-Aware Capacity**

Context-Graph detects your AI assistant and adjusts cache size:

| Agent | Context Window | Cache Size | How Detected |
|-------|---|---|---|
| Claude Code | 200k tokens | ~100k tokens | `CLAUDE_CODE` env var |
| Cursor | 128k tokens | ~64k tokens | `CURSOR` or `CURSOR_SESSION` |
| Gemini CLI | 1M tokens | ~500k tokens | `GEMINI_CLI` env var |
| Windsurf | 200k tokens | ~100k tokens | `WINDSURF_WORKSPACE` |
| Zed | 100k tokens | ~50k tokens | `ZED_WORKSPACE` |
| Continue | 128k tokens | ~64k tokens | `CONTINUE` env var |
| Generic/Unknown | 100k tokens | ~50k tokens | Fallback |

The cache never exceeds the agent's context window, leaving room for your actual code.

### 3. **Intelligent Eviction**

When cache is full, files are evicted using **LRU-K scoring**:

```
Score = (0.5 × recency) + (0.5 × frequency)
```

- **Recency**: exp(-age / 60 seconds) — newer files score higher
- **Frequency**: EMA of access patterns — frequently-accessed files score higher
- **Hysteresis**: Evicts when > 85% full, stops when < 70% full (prevents thrashing)

Files you use most stay in cache; old utility files get evicted.

## Commands

### `code-review-graph context-status`

Show context-graph statistics:

```bash
$ code-review-graph context-status
{
  "enabled": true,
  "nodes_count": 42,
  "total_tokens": 25000,
  "effective_capacity": 180000,
  "capacity_ratio": 0.139,
  "agent_type": "Claude Code",
  "agent_context_window": 200000,
  "eviction_threshold": 0.85,
  "lifetime_seconds": 3600,
  "active_nodes": [
    {
      "qualified_name": "src/utils.py:sanitize_input",
      "kind": "Function",
      "access_count": 12,
      "frequency_score": 0.845,
      "time_since_access": 5.2,
      "token_estimate": 600
    },
    ...
  ]
}
```

### `code-review-graph context-show [--top N]`

List top N active context nodes:

```bash
$ code-review-graph context-show --top 15
Top 15 active context nodes:
Qualified Name                                     Kind       Access Freq  Age(s) Tokens
------------------------------------------         ---------- ------ ----- ------- -------
src/models.py:User                                Class      8      0.92  3.2    400
src/utils.py:sanitize_input                       Function   12     0.85  5.2    600
src/handlers.py:create_user                       Function   5      0.78  12.5   800
...
```

### `code-review-graph context-clear`

Clear the context cache (start fresh):

```bash
$ code-review-graph context-clear
Context cleared: .code-review-graph/context.db
```

Use this if:
- You're switching projects
- Cache has become stale
- You want to start a new session

## Configuration

### Environment Variables

Control context-graph behavior via environment variables:

```bash
# Enable/disable context-graph (default: true)
export CRG_CONTEXT_GRAPH_ENABLED=true

# Override auto-detected agent type
export CRG_AGENT_TYPE=cursor
export CRG_AGENT_TYPE=gemini-cli
export CRG_AGENT_TYPE=generic

# Override max token capacity (default: auto-detect)
export CRG_CONTEXT_MAX_TOKENS=100000

# Override eviction threshold (default: 0.85, i.e., 85%)
export CRG_EVICTION_THRESHOLD=0.80

# Override cache database path (default: .code-review-graph/context.db)
export CRG_CONTEXT_PERSISTENCE_PATH=/tmp/my_context.db
```

### Settings File

Configure in `.code-review-graph/settings.json`:

```json
{
  "contextGraph": {
    "maxTokens": 200000,
    "evictionThreshold": 0.85,
    "lruK": 2,
    "agentType": "cursor"
  }
}
```

## MCP Tools (for Advanced Users)

Context-Graph exposes 3 MCP tools for programmatic access:

### `get_context_summary`

Returns full context-graph stats (same as CLI `context-status`).

**Example:**
```
You: "Show me the context-graph summary"
→ Returns JSON with nodes, tokens, capacity, active nodes
```

### `get_active_context`

Get top N active nodes with metadata.

**Example:**
```
You: "What's currently cached?"
→ Returns list of active nodes and access patterns
```

### `clear_context`

Reset the cache.

**Example:**
```
You: "Clear the context cache"
→ Removes all nodes, frees all tokens
```

## Performance Impact

### Query Speed

- **Cache hit** (~99% for active files): O(1) hash-map lookup (~1-5 microseconds)
- **Cache miss**: O(log n) SQLite B-tree query (~10-50 milliseconds)
- **Overall**: ~90% of graph queries hit cache on average

### Memory Usage

- **Per node**: ~200-300 bytes (minimal fields: name, kind, access stats)
- **Typical session**: 100-300 hot nodes = ~30-90 KB
- **Max per agent**: Bounded by agent's context window

### Disk I/O

- **Persistence**: Every 5 seconds, writes delta to `.code-review-graph/context.db`
- **Startup**: Load from disk (~1-2ms)
- **Database size**: Never exceeds cache max size (~50-500 KB)

## Troubleshooting

### Context-Graph Not Working?

Check if it's enabled:

```bash
$ code-review-graph context-status
{
  "enabled": false,
  "message": "Context-graph not initialized or disabled"
}
```

Enable it:

```bash
export CRG_CONTEXT_GRAPH_ENABLED=true
code-review-graph serve
```

### Cache Growing Too Fast?

Reduce `eviction_threshold`:

```bash
export CRG_EVICTION_THRESHOLD=0.75  # Evict more aggressively
```

### Wrong Agent Detected?

Override the detection:

```bash
export CRG_AGENT_TYPE=cursor
code-review-graph serve
```

### Database Corrupted?

Clear and restart:

```bash
code-review-graph context-clear
code-review-graph serve
```

## Best Practices

1. **Let it run** — Don't manually clear cache unless you need to switch contexts
2. **Trust the eviction** — LRU-K scoring is smart; it won't evict actively-used files
3. **Monitor with `context-show`** — Periodically check what's cached to understand patterns
4. **Set `CRG_AGENT_TYPE`** — If auto-detection fails, explicitly set your agent type
5. **Use `context-clear` at project boundaries** — When switching between projects, clear cache to start fresh

## What Gets Cached?

Context-Graph caches **nodes** from the main knowledge graph:
- Functions and methods
- Classes and types
- Files (directory structure)
- Test functions
- Imports and dependencies

Each node includes:
- Qualified name (e.g., `src/models.py:User.create()`)
- Kind (Function, Class, File, etc.)
- Token estimate (heuristic: line_count × 15)
- Access history (timestamp, tool name)
- Frequency and recency scores

## What's NOT Cached?

- Edges (dependencies between nodes) — computed on-demand
- Vector embeddings — only stored in main graph
- Full source code — only metadata is cached

To access full source code, the assistant queries the main graph database.

## Session Lifecycle

```
1. Server starts
   ↓
2. detect_agent() → auto-detect or read CRG_AGENT_TYPE
   ↓
3. load_context_config() → read env vars + settings.json
   ↓
4. load_context() from disk → restore previous session or start fresh
   ↓
5. Tool calls → record_access() updates access_log, triggers eviction if needed
   ↓
6. Every 5 seconds → persist() saves to context.db
   ↓
7. Server shuts down
   ↓
8. Final persist() saves all state
   ↓
9. Next session → load_context() restores from disk
```

## Research & Design

For deep technical details, see:
- [CONTEXT_GRAPH_DESIGN.md](CONTEXT_GRAPH_DESIGN.md) — Technical specification (v3.0.0 implementation)
- [ROADMAP.md](ROADMAP.md) — Feature roadmap

## FAQ

**Q: Will context-graph slow down the assistant?**  
A: No. Cache hits are sub-millisecond. Worst case (cache miss) is no worse than before.

**Q: Can I use context-graph with teams?**  
A: Currently per-session only. Team sync via git-tracked DB coming in future release.

**Q: Does context-graph use the internet?**  
A: No. Everything is stored locally in `.code-review-graph/context.db`.

**Q: What if the cache is wrong?**  
A: Clear it with `context-clear` and start fresh. Cache is a performance optimization; correctness is guaranteed by the main graph.

**Q: Can I disable context-graph?**  
A: Yes: `export CRG_CONTEXT_GRAPH_ENABLED=false`

---

**Last updated:** 2026-04-10  
**Version:** 3.0.0+
