"""Static HTML site renderer for a wiki bundle.

Walks a
bundle's ``articles/`` and ``people/`` directories, parses each page
through the canonical ``store.wiki_bundle.parse_page`` parser, runs the
markdown body through ``python-markdown`` (with the ``footnotes``,
``tables``, ``attr_list``, ``def_list``, and ``pymdownx.superfences``
extensions), resolves ``[[wikilinks]]`` against an in-memory
title/alias map computed from the loaded pages, copies inline
``![Figure N](path)`` images into the output's ``assets/`` tree, and
emits one HTML file per page plus an index landing page.

Special pages (random/recent/categories/domain/metrics) are intentionally
omitted. Categories are derived from ``page_kind`` only.
"""

import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any, Self

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape

from wikify.bundle.wiki.navigation import read_navigation
from wikify.bundle.wiki.page import Bundle, Page
from wikify.bundle.wiki.page_naming import url_slug
from wikify.ingest.metadata import _is_valid_author

WIKI_NAME = "Wikify Simple"

_NORM_RE = re.compile(r"[^a-z0-9]+")


def _normalize(s: str) -> str:
    return _NORM_RE.sub("-", s.lower()).strip("-")


def _plain_excerpt(text: str, limit: int = 200) -> str:
    """Return a compact plain-text excerpt from markdown-ish prose."""
    text = re.sub(r"\[\^e\d+\]", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_`~]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]

_MD_EXTENSIONS = [
    "tables",
    "fenced_code",
    "attr_list",
    "def_list",
    "footnotes",
    "sane_lists",
    "pymdownx.superfences",
    "pymdownx.arithmatex",
]

_MD_EXTENSION_CONFIGS = {
    "pymdownx.arithmatex": {
        "generic": True,  # emit raw $/$$ for KaTeX auto-render
    },
}

SKELETON_MIN_BODY_LEN = 200

_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_FIGURE_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_HEADING_RE = re.compile(r'<h2(?:\s+id="([^"]*)")?[^>]*>(.+?)</h2>', re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_TEMPLATES_DIR = Path(__file__).parent / "templates"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_site(
    bundle: Bundle,
    out_dir: Path,
    *,
    corpus_root: Path | None = None,
) -> Path:
    """Render a wiki bundle to a static HTML site under ``out_dir``.

    Takes a pre-loaded ``Bundle`` (the wiki-bundle view of
    ``<bundle>/wiki/``). Returns ``out_dir``.
    """
    loaded = bundle

    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "static").mkdir(exist_ok=True)
    (out_dir / "assets").mkdir(exist_ok=True)

    # Load doc_id -> source URL map from the corpus and stage cited PDFs
    # into ``assets/sources/`` so the rendered reference list can hyperlink
    # straight to the paper a reader is clicking on.
    doc_source_map = _load_doc_source_map(corpus_root, out_dir)

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )

    all_page_views = [_PageView.from_page(p) for p in loaded.pages]
    # Only include pages with real prose in navigation and listing.
    # Skeleton pages (body_clean < SKELETON_MIN_BODY_LEN chars) are omitted
    # from the rendered site. Also filter out person pages whose title looks
    # like a journal name.
    page_views = [
        pv
        for pv in all_page_views
        if pv.has_prose and not (pv.kind == "person" and not _is_valid_author(pv.title))
    ]
    skipped = len(all_page_views) - len(page_views)
    if skipped:
        print(
            f"[html] skipped {skipped} skeleton page(s) (body < {SKELETON_MIN_BODY_LEN} chars)",
            file=sys.stderr,
        )
    concepts = sorted(
        [pv for pv in page_views if pv.kind == "article"],
        key=lambda v: v.title.lower(),
    )
    people = sorted(
        [pv for pv in page_views if pv.kind == "person"],
        key=lambda v: v.title.lower(),
    )
    page_by_id = {p.id: p for p in loaded.pages}
    stats = _build_site_stats(
        pages=[page_by_id[pv.id] for pv in page_views if pv.id in page_by_id],
        concepts=concepts,
        people=people,
        corpus_root=corpus_root,
    )
    navigation = _build_navigation_view(
        read_navigation(loaded.root.parent),
        page_views={pv.id: pv for pv in page_views},
    )
    key_articles = _key_articles(concepts, page_by_id=page_by_id)
    shared_ctx = {
        "wiki_name": WIKI_NAME,
        "stats": stats,
        "concepts": concepts,
        "people": people,
        "navigation": navigation,
        "key_articles": key_articles,
    }

    slug_to_url = {pv.id: pv.url for pv in page_views}
    alias_to_id = {_normalize(name): pv.id for pv in page_views for name in (pv.title, *pv.aliases)}

    for pv in page_views:
        page = page_by_id.get(pv.id)
        if page is None:
            continue
        html_path = out_dir / pv.url
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_str = _render_article(
            pv,
            page,
            env,
            slug_to_url=slug_to_url,
            alias_to_id=alias_to_id,
            out_dir=out_dir,
            corpus_root=corpus_root,
            shared_ctx=shared_ctx,
            page_by_id=page_by_id,
            root="../",
            doc_source_map=doc_source_map,
        )
        html_path.write_text(html_str, encoding="utf-8")

    # Index page lives at the site root.
    index_html = env.get_template("index_page.html").render(
        title="Main Page",
        root="",
        **shared_ctx,
    )
    (out_dir / "index.html").write_text(index_html, encoding="utf-8")

    # Static assets: CSS + search.js stub.
    shutil.copy2(_TEMPLATES_DIR / "wiki.css", out_dir / "static" / "wiki.css")
    (out_dir / "static" / "search.js").write_text(_SEARCH_JS, encoding="utf-8")

    # Search index sidecar (title + url + first paragraph).
    search_index = [{"title": pv.title, "url": pv.url, "excerpt": pv.excerpt} for pv in page_views]
    (out_dir / "search-index.json").write_text(
        json.dumps(search_index, ensure_ascii=False),
        encoding="utf-8",
    )

    return out_dir


def _build_site_stats(
    *,
    pages: list[Page],
    concepts: list["_PageView"],
    people: list["_PageView"],
    corpus_root: Path | None,
) -> dict[str, Any]:
    used_docs = sorted({ev.doc_id for page in pages for ev in page.evidence if ev.doc_id})
    years = _years_from_doc_ids(used_docs)
    words_processed: int | None = None
    if corpus_root is not None and used_docs:
        corpus_stats = _corpus_used_doc_stats(Path(corpus_root), used_docs)
        if corpus_stats["years"]:
            years = corpus_stats["years"]
        words_processed = corpus_stats["words"]
    date_range = ""
    if years:
        date_range = f"{min(years)}-{max(years)}"
    return {
        "total_articles": len(pages),
        "total_concepts": len(concepts),
        "total_people": len(people),
        "source_articles_used": len(used_docs),
        "words_processed": words_processed,
        "date_range": date_range,
        "figures_included": sum(len(page.figures or []) for page in pages),
        "rendered_at": datetime.now(UTC).strftime("%Y-%m-%d"),
    }


def _years_from_doc_ids(doc_ids: list[str]) -> list[int]:
    years: list[int] = []
    for doc_id in doc_ids:
        m = re.match(r"\[(\d{4})\b", doc_id or "")
        if m:
            years.append(int(m.group(1)))
    return years


def _corpus_used_doc_stats(corpus_root: Path, doc_ids: list[str]) -> dict[str, Any]:
    db_path = corpus_root / "wikify.db"
    if not db_path.is_file():
        return {"years": [], "words": None}
    import sqlite3 as _sqlite

    placeholders = ",".join("?" * len(doc_ids))
    years: list[int] = []
    words = 0
    con = _sqlite.connect(str(db_path))
    con.row_factory = _sqlite.Row
    try:
        for r in con.execute(
            f"SELECT year FROM documents WHERE doc_id IN ({placeholders})",
            doc_ids,
        ):
            if r["year"]:
                years.append(int(r["year"]))
        for r in con.execute(
            f"SELECT text FROM chunks WHERE doc_id IN ({placeholders})",
            doc_ids,
        ):
            words += len(re.findall(r"\b\w+\b", r["text"] or ""))
    finally:
        con.close()
    return {"years": years, "words": words or None}


def _build_navigation_view(
    navigation: dict[str, Any] | None,
    *,
    page_views: dict[str, "_PageView"],
) -> dict[str, Any] | None:
    if not navigation or not isinstance(navigation.get("groups"), list):
        return None

    def group_view(group: dict[str, Any]) -> dict[str, Any]:
        pages = [
            page_views[page_id]
            for page_id in group.get("page_ids", [])
            if page_id in page_views
        ]
        children = [
            group_view(child)
            for child in group.get("children", [])
            if isinstance(child, dict)
        ]
        return {
            "id": group.get("id", ""),
            "title": group.get("title", ""),
            "description": group.get("description", ""),
            "pages": pages,
            "children": children,
        }

    groups = [
        group_view(group)
        for group in navigation.get("groups", [])
        if isinstance(group, dict)
    ]
    return {"groups": groups}


def _key_articles(
    concepts: list["_PageView"], *, page_by_id: dict[str, Page]
) -> list["_PageView"]:
    return sorted(
        concepts,
        key=lambda pv: (
            -len(page_by_id.get(pv.id).links if page_by_id.get(pv.id) else []),
            -pv.n_evidence,
            pv.title.lower(),
        ),
    )[:8]


# ---------------------------------------------------------------------------
# Per-page rendering
# ---------------------------------------------------------------------------


@dataclass
class _PageView:
    id: str
    kind: str
    title: str
    aliases: list[str]
    url: str  # site-relative
    n_evidence: int
    excerpt: str
    has_prose: bool  # True if body has real content beyond just evidence

    @classmethod
    def from_page(cls, page: Page) -> Self:
        sub = "articles" if page.kind == "article" else "people"
        excerpt = ""
        for line in page.body_clean.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("|"):
                excerpt = _plain_excerpt(stripped)
                break
        # A page "has prose" if body_clean is at least SKELETON_MIN_BODY_LEN chars.
        has_prose = len(page.body_clean) >= SKELETON_MIN_BODY_LEN
        return cls(
            id=page.id,
            kind=page.kind,
            title=page.title,
            aliases=list(page.aliases),
            url=f"{sub}/{url_slug(page.id)}.html",
            n_evidence=len(page.evidence),
            excerpt=excerpt,
            has_prose=has_prose,
        )


def _render_article(
    pv: _PageView,
    page: Page,
    env: Environment,
    *,
    slug_to_url: dict[str, str],
    alias_to_id: dict[str, str],
    out_dir: Path,
    corpus_root: Path | None,
    shared_ctx: dict[str, Any],
    page_by_id: dict[str, Page],
    root: str,
    doc_source_map: dict[str, str] | None = None,
) -> str:
    # Reconstruct the full body (frontmatter-stripped) including the
    # ## Evidence block, so the markdown footnotes extension can render
    # the [^eN]: definitions as proper footnotes.
    raw = page.path.read_text(encoding="utf-8")
    body_md = _strip_frontmatter(raw)

    # Stage figures referenced inline. Rewrite their src to a path
    # relative to the page's HTML location (which lives at
    # out_dir/<sub>/<id>.html, i.e. one level deep).
    body_md = _stage_and_rewrite_figures(
        body_md,
        page=page,
        out_dir=out_dir,
        corpus_root=corpus_root,
        page_url_depth=1,
    )
    body_md = _replace_selected_figure_placeholders(
        body_md,
        page=page,
        out_dir=out_dir,
        corpus_root=corpus_root,
        page_url_depth=1,
    )

    # Clean up evidence footnote lines: format as bibliographic references.
    body_md = _clean_evidence_lines(body_md, doc_source_map=doc_source_map)

    # Format bibliography section: convert [N] markers to superscript links
    body_md = _format_bibliography_section(body_md)

    # Normalize the section heading: "Evidence" -> "References"
    body_md = body_md.replace("## Evidence\n", "## References\n")

    # Resolve [[wikilinks]] BEFORE markdown conversion so they emit
    # plain <a> tags rather than literal "[[...]]" text.
    body_md = _resolve_wikilinks(
        body_md,
        slug_to_url=slug_to_url,
        alias_to_id=alias_to_id,
        root=root,
    )

    md = markdown.Markdown(
        extensions=_MD_EXTENSIONS,
        extension_configs=_MD_EXTENSION_CONFIGS,
    )
    body_html = md.convert(body_md)

    toc = _build_toc(body_html)
    categories = [pv.kind]

    # Build "See also" from crosslinks that exist as rendered pages.
    see_also = []
    seen_ids: set[str] = set()
    # First: explicit crosslinks from the page's links field.
    for link_id in page.links:
        if link_id in slug_to_url and link_id != pv.id and link_id not in seen_ids:
            seen_ids.add(link_id)
            link_title = link_id
            for candidate in shared_ctx.get("concepts", []) + shared_ctx.get("people", []):
                if candidate.id == link_id:
                    link_title = candidate.title
                    break
            see_also.append({"title": link_title, "url": slug_to_url[link_id]})
    # Second: if few explicit links resolved, add other rendered concepts
    # that share evidence docs (co-occurrence heuristic).
    if len(see_also) < 5:
        page_docs = {ev.doc_id for ev in page.evidence}
        for candidate in shared_ctx.get("concepts", []):
            if candidate.id == pv.id or candidate.id in seen_ids:
                continue
            if len(see_also) >= 10:
                break
            # Check if this candidate shares evidence docs
            cand_page = page_by_id.get(candidate.id)
            if cand_page and any(ev.doc_id in page_docs for ev in cand_page.evidence):
                seen_ids.add(candidate.id)
                see_also.append({"title": candidate.title, "url": candidate.url})

    # Build infobox for article pages.
    infobox = {}
    if pv.kind == "article":
        infobox["Type"] = "Article"
        if pv.n_evidence:
            infobox["Sources"] = str(pv.n_evidence)
    elif pv.kind == "person":
        infobox["Type"] = "Person"
        prov = page.provenance or {}
        if prov.get("primary_count"):
            infobox["Papers"] = str(prov["primary_count"])
        if prov.get("collaborator_count"):
            infobox["Collaborators"] = str(prov["collaborator_count"])

    template = env.get_template("article.html")
    visible_aliases = [
        a for a in pv.aliases
        if isinstance(a, str) and not a.lower().startswith("author:")
    ]
    return template.render(
        title=pv.title,
        aliases=visible_aliases,
        content=body_html,
        toc=toc,
        categories=categories,
        see_also=see_also[:10],  # cap at 10 links
        infobox=infobox if infobox else None,
        root=root,
        **shared_ctx,
    )


def _load_doc_source_map(
    corpus_root: Path | None, out_dir: Path,
) -> dict[str, str]:
    """Return ``{doc_id: url}`` for every doc the corpus knows about.

    Preference order per doc:

    1. ``doi`` — rendered as ``https://doi.org/<doi>``. Always portable.
    2. ``source_path`` — the original PDF (or markdown) the doc was
       ingested from. Copied into ``<out_dir>/assets/sources/`` so the
       wiki carries the file and the rendered ``<a>`` points at the
       relative path. The reader clicks and the PDF opens in their
       browser.

    Returns an empty dict when no corpus is provided or the corpus
    has no SQLite store.
    """
    if corpus_root is None:
        return {}
    db_path = Path(corpus_root) / "wikify.db"
    if not db_path.is_file():
        return {}
    import sqlite3 as _sqlite

    sources_dir = out_dir / "assets" / "sources"
    out: dict[str, str] = {}
    con = _sqlite.connect(str(db_path))
    con.row_factory = _sqlite.Row
    try:
        rows = list(con.execute(
            "SELECT doc_id, source_path, doi FROM documents"
        ))
    finally:
        con.close()
    for r in rows:
        doc_id = r["doc_id"]
        doi = (r["doi"] or "").strip()
        if doi:
            out[doc_id] = f"https://doi.org/{doi}"
            continue
        source_path = (r["source_path"] or "").strip()
        if not source_path:
            continue
        # source_path may be Windows-style (backslashes) from an older ingest.
        src = Path(source_path.replace("\\", "/"))
        if not src.is_absolute() and not src.is_file():
            # Try relative to corpus_root's parent (typical layout).
            alt = Path(corpus_root).parent.parent / src
            if alt.is_file():
                src = alt
        if not src.is_file():
            continue
        # Stage under assets/sources/ with a stable short-handle filename
        # so the per-page link is a fixed relative URL regardless of where
        # the corpus lives on disk.
        short = doc_id[-12:] if len(doc_id) > 12 else doc_id
        suffix = src.suffix or ".pdf"
        dest = sources_dir / f"{short}{suffix}"
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src, dest)
            except OSError:
                continue
        # Per-page HTML lives at out_dir/<sub>/<id>.html (depth 1), so
        # the relative URL to assets/sources/<short>.pdf is one level up.
        out[doc_id] = f"../assets/sources/{short}{suffix}"
    return out


def _strip_frontmatter(text: str) -> str:
    body = text
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            body = body[end + 4 :].lstrip("\n")
    # Strip the leading `# Title` heading if present — the article
    # template already renders the title as <h1>, so keeping it in
    # the body produces a duplicate.
    if body.startswith("# "):
        first_nl = body.find("\n")
        if first_nl != -1:
            body = body[first_nl:].lstrip("\n")
    return body


def _resolve_wikilinks(
    body: str,
    *,
    slug_to_url: dict[str, str],
    alias_to_id: dict[str, str],
    root: str,
) -> str:
    def _replace(match: re.Match[str]) -> str:
        text = match.group(1).strip()
        page_id = alias_to_id.get(_normalize(text))
        if page_id is None:
            page_id = slug_to_url and (text if text in slug_to_url else None)
        if page_id is not None and page_id in slug_to_url:
            return f'<a href="{root}{slug_to_url[page_id]}">{text}</a>'
        # Unresolved wikilinks render as plain text (no dead links).
        return text

    return _WIKILINK_RE.sub(_replace, body)


# Matches the internal chunk hash suffix in evidence lines, e.g.
# "__c0000__fec9f3fb" at the end of a chunk_id.
_CHUNK_HASH_RE = re.compile(r"__c\d{4}__[0-9a-f]{6,}")

# Extracts [Year Author] prefix from doc_id, e.g. "[2020 Liu]"
_DOC_YEAR_RE = re.compile(r"\[(\d{4})\s+([^\]]+)\]")


def _clean_evidence_lines(
    body: str, *, doc_source_map: dict[str, str] | None = None,
) -> str:
    """Reformat evidence footnote definitions as bibliographic references.

    Transforms raw evidence like:
        ``[^e1]: chunk_hash (doc_id) > "quote"``
    into clean references like:
        ``[^e1]: [Author (Year). *Paper Title.*](url) "quote"``

    When ``doc_source_map`` resolves the doc_id to a URL (DOI link or
    locally-staged PDF path) the bibliographic head is wrapped in a
    markdown link so the rendered footnote becomes clickable.
    """
    lines = body.split("\n")
    out: list[str] = []
    for line in lines:
        if line.startswith("[^") and "]:" in line:
            line = _CHUNK_HASH_RE.sub("", line)
            line = _format_evidence_as_reference(line, doc_source_map=doc_source_map)
        out.append(line)
    return "\n".join(out)


def _format_evidence_as_reference(
    line: str, *, doc_source_map: dict[str, str] | None = None,
) -> str:
    """Format a single evidence footnote line as a bibliographic reference."""
    # Extract marker prefix
    marker_end = line.index("]:") + 2
    marker = line[:marker_end]
    rest = line[marker_end:].strip()

    # Parse the evidence value: look for ' > "quote"'
    sep = rest.find(' > "')
    if sep == -1:
        # No quote separator -- just clean up what we have
        return f"{marker} {_format_doc_id(rest, doc_source_map=doc_source_map)}"

    head = rest[:sep].strip()
    quote = rest[sep + 4 :].rstrip('"').strip()

    # Head may be "chunk_id (doc_id)" or just "doc_id"
    doc_id = head
    paren_open = head.rfind("(")
    paren_close = head.rfind(")")
    if paren_open > 0 and paren_close > paren_open:
        doc_id = head[paren_open + 1 : paren_close].strip()

    formatted = _format_doc_id(doc_id, doc_source_map=doc_source_map)
    if quote:
        return f'{marker} {formatted} -- "{quote}"'
    return f"{marker} {formatted}"


# Bibliography: inline [N] markers and ## Bibliography section
_BIB_INLINE_RE = re.compile(r"\[(\d{1,3})\]")
_BIB_SECTION_RE = re.compile(r"^## Bibliography\s*$", re.MULTILINE)


def _format_bibliography_section(body: str) -> str:
    """Format ## Bibliography as a numbered list and [N] as superscripts.

    If the body contains a ``## Bibliography`` section, convert inline
    ``[N]`` markers to superscript anchors and format the bibliography
    entries as a numbered list.  If no bibliography section exists,
    returns the body unchanged.
    """
    m = _BIB_SECTION_RE.search(body)
    if not m:
        return body

    # Split into body before bibliography and the bibliography entries
    before_bib = body[:m.start()]
    bib_text = body[m.end():]

    # Parse bibliography entries: "[N]: Author (Year). Title." or "N. Author..."
    bib_entries: dict[int, str] = {}
    bib_lines: list[str] = []
    other_lines: list[str] = []
    for line in bib_text.split("\n"):
        stripped = line.strip()
        # Pattern: "[N]: ..." or "N. ..."
        bm = re.match(r"\[(\d+)\]:\s*(.*)", stripped)
        if not bm:
            bm = re.match(r"(\d+)\.\s+(.*)", stripped)
        if bm:
            num = int(bm.group(1))
            text = bm.group(2).strip()
            bib_entries[num] = text
            bib_lines.append(f'<li id="bib-{num}" value="{num}">{text}</li>')
        elif stripped:
            other_lines.append(line)

    if not bib_entries:
        return body

    # Convert inline [N] to superscript links in the body text
    def _replace_inline(match: re.Match) -> str:
        n = int(match.group(1))
        if n in bib_entries:
            return f'<sup><a href="#bib-{n}">[{n}]</a></sup>'
        return match.group(0)

    before_bib = _BIB_INLINE_RE.sub(_replace_inline, before_bib)

    # Rebuild with formatted bibliography
    bib_html = "\n## Bibliography\n\n<ol class=\"bibliography\">\n"
    bib_html += "\n".join(bib_lines)
    bib_html += "\n</ol>\n"
    if other_lines:
        bib_html += "\n".join(other_lines)

    return before_bib + bib_html


# Trailing content hash: _hexstring at end of doc_id (5+ hex chars)
_TRAILING_HASH_RE = re.compile(r"_[0-9a-f]{5,}$")


def _format_doc_id(
    doc_id: str, *, doc_source_map: dict[str, str] | None = None,
) -> str:
    """Turn a doc_id like '[2020 Liu] Paper Title_hash' into 'Liu (2020). *Paper Title*.'

    When ``doc_source_map`` has a URL for the doc, wrap the reference
    text in a markdown link so the rendered footnote becomes clickable
    (DOI link, or a locally-staged PDF copy in the site's assets dir).
    """
    raw_doc_id = doc_id
    # Strip chunk and content hash suffixes
    doc_id = _CHUNK_HASH_RE.sub("", doc_id).strip().rstrip("_")
    doc_id = _TRAILING_HASH_RE.sub("", doc_id).strip().rstrip("_")

    # Resolve URL once; the lookup key is the raw doc_id (the form
    # the source map was built with).
    url = (doc_source_map or {}).get(raw_doc_id) or (doc_source_map or {}).get(doc_id)

    # Try to extract [Year Author] prefix
    m = _DOC_YEAR_RE.match(doc_id)
    if m:
        year = m.group(1)
        author = m.group(2).strip()
        title = doc_id[m.end() :].strip().lstrip("_ ").replace("_", " ")
        # Strip trailing hashes from title too
        title = _TRAILING_HASH_RE.sub("", title).strip().rstrip("_")
        title = title.replace("_", " ").strip()
        if title:
            label = f"{author} ({year}). *{title}.*"
        else:
            label = f"{author} ({year})."
    else:
        # Fallback: just clean underscores and present as-is
        clean = doc_id.replace("_", " ").strip()
        label = f"*{clean}.*" if clean else doc_id

    if url:
        return f"[{label}]({url})"
    return label


def _stage_and_rewrite_figures(
    body: str,
    *,
    page: Page,
    out_dir: Path,
    corpus_root: Path | None,
    page_url_depth: int,
) -> str:
    """Copy each inline ``![alt](rel)`` figure into ``out/assets/<rel>``
    and rewrite the src to a path relative to the page's HTML location.

    The page lives at ``out_dir/<sub>/<id>.html`` (depth 1), so the
    rewritten src is ``../assets/<rel>``.
    """
    assets_dir = out_dir / "assets"

    def _replace(match: re.Match[str]) -> str:
        alt = match.group(1)
        rel_path = match.group(2).strip()
        if rel_path.startswith(("http://", "https://", "/")):
            return match.group(0)
        # Resolve source candidate locations.
        candidates: list[Path] = []
        if corpus_root is not None:
            candidates.append(corpus_root / rel_path)
        candidates.append(page.path.parent / rel_path)
        for src in candidates:
            if src.is_file():
                dest = assets_dir / rel_path
                if not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                break
        rewritten = ("../" * page_url_depth) + "assets/" + rel_path
        return f"![{alt}]({rewritten})"

    return _FIGURE_REF_RE.sub(_replace, body)


_FIGURE_PLACEHOLDER_RE = re.compile(r"\{\{figure:([A-Za-z0-9_.-]+)\}\}")


def _safe_asset_name(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return clean or "figure"


def _replace_selected_figure_placeholders(
    body: str,
    *,
    page: Page,
    out_dir: Path,
    corpus_root: Path | None,
    page_url_depth: int,
) -> str:
    if not page.figures:
        return body
    by_anchor = {
        str(fig.get("placement_anchor", "")): fig
        for fig in page.figures
        if isinstance(fig, dict)
    }
    assets_dir = out_dir / "assets" / "figures"

    def _replace(match: re.Match[str]) -> str:
        anchor = match.group(1)
        fig = by_anchor.get(anchor)
        if fig is None:
            return match.group(0)
        rel_path = str(fig.get("path") or "").strip().replace("\\", "/")
        src = Path(rel_path)
        if corpus_root is not None and rel_path and not src.is_absolute():
            src = Path(corpus_root) / rel_path
        if not src.is_file():
            return match.group(0)
        suffix = src.suffix or ".png"
        figure_name = _safe_asset_name(str(fig.get("figure_id") or "figure"))
        dest_name = f"{_safe_asset_name(anchor)}-{figure_name}{suffix}"
        dest = assets_dir / dest_name
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        url = ("../" * page_url_depth) + f"assets/figures/{dest_name}"
        caption = escape(str(fig.get("caption") or ""))
        alt = escape(str(fig.get("figure_id") or anchor))
        return (
            f'\n\n<figure class="wiki-figure" id="figure-{escape(anchor)}">'
            f'<img src="{url}" alt="{alt}">'
            f"<figcaption>{caption}</figcaption>"
            "</figure>\n\n"
        )

    return _FIGURE_PLACEHOLDER_RE.sub(_replace, body)


def _build_toc(html: str) -> list[dict[str, str]]:
    toc: list[dict[str, str]] = []
    for match in _HEADING_RE.finditer(html):
        heading_id = match.group(1) or ""
        text = _TAG_RE.sub("", match.group(2)).strip()
        if not heading_id:
            heading_id = _normalize(text)
        toc.append({"id": heading_id, "text": text})
    return toc


# Minimal client-side search; loads search-index.json on focus.
_SEARCH_JS = """\
(function() {
  var idx = null;
  var input = document.getElementById('search-input');
  var results = document.getElementById('search-results');
  if (!input || !results) return;
  function load() {
    if (idx) return Promise.resolve(idx);
    var root = (document.documentElement.getAttribute('data-root') || '');
    return fetch(root + 'search-index.json')
      .then(function(r) { return r.json(); })
      .then(function(d) { idx = d; return d; });
  }
  function run(q) {
    if (!idx || !q) { results.innerHTML = ''; results.style.display = 'none'; return; }
    var ql = q.toLowerCase();
    var hits = idx.filter(function(it) {
      return it.title.toLowerCase().indexOf(ql) !== -1
          || (it.excerpt || '').toLowerCase().indexOf(ql) !== -1;
    }).slice(0, 10);
    if (!hits.length) {
      results.innerHTML = '<div class="search-no-results">No results</div>';
      results.style.display = 'block';
      return;
    }
    var root = document.documentElement.getAttribute('data-root') || '';
    results.innerHTML = hits.map(function(h) {
      return '<a class="search-result" href="' + root + h.url + '">' +
             '<div class="search-result-title">' + h.title + '</div>' +
             '<div class="search-result-excerpt">' + (h.excerpt || '') + '</div></a>';
    }).join('');
    results.style.display = 'block';
  }
  input.addEventListener('focus', load);
  input.addEventListener('input', function() { load().then(function() { run(input.value); }); });
  document.addEventListener('click', function(e) {
    if (!input.contains(e.target) && !results.contains(e.target)) {
      results.style.display = 'none';
    }
  });
})();
"""
