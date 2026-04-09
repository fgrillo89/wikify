"""Deterministic author/person pages from metadata + citations.

Enriched by the writer when chunk-extracted evidence accumulates.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import Path

from ..ingest.metadata import _is_valid_author
from ..models import Document, Evidence, WikiPage
from ..store.page_naming import page_filename, page_id_from_title

_NORM_RE = re.compile(r"[^a-z0-9]+")

# Stopwords for the deterministic "field hint" title-phrase extractor.
_STOP = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "based",
    "by",
    "case",
    "effect",
    "for",
    "from",
    "in",
    "into",
    "is",
    "its",
    "new",
    "novel",
    "of",
    "on",
    "or",
    "over",
    "role",
    "s",
    "studies",
    "study",
    "the",
    "their",
    "to",
    "toward",
    "towards",
    "use",
    "using",
    "via",
    "with",
    "within",
    "without",
}

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]+")


def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return _NORM_RE.sub("-", s.lower()).strip("-")


def _normalize_author_name(name: str) -> str:
    """Normalize whitespace and trailing punctuation; preserve initials."""
    if not name:
        return ""
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(",.;")
    name = re.sub(r"\s+\d+(?:\s*,\s*\d+)*$", "", name)
    return name


def _author_key(name: str) -> str:
    n = _normalize_author_name(name)
    return _NORM_RE.sub(" ", n.lower()).strip()


def build_author_pages(
    docs: list[Document],
    existing_index=None,  # noqa: ARG001 — reserved for future merge
    existing_page_dir: Path | None = None,
) -> list[WikiPage]:
    """Return one WikiPage per unique valid author across ``docs``."""
    bucket: dict[str, dict] = {}

    for doc in docs:
        meta = doc.metadata or {}
        year = meta.get("year")
        primary_authors = meta.get("authors") or []
        if isinstance(primary_authors, str):
            primary_authors = [primary_authors]
        normed_primary = [
            _normalize_author_name(str(a))
            for a in primary_authors
            if _is_valid_author(_normalize_author_name(str(a)))
        ]
        for name in normed_primary:
            key = _author_key(name)
            if not key:
                continue
            entry = bucket.setdefault(
                key,
                {"display": name, "primary": [], "cited": [], "collaborators": set()},
            )
            entry["primary"].append((doc, year))
            for other in normed_primary:
                if _author_key(other) != key:
                    entry["collaborators"].add(other)

        for cit in doc.citations or []:
            cit_year = cit.get("year")
            cit_title = cit.get("title") or cit.get("raw_text", "")[:120]
            for raw in cit.get("authors") or []:
                name = _normalize_author_name(str(raw))
                if not _is_valid_author(name):
                    continue
                key = _author_key(name)
                if not key:
                    continue
                entry = bucket.setdefault(
                    key,
                    {"display": name, "primary": [], "cited": [], "collaborators": set()},
                )
                entry["cited"].append((doc, cit_year, cit_title))

    pages: list[WikiPage] = []
    for key, info in sorted(bucket.items()):
        display = info["display"]
        if not _slug(display):
            continue
        page_id = page_id_from_title(display)
        if not page_id:
            continue
        primary = info["primary"]
        cited = info["cited"]
        collaborators = sorted(info["collaborators"])

        existing_links: list[str] = []
        if existing_page_dir is not None:
            existing_links = _existing_paper_links(existing_page_dir / page_filename(page_id))

        body = _render_body(display, primary, cited, collaborators, existing_links)
        evidence = _build_evidence(primary, cited)
        if not evidence:
            continue
        pages.append(
            WikiPage(
                id=page_id,
                kind="person",
                title=display,
                aliases=[],
                body_markdown=body,
                evidence=evidence,
                provenance={
                    "source": "deterministic",
                    "strategy": "deterministic",
                    "tags": "author",
                    "primary_count": len(primary),
                    "from_citation_count": len(cited),
                    "collaborator_count": len(collaborators),
                },
            )
        )
    return pages


def _render_body(
    name: str,
    primary: list[tuple[Document, int | None]],
    cited: list[tuple[Document, int | None, str]],
    collaborators: list[str],
    existing_links: list[str],
) -> str:
    lines: list[str] = []
    lines.append(_lead_paragraph(name, primary))
    lines.append("")

    if primary:
        contrib = _notable_contributions(primary)
        if contrib:
            lines.append("## Notable contributions")
            lines.append("")
            lines.extend(contrib)
            lines.append("")

        pub_lines, pub_titles = _publications_section(primary)
        # Merge existing on-disk titles (append-only across re-runs).
        merged_existing = [t for t in existing_links if t not in pub_titles]
        if merged_existing:
            for t in merged_existing:
                pub_lines.append(f"- n.d. [[{t}]]")
        if pub_lines:
            lines.append("## Publications in this corpus")
            lines.append("")
            lines.extend(pub_lines)
            lines.append("")
    elif existing_links:
        lines.append("## Publications in this corpus")
        lines.append("")
        for t in existing_links:
            lines.append(f"- n.d. [[{t}]]")
        lines.append("")

    if cited:
        lines.append("## Cited works in this corpus")
        lines.append("")
        seen_pairs: set[tuple[str, str]] = set()
        for doc, cit_year, cit_title in cited:
            key = (doc.id, (cit_title or "")[:80])
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            year_str = str(cit_year) if cit_year else "n.d."
            citing = doc.title or doc.id
            # Skip citation titles that are clearly garbage (volume/page
            # fragments, very short, or mostly digits).
            title_clean = (cit_title or "").strip()
            digit_heavy = sum(c.isdigit() for c in title_clean) > len(title_clean) // 2
            if len(title_clean) < 10 or digit_heavy:
                lines.append(f"- {year_str}. Cited in *{citing}*")
            else:
                lines.append(f"- {year_str}. *{title_clean}* (cited in *{citing}*)")
        lines.append("")

    if collaborators:
        lines.append("## Collaborators")
        lines.append("")
        for c in collaborators:
            lines.append(f"- [[{c}]]")
        lines.append("")

    return "\n".join(lines).strip()


def _lead_paragraph(
    name: str,
    primary: list[tuple[Document, int | None]],
) -> str:
    if not primary:
        return (
            f"**{name}** appears in this corpus only through citations in other "
            f"authors' reference lists."
        )
    years = [y for _, y in primary if isinstance(y, int)]
    n = len({d.id for d, _ in primary})
    plural = "s" if n != 1 else ""
    if years:
        y_lo, y_hi = min(years), max(years)
        span = f"from {y_lo}" if y_lo == y_hi else f"from {y_lo} to {y_hi}"
    else:
        span = "of unspecified date"

    field_hint = _field_hint([d.title or "" for d, _ in primary])
    anchor = _anchor_title(primary)

    parts = [f"**{name}**"]
    if field_hint:
        parts.append(f"is associated with *{field_hint}* in this corpus,")
    else:
        parts.append("appears in this corpus,")
    tail = f"contributing {n} paper{plural} {span}"
    if anchor:
        tail += f", notably *{anchor}*."
    else:
        tail += "."
    parts.append(tail)
    return " ".join(parts)


def _field_hint(titles: list[str]) -> str:
    """Most-common content word across the author's paper titles."""
    counts: Counter[str] = Counter()
    for t in titles:
        for w in _WORD_RE.findall(t.lower()):
            if len(w) < 4:
                continue
            if w in _STOP:
                continue
            counts[w] += 1
    if not counts:
        return ""
    word, n = counts.most_common(1)[0]
    if n < 1:
        return ""
    return word


def _anchor_title(primary: list[tuple[Document, int | None]]) -> str:
    """Earliest-year paper title, tie-broken by doc id for stability."""
    dated = [(d, y) for d, y in primary if isinstance(y, int)]
    if dated:
        dated.sort(key=lambda t: (t[1], t[0].id))
        return dated[0][0].title or ""
    sorted_primary = sorted(primary, key=lambda t: t[0].id)
    return sorted_primary[0][0].title or ""


def _notable_contributions(primary: list[tuple[Document, int | None]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for doc, _ in primary:
        if doc.id in seen:
            continue
        seen.add(doc.id)
        title = doc.title or doc.id
        text = (doc.tldr or "").strip() or (doc.abstract or "").strip()
        summary = ""
        if text:
            summary = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].strip()[:200]
        out.append(f"- [[{title}]] — {summary}" if summary else f"- [[{title}]]")
    return out


def _publications_section(
    primary: list[tuple[Document, int | None]],
) -> tuple[list[str], set[str]]:
    seen: set[str] = set()
    titles: set[str] = set()
    lines: list[str] = []
    for doc, year in sorted(
        primary, key=lambda t: (t[1] if isinstance(t[1], int) else 9999, t[0].id)
    ):
        if doc.id in seen:
            continue
        seen.add(doc.id)
        title = doc.title or doc.id
        titles.add(title)
        lines.append(f"- {str(year) if year else 'n.d.'}. [[{title}]]")
    return lines, titles


_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def _existing_paper_links(page_path: Path) -> list[str]:
    """Parse an existing author page for Publications wikilinks."""
    try:
        content = page_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return []
    out: list[str] = []
    in_section = False
    for line in content.splitlines():
        if line.startswith("## "):
            in_section = line.strip().lower().startswith("## publications")
            continue
        if not in_section:
            continue
        m = _WIKILINK_RE.search(line)
        if m:
            title = m.group(1).strip()
            if title and title not in out:
                out.append(title)
    return out


def _build_evidence(
    primary: list[tuple[Document, int | None]],
    cited: list[tuple[Document, int | None, str]],
) -> list[Evidence]:
    """One Evidence entry per unique linked doc."""
    seen: set[str] = set()
    out: list[Evidence] = []
    for doc in [d for d, *_ in [*primary, *cited]]:
        if doc.id in seen:
            continue
        seen.add(doc.id)
        out.append(
            Evidence(
                marker=f"e{len(out) + 1}",
                chunk_id=_first_chunk_id(doc),
                doc_id=doc.id,
                quote=doc.title or doc.id,
            )
        )
    return out


def merge_extracted_evidence(page: WikiPage, extracted_evidence: list[Evidence]) -> None:
    """Append extracted evidence; preserve skeleton. Skip duplicate chunk_ids."""
    existing_chunks = {ev.chunk_id for ev in page.evidence}
    for ev in extracted_evidence:
        if ev.chunk_id in existing_chunks:
            continue
        existing_chunks.add(ev.chunk_id)
        page.evidence.append(
            Evidence(
                marker=f"e{len(page.evidence) + 1}",
                chunk_id=ev.chunk_id,
                doc_id=ev.doc_id,
                quote=ev.quote,
                locator=ev.locator,
            )
        )


def _first_chunk_id(doc: Document) -> str:
    for sec in doc.sections or []:
        if sec.chunk_ids:
            return sec.chunk_ids[0]
    return f"{doc.id}/c000"
