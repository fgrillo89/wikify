"""HTML web-clip ingestion."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from rich.console import Console

from wikify.extract.chunker import chunk_sections
from wikify.store.models import DocType, Paper

console = Console()

# ── Extraction helpers ────────────────────────────────────────────────────────


def _extract_with_trafilatura(html: str) -> str:
    """Use trafilatura to extract main text content from HTML."""
    try:
        import trafilatura

        result = trafilatura.extract(html, include_comments=False, include_tables=True)
        return result or ""
    except ImportError:
        return ""


def _strip_html_fallback(html: str) -> str:
    """Naive HTML tag stripper when trafilatura is unavailable."""
    # Remove script/style blocks
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove remaining tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_text(html: str) -> str:
    """Extract clean text from HTML, preferring trafilatura."""
    text = _extract_with_trafilatura(html)
    if not text or len(text) < 100:
        text = _strip_html_fallback(html)
    return text


def _extract_meta_tag(html: str, prop: str = "", name: str = "") -> str:
    """Extract content from an HTML meta tag by property or name."""
    if prop:
        m = re.search(
            rf'<meta[^>]+property=["\']?{re.escape(prop)}["\']?[^>]+content=["\']([^"\']+)',
            html,
            re.IGNORECASE,
        )
        if not m:
            m = re.search(
                rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']?{re.escape(prop)}',
                html,
                re.IGNORECASE,
            )
    else:
        m = re.search(
            rf'<meta[^>]+name=["\']?{re.escape(name)}["\']?[^>]+content=["\']([^"\']+)',
            html,
            re.IGNORECASE,
        )
        if not m:
            m = re.search(
                rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']?{re.escape(name)}',
                html,
                re.IGNORECASE,
            )
    return m.group(1).strip() if m else ""


def _extract_title(html: str) -> str:
    """Extract title from <title> tag or first <h1>."""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return ""


def _extract_year(html: str) -> int | None:
    """Extract publication year from common meta tags."""
    import datetime

    candidates: list[str] = []
    candidates.append(_extract_meta_tag(html, prop="article:published_time"))
    candidates.append(_extract_meta_tag(html, prop="og:updated_time"))
    candidates.append(_extract_meta_tag(html, name="date"))
    candidates.append(_extract_meta_tag(html, name="pubdate"))
    candidates.append(_extract_meta_tag(html, name="DC.date"))

    for val in candidates:
        if not val:
            continue
        m = re.search(r"(\d{4})", val)
        if m:
            yr = int(m.group(1))
            if 1900 <= yr <= datetime.date.today().year + 1:
                return yr
    return None


def _extract_author(html: str) -> list[str]:
    """Extract author(s) from meta tags."""
    author = _extract_meta_tag(html, name="author")
    if not author:
        author = _extract_meta_tag(html, prop="article:author")
    if not author:
        author = _extract_meta_tag(html, name="DC.creator")
    if author:
        return [a.strip() for a in author.split(",") if a.strip()]
    return []


def _build_section_tree(text: str) -> dict:
    """Build a minimal section tree from heading lines in the extracted text."""
    tree: dict = {"title": "", "children": [], "source": "markdown"}
    stack: list[dict] = [tree]

    for line in text.split("\n"):
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


def parse_html(path: Path) -> tuple[Paper, list]:
    """Parse an HTML file into a Paper + chunks. Does not touch DB."""
    file_bytes = path.read_bytes()
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    html = path.read_text(encoding="utf-8", errors="replace")

    title = _extract_title(html) or path.stem
    year = _extract_year(html)
    authors = _extract_author(html)
    description = _extract_meta_tag(html, prop="og:description") or _extract_meta_tag(
        html, name="description"
    )

    body_text = _extract_text(html)

    section_tree = _build_section_tree(body_text)
    chunks = chunk_sections(body_text, section_tree, file_hash)

    paper = Paper(
        id=file_hash,
        title=title,
        authors=json.dumps(authors),
        summary=description or None,
        year=year,
        doc_type=DocType.WEB_ARTICLE,
        source_path=str(path),
        file_hash=file_hash,
        section_tree=json.dumps(section_tree),
    )

    return paper, chunks


def ingest_html(path: Path, return_id: bool = False) -> int | str | None:
    """Ingest an HTML file into the knowledge base.

    Returns 1 on success / 0 on skip, or paper ID if return_id=True (None on skip).
    """
    file_bytes = path.read_bytes()
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    from wikify.store.db import get_session

    with get_session() as session:
        from wikify.store.models import Paper

        existing = session.get(Paper, file_hash)
        if existing:
            console.print(f"[dim]Skipping (unchanged):[/dim] {path.name}")
            return None if return_id else 0

    paper, chunks = parse_html(path)
    _persist_html(paper, chunks, path)

    console.print(f"[green]Ingested:[/green] {path.name} ({len(chunks)} chunks)")
    return paper.id if return_id else 1


def _persist_html(paper: Paper, chunks: list, path: Path) -> None:
    """Persist parsed HTML to SQLite and vault."""
    from wikify.store.db import get_session
    from wikify.vault.writer import ensure_vault_dirs, write_paper_note

    ensure_vault_dirs()

    with get_session() as session:
        session.merge(paper)
        for chunk in chunks:
            session.merge(chunk)
        session.commit()

    full_text = "\n\n".join(c.content for c in chunks)
    write_paper_note(paper, len(chunks), 0, full_text=full_text)
