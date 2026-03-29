"""Test the ScholarForge MCP server by exercising all tools.

Starts the MCP server as a subprocess, connects as a client, and
calls each tool to verify the knowledge base is accessible.

Usage:
    uv run python scripts/test_mcp_agent.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


async def call_tool(session, name: str, args: dict | None = None) -> str:
    """Call an MCP tool and return the text result."""
    try:
        result = await asyncio.wait_for(
            session.call_tool(name, args or {}),
            timeout=30,
        )
        return result.content[0].text if result.content else "(empty)"
    except asyncio.TimeoutError:
        return "(TIMEOUT after 30s)"
    except Exception as e:
        return f"(ERROR: {e})"


async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server_params = StdioServerParameters(
        command="uv",
        args=["run", "scholarforge", "mcp"],
        cwd=str(Path(__file__).parent.parent),
    )

    print("Starting MCP server...")
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            print(f"Tools: {tool_names}\n")

            # Test each tool
            tests = [
                ("list_papers", {}, "papers"),
                ("list_topics", {}, "topics"),
                ("search_papers", {"query": "memristor", "max_results": 3}, "search"),
                ("get_graph_metrics", {}, "graph"),
                ("get_corpus_summary", {}, "summary"),
            ]

            for name, args, label in tests:
                print(f"--- {label} ({name}) ---")
                result = await call_tool(session, name, args)
                # Show first 300 chars
                preview = result[:300]
                print(preview)
                if len(result) > 300:
                    print(f"  ... ({len(result)} chars total)")
                print()

            # Deep read first paper
            print("--- deep_read (first paper) ---")
            papers_result = await call_tool(session, "list_papers", {})
            try:
                papers = json.loads(papers_result)
                if papers:
                    pid = papers[0]["id"]
                    deep = await call_tool(session, "deep_read", {"paper_id": pid})
                    print(f"Paper: {papers[0].get('title', '?')[:60]}")
                    print(f"Deep read: {len(deep)} chars")
                    print(deep[:200])
                else:
                    print("No papers in corpus")
            except json.JSONDecodeError:
                print(f"Could not parse papers list: {papers_result[:100]}")

            print("\n=== All tools tested ===")


if __name__ == "__main__":
    asyncio.run(main())
