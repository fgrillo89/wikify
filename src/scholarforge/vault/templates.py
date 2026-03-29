"""Note templates for different vault note types."""

from __future__ import annotations

import re
from typing import Any

import yaml


def _strip_citation_brackets(text: str) -> str:
    """Remove inline citation markers like [[4,5]], [[10-12]], [4], [10,11].

    These look like wikilinks to Obsidian and create phantom numbered nodes.
    """
    # [[4]], [[4,5]], [[10–12]], [[4-6,8]]
    text = re.sub(r"\[\[\d[\d,\s\-–—]*\]\]", "", text)
    # [4], [4,5], [10–12] (single brackets with only numbers inside)
    text = re.sub(r"\[(\d[\d,\s\-–—]*)\]", r"(\1)", text)
    return text


def paper_note(
    title: str,
    authors: list[str],
    year: int | None,
    doi: str | None,
    summary: str | None,
    file_hash: str,
    source_path: str,
    topics: list[str] | None = None,
    cites: list[str] | None = None,
    similar_to: list[str] | None = None,
    cites_same: list[str] | None = None,
    figure_refs: list[tuple[str, str]] | None = None,
    note: str | None = None,
    chunks_count: int = 0,
    figures_count: int = 0,
    full_text: str | None = None,
) -> str:
    """Generate markdown for a paper note."""
    frontmatter: dict[str, Any] = {
        "title": title,
        "authors": list(authors) if authors else [],  # plain text, no wikilinks
        "year": year,
        "tags": ["source/paper"],
        "file_hash": file_hash,
        "source_path": source_path,
    }
    if doi:
        frontmatter["doi"] = doi
    if topics:
        frontmatter["topics"] = list(topics)  # plain text, no wikilinks
    if cites:
        frontmatter["cites"] = [f"[[papers/{c}]]" for c in cites]
    if similar_to:
        frontmatter["similar_to"] = [f"[[papers/{s}]]" for s in similar_to]
    if cites_same:
        frontmatter["cites_same"] = [f"[[papers/{c}]]" for c in cites_same]

    fm = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False)

    sections = [f"---\n{fm}---\n"]

    # Link to open the original file
    if source_path:
        # Convert to absolute file:/// URI for clickable link in Obsidian
        from pathlib import Path

        abs_path = Path(source_path).resolve()
        file_uri = abs_path.as_uri()
        sections.append(f"[Open original file]({file_uri})\n")

    if summary:
        clean_summary = _strip_citation_brackets(summary)
        sections.append(f"## Abstract\n\n{clean_summary}\n")

    if note:
        sections.append(f"## Summary\n\n{note}\n")

    if cites:
        links = "\n".join(f"- [[papers/{c}]] `#edge/citation`" for c in cites)
        sections.append(f"## Cites\n\n{links}\n")

    if figure_refs:
        lines = "\n".join(
            f"- **{key}**: {_strip_citation_brackets(caption)}" for key, caption in figure_refs
        )
        sections.append(f"## Figure References\n\n{lines}\n")

    if similar_to:
        links = "\n".join(f"- [[papers/{s}]] `#edge/similarity`" for s in similar_to)
        sections.append(f"## Similar Papers\n\n{links}\n")

    if cites_same:
        links = "\n".join(f"- [[papers/{c}]] `#edge/coupling`" for c in cites_same)
        sections.append(f"## Bibliographic Coupling\n\n{links}\n")

    sections.append(
        f"## Statistics\n\n- **Chunks**: {chunks_count}\n- **Figures**: {figures_count}\n"
    )

    if full_text:
        # Obsidian collapsible callout: collapsed by default, searchable
        # The "-" after the type makes it collapsed
        indented = "\n> ".join(full_text.split("\n"))
        sections.append(f"> [!quote]- Full Text\n> {indented}\n")

    return "\n".join(sections)


def author_note(name: str, papers: list[str]) -> str:
    """Generate markdown for an author note."""
    frontmatter = {
        "name": name,
        "tags": ["author"],
    }
    fm = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False)

    paper_links = "\n".join(f"- [[papers/{p}]]" for p in papers)
    return f"---\n{fm}---\n\n## Papers\n\n{paper_links}\n"


def topic_note(name: str, papers: list[str]) -> str:
    """Generate markdown for a topic note."""
    frontmatter = {
        "name": name,
        "tags": ["topic"],
    }
    fm = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False)

    paper_links = "\n".join(f"- [[papers/{p}]]" for p in papers)
    return f"---\n{fm}---\n\n## Related Papers\n\n{paper_links}\n"
