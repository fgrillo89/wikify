"""Interactive chat with the literature corpus."""

from __future__ import annotations

import json

from rich.console import Console
from rich.markdown import Markdown

from scholarforge.llm.client import complete
from scholarforge.retrieve.context import retrieve_for_query

console = Console()


def chat_once(query: str, history: list[dict[str, str]] | None = None) -> str:
    """Answer a single question using the literature corpus.

    Retrieves relevant chunks, sends to LLM with context, returns answer.
    """
    context = retrieve_for_query(query, max_papers=10, max_tokens=8000)

    if not context.papers:
        return "No relevant papers found in the corpus."

    lit_text = context.as_text()

    # Build paper reference list for citations
    paper_refs = []
    for p in context.papers:
        authors = json.loads(p.authors) if p.authors else []
        first = authors[0].split()[-1] if authors else "Unknown"
        paper_refs.append(f"{first} {p.year}")

    system_msg = (
        "You are a research assistant with access to a corpus of academic papers. "
        "Answer the user's question based on the literature provided below. "
        "Cite papers using (Author, Year) format. "
        "If the literature doesn't contain enough information to answer, say so. "
        "Be precise and technical.\n\n"
        f"Available papers: {', '.join(paper_refs)}\n\n"
        f"--- Literature context ---\n{lit_text}"
    )

    messages: list[dict[str, str]] = [{"role": "system", "content": system_msg}]

    if history:
        messages.extend(history)

    messages.append({"role": "user", "content": query})

    return complete(messages=messages, temperature=0.3, max_tokens=2048, use_cache=False)


def chat_interactive() -> None:
    """Run an interactive chat loop."""
    console.print("[bold]ScholarForge — Chat with your literature[/bold]")
    console.print("Type your question, or 'quit' to exit.\n")

    history: list[dict[str, str]] = []

    while True:
        try:
            query = console.input("[bold blue]You:[/bold blue] ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not query or query.lower() in ("quit", "exit", "q"):
            break

        answer = chat_once(query, history=history)

        console.print()
        console.print(Markdown(answer))
        console.print()

        # Keep conversation history (last 6 turns)
        history.append({"role": "user", "content": query})
        history.append({"role": "assistant", "content": answer})
        if len(history) > 12:
            history = history[-12:]
