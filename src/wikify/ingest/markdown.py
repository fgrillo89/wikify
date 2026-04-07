"""Markdown and plain-text ingestion."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from rich.console import Console

from wikify.ingest.extract.chunker import chunk_sections
from wikify.core.store.models import DocType, Paper

console = Console()

# ── Frontmatter helpers ───────────────────────────────────────────────────────


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter from markdown text.

    Returns (metadata_dict, body_without_frontmatter).
    Uses python-frontmatter if available, falls back to simple regex.
    """
    try:
        import frontmatter as fm

        post = fm.loads(text)
        return dict(post.metadata), post.content
    except ImportError:
        pass

    # Regex fallback — handles simple key: value YAML only
    fm_pattern = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
    m = fm_pattern.match(text)
    if not m:
        return {}, text

    meta: dict = {}
    for line in m.group(1).splitlines():
        kv = line.split(":", 1)
        if len(kv) == 2:
            key = kv[0].strip()
            val = kv[1].strip().strip('"').strip("'")
            meta[key] = val

    body = text[m.end() :]
    return meta, body


def _extract_title(meta: dict, body: str, stem: str) -> str:
    """Extract title from frontmatter or first # heading."""
    if "title" in meta and meta["title"]:
        return str(meta["title"])
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return stem


def _extract_year(meta: dict) -> int | None:
    """Extract year from frontmatter date/created field."""
    import datetime

    for key in ("date", "created", "created_at", "year"):
        val = meta.get(key)
        if not val:
            continue
        val_str = str(val)
        # Try ISO date (2024-01-15)
        m = re.search(r"(\d{4})", val_str)
        if m:
            yr = int(m.group(1))
            if 1900 <= yr <= datetime.date.today().year + 1:
                return yr
    return None


def _extract_authors(meta: dict) -> list[str]:
    """Extract authors from frontmatter author/authors field."""
    for key in ("authors", "author"):
        val = meta.get(key)
        if not val:
            continue
        if isinstance(val, list):
            return [str(a) for a in val]
        if isinstance(val, str) and val:
            # Could be comma-separated
            parts = [p.strip() for p in val.split(",") if p.strip()]
            return parts
    return []


def _build_section_tree(md_text: str) -> dict:
    """Build a minimal section tree from markdown headings."""
    tree: dict = {"title": "", "children": [], "source": "markdown"}
    stack: list[dict] = [tree]

    for line in md_text.split("\n"):
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        level = 0
        for ch in stripped:
            if ch == "#":
                level += 1
            else:
                break
        heading = stripped[level:].strip()
        node: dict = {"title": heading, "level": level, "children": []}
        while len(stack) > 1 and stack[-1].get("level", 0) >= level:
            stack.pop()
        stack[-1]["children"].append(node)
        stack.append(node)

    return tree


# ── Public API ────────────────────────────────────────────────────────────────


def parse_markdown(path: Path) -> tuple[Paper, list]:
    """Parse a markdown or text file into a Paper + chunks. Does not touch DB."""
    file_bytes = path.read_bytes()
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    text = path.read_text(encoding="utf-8", errors="replace")
    meta, body = _parse_frontmatter(text)

    title = _extract_title(meta, body, path.stem)
    year = _extract_year(meta)
    authors = _extract_authors(meta)
    summary = meta.get("description") or meta.get("summary") or None

    section_tree = _build_section_tree(body)
    chunks = chunk_sections(body, section_tree, file_hash)

    paper = Paper(
        id=file_hash,
        title=title,
        authors=json.dumps(authors),
        summary=summary,
        year=year,
        doc_type=DocType.MARKDOWN,
        source_path=str(path),
        file_hash=file_hash,
        section_tree=json.dumps(section_tree),
    )

    return paper, chunks


def ingest_markdown(path: Path, return_id: bool = False) -> int | str | None:
    """Ingest a markdown or plain-text file into the knowledge base.

    Returns 1 on success / 0 on skip, or the paper ID if return_id=True
    (None on skip).
    """
    file_bytes = path.read_bytes()
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    from wikify.core.store.db import get_session

    with get_session() as session:
        from wikify.core.store.models import Paper

        existing = session.get(Paper, file_hash)
        if existing:
            console.print(f"[dim]Skipping (unchanged):[/dim] {path.name}")
            return None if return_id else 0

    paper, chunks = parse_markdown(path)
    _persist_markdown(paper, chunks, path)

    console.print(f"[green]Ingested:[/green] {path.name} ({len(chunks)} chunks)")
    return paper.id if return_id else 1


def _persist_markdown(paper: Paper, chunks: list, path: Path) -> None:
    """Persist parsed markdown to SQLite and vault."""
    from wikify.core.store.db import get_session
    from wikify.ingest.vault.writer import ensure_vault_dirs, write_paper_note

    ensure_vault_dirs()

    with get_session() as session:
        session.merge(paper)
        for chunk in chunks:
            session.merge(chunk)
        session.commit()

    full_text = "\n\n".join(c.content for c in chunks)
    write_paper_note(paper, len(chunks), 0, full_text=full_text)
