"""Write one Obsidian-friendly markdown file per ingested Document.

The per-doc markdown contains YAML frontmatter with bibliographic
metadata and link sections (``cites``, ``similar_to``, ``cites_same``)
pointing to other docs in the corpus, followed by the cleaned body
text and an ``## Edges`` section with Obsidian wikilinks plus
``#edge/...`` tags so the vault can visualise citation / similarity /
coupling graphs.

This is the small wikify_simple analogue of the legacy
``wikify.ingest.vault.templates.paper_note`` template.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import Document
from ..paths import CorpusPaths


def write_doc_markdown(corpus: CorpusPaths, doc: Document, body: str) -> Path:
    """Overwrite ``<corpus>/markdown/<doc.id>.md`` with an enriched
    Obsidian-friendly rendering.

    ``body`` is the raw markdown text the parser emitted for the doc
    (what ``write_document`` would otherwise put on disk). The output
    prepends YAML frontmatter and appends an ``## Edges`` block.
    """
    path = corpus.markdown_dir / f"{doc.id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render(doc, body), encoding="utf-8")
    return path


def _render(doc: Document, body: str) -> str:
    meta = doc.metadata or {}
    authors = meta.get("authors") or []
    if isinstance(authors, str):
        authors = [authors]
    year = meta.get("year")
    doi = meta.get("doi")
    topics = meta.get("keywords") or []
    lines: list[str] = ["---"]
    lines.append(f"title: {_yaml_scalar(doc.title)}")
    lines.append("authors:")
    for a in authors:
        lines.append(f"  - {_yaml_scalar(str(a))}")
    if year is not None:
        lines.append(f"year: {year}")
    if doi:
        lines.append(f"doi: {_yaml_scalar(str(doi))}")
    if topics:
        lines.append(f"topics: {json.dumps(list(topics), ensure_ascii=False)}")
    lines.append(f"source_path: {_yaml_scalar(doc.source_path)}")
    lines.append("cites:")
    for did in doc.cites or []:
        lines.append(f"  - {_obsidian_link(did)}")
    lines.append("similar_to:")
    for did in doc.similar_to or []:
        lines.append(f"  - {_obsidian_link(did)}")
    lines.append("cites_same:")
    for did in doc.cites_same or []:
        lines.append(f"  - {_obsidian_link(did)}")
    lines.append("---")
    lines.append("")
    lines.append(body.strip())
    lines.append("")
    lines.append("## Edges")
    lines.append("")
    lines.append("### Citations")
    if doc.cites:
        for did in doc.cites:
            lines.append(f"- {_obsidian_link(did)} #edge/citation")
    else:
        lines.append("- _(none resolved to the corpus)_")
    lines.append("")
    lines.append("### Similar")
    if doc.similar_to:
        for did in doc.similar_to:
            lines.append(f"- {_obsidian_link(did)} #edge/similarity")
    else:
        lines.append("- _(none above threshold)_")
    lines.append("")
    lines.append("### Coupled")
    if doc.cites_same:
        for did in doc.cites_same:
            lines.append(f"- {_obsidian_link(did)} #edge/coupling")
    else:
        lines.append("- _(no shared references)_")
    lines.append("")
    return "\n".join(lines)


def _obsidian_link(doc_id: str) -> str:
    return f"[[papers/{doc_id}]]"


def _yaml_scalar(s: str) -> str:
    s = (s or "").replace("\n", " ").strip()
    if any(ch in s for ch in ":#[]{},&*!|>'\"%@`"):
        return '"' + s.replace('"', '\\"') + '"'
    return s
