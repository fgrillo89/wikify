# MCP Server (LLM Tool Access)

## What it is
A Model Context Protocol server that exposes ScholarForge as tools for Claude Code or any MCP-compatible LLM client.

## Tools exposed

| Tool | Input | Output | Use case |
|------|-------|--------|----------|
| `search_papers` | query, top_k, max_tokens | papers + relevant chunks | Find literature on a topic |
| `get_paper` | title/author pattern | paper + all chunks | Get full details of a specific paper |
| `get_graph_metrics` | (none) | hub/bridge/frontier + PageRank ranking | Understand corpus structure |
| `list_papers` | limit (optional) | paper metadata list | Browse the corpus |
| `list_topics` | (none) | topics + paper counts | See topic coverage |
| `deep_read` | title/author pattern | full text + all chunks | Read an entire paper |

## Transport
stdio (standard for Claude Code MCP servers)

## Registration
Add to Claude Code MCP settings:
```json
{
  "mcpServers": {
    "scholarforge": {
      "command": "scholarforge",
      "args": ["mcp"]
    }
  }
}
```

## Error handling
All tools catch exceptions and return `{"error": "..."}` instead of crashing the server.

## Future: the app
In the future, ScholarForge will be a user-facing app where users either bring their own LLM API key or pay for API calls. The MCP server is the foundation — it defines the tool contract that any LLM client can use to interact with the knowledge base.

## Where the code lives
- `mcp_server.py` — FastMCP server with 6 tools
- `cli.py` — `scholarforge mcp` command
