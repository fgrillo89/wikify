"""Note templates for different vault note types."""

from __future__ import annotations

from typing import Any

import yaml


def paper_note(
    title: str,
    authors: list[str],
    year: int | None,
    doi: str | None,
    abstract: str | None,
    file_hash: str,
    source_path: str,
    topics: list[str] | None = None,
    methods: list[str] | None = None,
    cites: list[str] | None = None,
    similar_to: list[str] | None = None,
    cites_same: list[str] | None = None,
    figure_refs: list[tuple[str, str]] | None = None,
    summary: str | None = None,
    chunks_count: int = 0,
    figures_count: int = 0,
) -> str:
    """Generate markdown for a paper note."""
    frontmatter: dict[str, Any] = {
        "title": title,
        "authors": [f"[[authors/{a}]]" for a in authors] if authors else [],
        "year": year,
        "tags": ["source/paper"],
        "file_hash": file_hash,
        "source_path": source_path,
    }
    if doi:
        frontmatter["doi"] = doi
    if topics:
        frontmatter["hasTopic"] = [f"[[topics/{t}]]" for t in topics]
    if methods:
        frontmatter["uses_method"] = [f"[[methods/{m}]]" for m in methods]
    if cites:
        frontmatter["cites"] = [f"[[papers/{c}]]" for c in cites]
    if similar_to:
        frontmatter["similar_to"] = [f"[[papers/{s}]]" for s in similar_to]
    if cites_same:
        frontmatter["cites_same"] = [f"[[papers/{c}]]" for c in cites_same]

    fm = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False)

    sections = [f"---\n{fm}---\n"]

    if abstract:
        sections.append(f"## Abstract\n\n{abstract}\n")

    if summary:
        sections.append(f"## Summary\n\n{summary}\n")

    if figure_refs:
        lines = "\n".join(f"- **{key}**: {caption}" for key, caption in figure_refs)
        sections.append(f"## Figure References\n\n{lines}\n")

    if similar_to:
        links = "\n".join(f"- [[papers/{s}]]" for s in similar_to)
        sections.append(f"## Similar Papers\n\n{links}\n")

    if cites_same:
        links = "\n".join(f"- [[papers/{c}]]" for c in cites_same)
        sections.append(f"## Bibliographic Coupling\n\n{links}\n")

    sections.append(
        f"## Statistics\n\n- **Chunks**: {chunks_count}\n- **Figures**: {figures_count}\n"
    )

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


def method_note(name: str, papers: list[str]) -> str:
    """Generate markdown for a method note."""
    frontmatter = {
        "name": name,
        "tags": ["method"],
    }
    fm = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False)

    paper_links = "\n".join(f"- [[papers/{p}]]" for p in papers)
    return f"---\n{fm}---\n\n## Used In\n\n{paper_links}\n"
