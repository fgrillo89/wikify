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
from wikify.corpus.handles import (
    AmbiguousHandleError,
    HandleIndex,
    build_index,
    short_id,
    try_resolve,
)
from wikify.ingest.metadata import _is_valid_author
from wikify.render.html.citation import format_cs1

DEFAULT_WIKI_NAME = "ScholarForge"

# Tokens stripped when deriving a wiki name from a corpus directory basename.
# Parser/date noise that shouldn't show up in the displayed name.
_CORPUS_NAME_STOPWORDS = frozenset(
    {"docling", "marker", "lite", "all", "default", "rechunked", "test", "validation"}
)
# Short alphabetic tokens (<=4 chars) are rendered ALL CAPS so e.g. "ald" -> "ALD".
_ALLCAPS_MAX_LEN = 4

_NORM_RE = re.compile(r"[^a-z0-9]+")


def derive_wiki_name(corpus_root: Path | None) -> str:
    """Derive a display name from a corpus path; fall back to DEFAULT_WIKI_NAME.

    ``data/corpora/ald_docling_2026_05_15`` -> ``"ALD Wiki"``.
    Strips 4-digit years, 1-2 digit numerics, and parser-name stopwords;
    upper-cases short alphabetic tokens (likely acronyms) and title-cases
    the rest. Appends "Wiki" so the header reads as a wiki name.
    """
    if corpus_root is None:
        return DEFAULT_WIKI_NAME
    basename = Path(corpus_root).name
    tokens: list[str] = []
    for tok in re.split(r"[_\-\s]+", basename):
        if not tok:
            continue
        if tok.isdigit():
            continue
        if tok.lower() in _CORPUS_NAME_STOPWORDS:
            continue
        if tok.isalpha() and len(tok) <= _ALLCAPS_MAX_LEN:
            tokens.append(tok.upper())
        else:
            tokens.append(tok[:1].upper() + tok[1:])
    if not tokens:
        return DEFAULT_WIKI_NAME
    return " ".join(tokens) + " Wiki"


def _normalize(s: str) -> str:
    return _NORM_RE.sub("-", s.lower()).strip("-")


def _doc_key(doc_id: str) -> str:
    """Normalize a doc id to its short identity for cross-page matching.

    Strips a ``doc:``/``chunk:`` handle prefix and reduces to the doc
    short-id, so the handle form (``doc:2c89…``) and the canonical form
    (``[2023 Kumar] …_2c89…``) of the same document compare equal.
    """
    s = doc_id.split(":", 1)[1] if doc_id.startswith(("doc:", "chunk:")) else doc_id
    return short_id(s)


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
    wiki_name: str | None = None,
) -> Path:
    """Render a wiki bundle to a static HTML site under ``out_dir``.

    Takes a pre-loaded ``Bundle`` (the wiki-bundle view of
    ``<bundle>/wiki/``). ``wiki_name`` overrides the header/title text;
    when ``None`` the name is derived from ``corpus_root``'s basename.
    Returns ``out_dir``.
    """
    loaded = bundle
    resolved_wiki_name = wiki_name if wiki_name else derive_wiki_name(corpus_root)

    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "static").mkdir(exist_ok=True)
    (out_dir / "assets").mkdir(exist_ok=True)

    # Load doc_id -> bibliographic metadata + source URL from the corpus
    # and stage cited PDFs into ``assets/sources/`` so the rendered
    # reference list can hyperlink straight to the paper.
    doc_meta_map, doc_index = _load_doc_meta_map(corpus_root, out_dir)

    # Load the corpus image index once so _render_article can inject a
    # fallback figure for pages whose body has no embedded figures yet.
    image_index = _load_image_index(corpus_root)

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
        if (pv.kind == "data" or pv.has_prose)
        and not (pv.kind == "person" and not _is_valid_author(pv.title))
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
    data_artifacts = sorted(
        [pv for pv in page_views if pv.kind == "data"],
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
        "wiki_name": resolved_wiki_name,
        "stats": stats,
        "concepts": concepts,
        "people": people,
        "data_artifacts": data_artifacts,
        "navigation": navigation,
        "key_articles": key_articles,
    }

    slug_to_url = {pv.id: pv.url for pv in page_views}
    alias_to_id = {_normalize(name): pv.id for pv in page_views for name in (pv.title, *pv.aliases)}

    # Tracks which fallback figure ids have already been assigned so that
    # multiple pages sharing a high-rank evidence doc get distinct figures.
    used_figure_ids: set[str] = set()

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
            doc_meta_map=doc_meta_map,
            doc_index=doc_index,
            image_index=image_index,
            used_figure_ids=used_figure_ids,
        )
        html_path.write_text(html_str, encoding="utf-8")

    # Index page lives at the site root.
    index_html = env.get_template("index_page.html").render(
        title="Main Page",
        root="",
        **shared_ctx,
    )
    (out_dir / "index.html").write_text(index_html, encoding="utf-8")

    # References page: aggregate every cited doc across all pages.
    references = _aggregate_references(
        pages=[page_by_id[pv.id] for pv in page_views if pv.id in page_by_id],
        page_views={pv.id: pv for pv in page_views},
        doc_meta_map=doc_meta_map,
        doc_index=doc_index,
    )
    refs_html = env.get_template("references.html").render(
        title="References",
        root="",
        references=references,
        **shared_ctx,
    )
    (out_dir / "references.html").write_text(refs_html, encoding="utf-8")

    # Article graph: D3 force-directed view of wikilinks between pages.
    if page_views:
        graph_data = _build_article_graph_data(
            page_views={pv.id: pv for pv in page_views},
            page_by_id=page_by_id,
        )
        graph_html = env.get_template("graph.html").render(
            title="Article graph",
            root="",
            graph_data=graph_data,
            **shared_ctx,
        )
        (out_dir / "graph.html").write_text(graph_html, encoding="utf-8")

    # Static assets: CSS + search.js stub.
    shutil.copy2(_TEMPLATES_DIR / "wiki.css", out_dir / "static" / "wiki.css")
    (out_dir / "static" / "search.js").write_text(_SEARCH_JS, encoding="utf-8")

    # Search index sidecar (title + url + first paragraph), emitted as a JS
    # file that assigns a global. Loaded via a <script> tag rather than
    # fetched, so search works when the site is opened from disk (file://).
    search_index = [{"title": pv.title, "url": pv.url, "excerpt": pv.excerpt} for pv in page_views]
    (out_dir / "static" / "search-index.js").write_text(
        "window.__WIKI_SEARCH_INDEX__ = "
        + json.dumps(search_index, ensure_ascii=False)
        + ";\n",
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
    corpus_doc_count: int | None = None
    if corpus_root is not None:
        corpus_stats = _corpus_used_doc_stats(Path(corpus_root), used_docs)
        if corpus_stats["years"]:
            years = corpus_stats["years"]
        words_processed = corpus_stats["words"]
        corpus_doc_count = corpus_stats.get("total_docs")
    date_range = ""
    if years:
        date_range = f"{min(years)}-{max(years)}"
    return {
        "total_articles": len(pages),
        "total_concepts": len(concepts),
        "total_people": len(people),
        "source_articles_used": len(used_docs),
        "corpus_doc_count": corpus_doc_count,
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
        return {"years": [], "words": None, "total_docs": None}
    import sqlite3 as _sqlite

    con = _sqlite.connect(str(db_path))
    con.row_factory = _sqlite.Row
    try:
        # Fetch all full doc_ids from the corpus so we can resolve short handles.
        all_full_ids = [r[0] for r in con.execute("SELECT doc_id FROM documents")]
        total_docs: int | None = len(all_full_ids) if all_full_ids else None

        # Resolve short ``doc:<hex>`` handles to full doc_ids for SQL queries.
        # Evidence records store the handle form; the documents table uses full ids.
        full_ids: list[str] = []
        suffix_map: dict[str, str] = {fid[-12:]: fid for fid in all_full_ids if len(fid) >= 12}
        for did in doc_ids:
            short = did[4:] if did.startswith("doc:") else did
            if short in all_full_ids:
                full_ids.append(short)
            elif short in suffix_map:
                full_ids.append(suffix_map[short])
            # If not resolvable, skip — don't inject unmatched ids into the query.

        years: list[int] = []
        words = 0
        if full_ids:
            placeholders = ",".join("?" * len(full_ids))
            for r in con.execute(
                f"SELECT year FROM documents WHERE doc_id IN ({placeholders})",
                full_ids,
            ):
                if r["year"]:
                    years.append(int(r["year"]))
            for r in con.execute(
                f"SELECT text FROM chunks WHERE doc_id IN ({placeholders})",
                full_ids,
            ):
                words += len(re.findall(r"\b\w+\b", r["text"] or ""))
    finally:
        con.close()
    return {"years": years, "words": words or None, "total_docs": total_docs}


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


def _aggregate_references(
    *,
    pages: list[Page],
    page_views: dict[str, "_PageView"],
    doc_meta_map: dict[str, dict],
    doc_index: HandleIndex | None = None,
) -> list[dict[str, Any]]:
    """Aggregate every cited doc into a CS1-formatted reference list.

    Each entry carries ``citation_html`` (CS1 markdown converted to HTML),
    a sorted list of pages that cite the doc, and a sort key derived
    from first-author surname + year. Docs cited by zero pages are
    omitted; docs missing from ``doc_meta_map`` render as a bare
    ``doc_id`` so nothing silently disappears.

    Evidence carries the short ``doc:<hex>`` handle, while
    ``doc_meta_map`` is keyed by the full canonical doc_id; resolve each
    handle through ``doc_index`` so the CS1 metadata is found (otherwise
    every entry degrades to a raw doc-id code span).
    """
    doc_to_pages: dict[str, set[str]] = {}
    for page in pages:
        for ev in page.evidence:
            if not ev.doc_id:
                continue
            raw = ev.doc_id
            bare = raw[4:] if raw.startswith("doc:") else raw
            full = raw
            if doc_index is not None:
                try:
                    resolved = try_resolve(bare, doc_index)
                except AmbiguousHandleError:
                    resolved = None
                if resolved is not None:
                    full = resolved
            doc_to_pages.setdefault(full, set()).add(page.id)

    out: list[dict[str, Any]] = []
    md = markdown.Markdown(extensions=["smarty"])
    for doc_id, citing_page_ids in doc_to_pages.items():
        meta = doc_meta_map.get(doc_id, {})
        # Rewrite the meta URL: _load_doc_meta_map emits "../assets/sources/X"
        # (relative to per-page HTML one level deep). References lives at the
        # site root, so drop one "../".
        url = meta.get("url", "")
        if url.startswith("../"):
            url = url[3:]
        if meta:
            citation_md = format_cs1(meta, url=url)
            citation_html = md.reset().convert(citation_md)
        else:
            citation_html = f"<code>{escape(doc_id)}</code>"
        cited_in = sorted(
            [
                {"title": page_views[pid].title, "url": page_views[pid].url}
                for pid in citing_page_ids
                if pid in page_views
            ],
            key=lambda c: c["title"].lower(),
        )
        sort_key = _reference_sort_key(meta, doc_id)
        out.append(
            {
                "doc_id": doc_id,
                "citation_html": citation_html,
                "cited_in": cited_in,
                "_sort": sort_key,
            }
        )
    out.sort(key=lambda r: r["_sort"])
    for ref in out:
        del ref["_sort"]
    return out


def _reference_sort_key(meta: dict, doc_id: str) -> tuple[str, str]:
    """Surname-then-year sort key. Falls back to doc_id when meta is empty."""
    authors = meta.get("authors") or []
    if authors:
        first = str(authors[0])
        surname = first.split(",", 1)[0] if "," in first else first.split()[-1]
        return (surname.lower(), str(meta.get("year") or ""))
    title = (meta.get("title") or "").strip()
    return (title.lower() or doc_id.lower(), "")


def _build_article_graph_data(
    *,
    page_views: dict[str, "_PageView"],
    page_by_id: dict[str, Page],
) -> dict[str, list[dict[str, Any]]]:
    """Build a D3 force-directed graph of article-article wikilinks.

    Nodes: every page (article + person). Edges: undirected pairs derived
    from each page's ``links`` field, deduplicated and weighted by the
    directional link count (1 = one-way crosslink, 2 = mutual).
    Self-links and links to pages not in the rendered set are dropped.
    """
    nodes = [
        {"id": pv.id, "label": pv.title, "type": pv.kind, "url": pv.url}
        for pv in page_views.values()
    ]
    valid = set(page_views)
    pair_weights: dict[tuple[str, str], int] = {}
    for pid, pv in page_views.items():
        page = page_by_id.get(pid)
        if page is None:
            continue
        for target in page.links:
            if target == pid or target not in valid:
                continue
            key = tuple(sorted([pid, target]))
            pair_weights[key] = pair_weights.get(key, 0) + 1
    links = [
        {"source": a, "target": b, "weight": w}
        for (a, b), w in pair_weights.items()
    ]
    return {"nodes": nodes, "links": links}


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
        sub = {"article": "articles", "person": "people", "data": "data"}.get(
            page.kind, "articles"
        )
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


def _load_image_index(corpus_root: Path | None) -> "Any | None":
    """Load the corpus image index; return ``None`` if unavailable."""
    if corpus_root is None:
        return None
    try:
        from wikify.api import Corpus
        from wikify.corpus.images_index import ImageIndex

        corpus = Corpus(root=Path(corpus_root))
        return ImageIndex.load(corpus)
    except Exception:
        return None


# Decoration thresholds mirror images_index.is_decoration_dims so that
# the fallback injector skips the same publisher banners the builder skips.
_RENDER_DECORATION_MIN_SHORT = 120
_RENDER_DECORATION_MIN_AREA = 40_000
_RENDER_DECORATION_MAX_ASPECT = 4.0


def _body_has_figure(body_md: str) -> bool:
    """True when the body already carries a figure of any form.

    A figure may be present as a markdown image (``![](...)``) or as an
    HTML ``<figure>`` block emitted by the selected-figure placeholder
    pass. Either must suppress the fallback so a page never shows two
    figures.
    """
    return bool(_FIGURE_REF_RE.search(body_md)) or "<figure" in body_md


def _inject_fallback_figure(
    body_md: str,
    *,
    page: "Page",
    out_dir: Path,
    image_index: Any,
    page_url_depth: int,
    used_figure_ids: "set[str] | None" = None,
) -> str:
    """Inject the top captioned corpus figure after the first paragraph.

    Called only when the page body has no inline figure references and the
    corpus image index is available. Picks the first captioned, non-decoration
    figure whose doc_id matches one of the page's evidence records and whose
    id has not already been used as a fallback on another page this render run.
    When a figure is chosen its id is added to ``used_figure_ids`` so
    subsequent pages skip it. Copies the image into ``assets/figures/`` and
    emits an HTML ``<figure>`` block. Degrades cleanly (returns ``body_md``
    unchanged) when no suitable figure is found or the file cannot be copied.
    """
    # Build a set of full doc_ids from evidence (handles short ``doc:<hex>`` forms).
    doc_keys = list(image_index.by_doc.keys())
    suffix_map: dict[str, str] = {
        k[-12:]: k for k in doc_keys if len(k) >= 12
    }
    candidate_docs: list[str] = []
    for ev in page.evidence:
        did = ev.doc_id or ""
        short = did[4:] if did.startswith("doc:") else did
        if short in doc_keys:
            candidate_docs.append(short)
        elif short in suffix_map:
            candidate_docs.append(suffix_map[short])

    # Pick the first captioned, non-decoration figure from those docs
    # that has not already been used as a fallback for another page.
    chosen = None
    for doc_id in candidate_docs:
        for img in image_index.for_doc(doc_id):
            if not img.caption or not img.path:
                continue
            if used_figure_ids is not None and img.id in used_figure_ids:
                continue
            w, h = img.width, img.height
            if w is None or h is None:
                # Try to read dims from file.
                abs_src = image_index.corpus_root / img.path
                if abs_src.is_file():
                    try:
                        from PIL import Image as _PilImage
                        with _PilImage.open(abs_src) as im:
                            w, h = im.size
                    except Exception:
                        pass
            if w is not None and h is not None:
                short_side = min(w, h)
                if (
                    short_side < _RENDER_DECORATION_MIN_SHORT
                    or w * h < _RENDER_DECORATION_MIN_AREA
                    or max(w, h) / max(short_side, 1) > _RENDER_DECORATION_MAX_ASPECT
                ):
                    continue
            src = image_index.corpus_root / img.path
            if not src.is_file():
                continue
            chosen = img
            break
        if chosen is not None:
            break

    if chosen is None:
        return body_md

    # Register the chosen figure so other pages in this render run skip it.
    if used_figure_ids is not None:
        used_figure_ids.add(chosen.id)

    # Copy the file into assets/figures/.
    suffix = src.suffix or ".png"
    dest_name = _safe_asset_name(chosen.id.replace("/", "--")) + suffix
    assets_dir = out_dir / "assets" / "figures"
    dest = assets_dir / dest_name
    if not dest.exists():
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        except OSError:
            return body_md

    url = ("../" * page_url_depth) + f"assets/figures/{dest_name}"
    caption = escape(chosen.caption)
    if chosen.label:
        caption = escape(chosen.label) + ": " + caption
    alt = escape(_figure_alt_text(chosen.label or "", chosen.caption or ""))
    figure_html = (
        f'\n\n<figure class="wiki-figure wiki-figure-fallback">'
        f'<img src="{url}" alt="{alt}">'
        f"<figcaption>{caption}</figcaption>"
        "</figure>\n\n"
    )

    # Insert after the first non-empty paragraph (ends at first double newline).
    first_para_end = body_md.find("\n\n")
    if first_para_end == -1:
        return body_md + figure_html
    return body_md[: first_para_end + 2] + figure_html + body_md[first_para_end + 2 :]


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
    doc_meta_map: dict[str, dict] | None = None,
    doc_index: HandleIndex | None = None,
    image_index: "Any | None" = None,
    used_figure_ids: "set[str] | None" = None,
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

    # Register this page's writer-embedded figures so a LATER page's
    # fallback never reuses a figure already shown on this page. Without
    # this, a page that embeds figure X and a page with no embedded figure
    # could both display X (embedded-vs-fallback collision).
    if used_figure_ids is not None and _body_has_figure(body_md):
        for fig in page.figures or []:
            if isinstance(fig, dict):
                fid = str(fig.get("figure_id") or "").strip()
                if fid:
                    used_figure_ids.add(fid)

    # Fallback: if the body has no embedded figures, inject the top captioned
    # figure from the evidence docs so older committed pages (built before the
    # figure pipeline fix) still render with at least one image.
    if image_index is not None and not _body_has_figure(body_md):
        body_md = _inject_fallback_figure(
            body_md,
            page=page,
            out_dir=out_dir,
            image_index=image_index,
            page_url_depth=1,
            used_figure_ids=used_figure_ids,
        )

    # Isolate ``$$...$$`` display math so arithmatex's BlockProcessor
    # picks it up. Without blank lines around the block, arithmatex
    # falls back to inline matching and produces stray ``$`` outside
    # the rendered span.
    body_md = _isolate_display_math(body_md)

    # Clean up evidence footnote lines: format as bibliographic references.
    body_md = _clean_evidence_lines(body_md, doc_meta_map=doc_meta_map, doc_index=doc_index)

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
    body_html = _remap_figure_citation_numbers(body_html, page_id=pv.id)

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

    # Related data artifacts: surface every data-artifact page that draws on a
    # source this page also cites, so the cross-source comparison table is one
    # click away and a data artifact is discoverable like a concept. Computed
    # from evidence-source overlap (normalized to the doc short-id so handle
    # and canonical forms unify), so it stays correct as data grows.
    related_data = []
    if pv.kind != "data":
        page_doc_keys = {_doc_key(ev.doc_id) for ev in page.evidence if ev.doc_id}
        for cand in shared_ctx.get("data_artifacts", []):
            cand_page = page_by_id.get(cand.id)
            if not cand_page:
                continue
            cand_keys = {_doc_key(ev.doc_id) for ev in cand_page.evidence if ev.doc_id}
            if page_doc_keys & cand_keys:
                related_data.append({"title": cand.title, "url": cand.url})

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
    elif pv.kind == "data":
        infobox["Type"] = "Data table"
        if pv.n_evidence:
            infobox["Sources"] = str(pv.n_evidence)

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
        related_data=related_data[:6],
        infobox=infobox if infobox else None,
        root=root,
        **shared_ctx,
    )


def _load_doc_meta_map(
    corpus_root: Path | None, out_dir: Path,
) -> tuple[dict[str, dict], HandleIndex]:
    """Return ``({doc_id: meta}, HandleIndex)`` for every doc in the corpus.

    ``meta`` carries the fields needed for a Wikipedia CS1 citation
    (``authors``, ``year``, ``title``, ``venue``, ``volume``, ``issue``,
    ``pages``, ``doi``) plus a ``url`` field with the click target.

    URL preference per doc:

    1. ``doi`` -> ``https://doi.org/<doi>`` (always portable).
    2. ``source_path`` -> original PDF, copied into
       ``<out_dir>/assets/sources/`` so the wiki carries the file and
       the link is a stable relative path.

    The ``HandleIndex`` is built over the full doc_id keys so callers
    can resolve short handles (``doc:<hex>`` or bare ``<hex>``) to a
    full doc_id without storing aliases in the dict.

    Returns empty collections when no corpus is provided or the corpus
    has no SQLite store.
    """
    if corpus_root is None:
        return {}, HandleIndex()
    db_path = Path(corpus_root) / "wikify.db"
    if not db_path.is_file():
        return {}, HandleIndex()
    import sqlite3 as _sqlite

    sources_dir = out_dir / "assets" / "sources"
    out: dict[str, dict] = {}
    con = _sqlite.connect(str(db_path))
    con.row_factory = _sqlite.Row
    try:
        rows = list(con.execute(
            "SELECT doc_id, source_path, doi, title, year, container_title, "
            "authors_json, metadata_json FROM documents"
        ))
    finally:
        con.close()
    for r in rows:
        doc_id = r["doc_id"]
        doi = (r["doi"] or "").strip()
        url = ""
        if doi:
            url = f"https://doi.org/{doi}"
        else:
            source_path = (r["source_path"] or "").strip()
            if source_path:
                # source_path may be Windows-style (backslashes) from older ingests.
                src = Path(source_path.replace("\\", "/"))
                if not src.is_absolute() and not src.is_file():
                    alt = Path(corpus_root).parent.parent / src
                    if alt.is_file():
                        src = alt
                if src.is_file():
                    short = doc_id[-12:] if len(doc_id) > 12 else doc_id
                    suffix = src.suffix or ".pdf"
                    dest = sources_dir / f"{short}{suffix}"
                    if not dest.exists():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            shutil.copy2(src, dest)
                        except OSError:
                            dest = None  # type: ignore[assignment]
                    if dest is not None and dest.exists():
                        # Per-page HTML lives at out_dir/<sub>/<id>.html,
                        # so assets/sources/<short>.pdf is one level up.
                        url = f"../assets/sources/{short}{suffix}"

        meta: dict = {
            "url": url,
            "authors": _safe_json_list(r["authors_json"]),
            "year": r["year"],
            "title": (r["title"] or "").strip(),
            "venue": (r["container_title"] or "").strip(),
            "doi": doi,
        }
        # Volume/issue/pages live in metadata_json (the OpenAlex/Crossref
        # blob); pull them out so CS1 can render the locator line.
        extra = _safe_json_dict(r["metadata_json"])
        for key in ("volume", "issue", "pages"):
            value = extra.get(key)
            if value:
                meta[key] = str(value).strip()
        out[doc_id] = meta
    doc_index = build_index(out.keys())
    return out, doc_index


def _safe_json_list(raw) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(str(raw))
    except (ValueError, json.JSONDecodeError):
        return []
    return [str(v) for v in data] if isinstance(data, list) else []


def _safe_json_dict(raw) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
    except (ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


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


# Matches the internal chunk-id suffix, e.g. ``__c0000_fec9f3fb`` or
# ``__c0000__fec9f3fb`` at the end of a chunk_id. The hyphen-vs-double
# underscore between the chunk index and the hash has varied across
# ingest paths; both forms are accepted so legacy bundles also clean up.
_CHUNK_HASH_RE = re.compile(r"__c\d{4}_+[0-9a-f]{6,}")

# Extracts [Year Author] prefix from doc_id, e.g. "[2020 Liu]"
_DOC_YEAR_RE = re.compile(r"\[(\d{4})\s+([^\]]+)\]")


def _clean_evidence_lines(
    body: str,
    *,
    doc_meta_map: dict[str, dict] | None = None,
    doc_index: HandleIndex | None = None,
) -> str:
    """Reformat evidence footnote definitions as Wikipedia CS1 citations.

    Transforms raw evidence like:
        ``[^e1]: chunk_hash (doc_id) > "quote"``
    into a CS1-style reference like:
        ``[^e1]: Last, F. M. (Year). "[Title](url)". *Venue*. **Vol** (Issue): pages. doi:X``

    The trailing chunk-text quote is dropped -- it was the first ~240
    chars of the chunk (build-evidence stores ``text[:400]``), which
    rarely matches what the writer cited. The upstream ``quote`` field
    on each evidence record stays -- the M6/G2 grounding metric
    substring-matches it against the chunk text.

    Two markers that resolve to the same paper are collapsed into one
    definition, with the prose-first marker kept as canonical. The
    duplicate uses in prose are rewritten to the canonical marker so
    python-markdown's footnotes extension emits a single ``<li>`` with
    multiple backref arrows.

    Definitions are emitted in the order they first appear in prose so
    the rendered reference list reads 1, 2, 3 top to bottom.
    """
    # First pass: classify every line as either a footnote definition or
    # body prose. For each definition, capture ``(doc_id, citation_text)``;
    # the doc_id (normalized through the meta map's DOI when available)
    # is the stable identity used for dedup, not the rendered text.
    parsed: dict[str, tuple[str, str]] = {}
    body_lines: list[str] = []
    in_def_block = False
    for line in body.split("\n"):
        if line.startswith("[^") and "]:" in line:
            in_def_block = True
            marker = line[2:line.index("]:")]
            cleaned = _CHUNK_HASH_RE.sub("", line)
            parsed[marker] = _format_evidence_body(
                cleaned, doc_meta_map=doc_meta_map, doc_index=doc_index,
            )
            continue
        if in_def_block and not line.strip():
            # Blank line between footnote definitions stays in the prose
            # buffer; we re-emit footnotes in a single block at the end.
            continue
        in_def_block = False
        body_lines.append(line)

    if not parsed:
        return "\n".join(body_lines)

    # Resolve each marker to a stable identity: DOI first when present
    # (so two doc_ids that share a DOI collapse), else the doc_id, else
    # the rendered citation text as a last-resort fallback.
    def _identity(doc_id: str, citation: str) -> str:
        meta = (doc_meta_map or {}).get(doc_id) or {}
        doi = (meta.get("doi") or "").strip().lower()
        if doi:
            return f"doi:{doi}"
        if doc_id:
            return f"id:{doc_id}"
        return f"text:{citation}"

    # Pick the prose-first marker per identity as the canonical one,
    # then rewrite uses of duplicates to the canonical marker. The python-
    # markdown footnotes extension naturally folds multiple ``[^eN]`` uses
    # of the same marker into one ``<li>`` with multiple backrefs.
    prose = "\n".join(body_lines)
    prose_order: list[str] = []
    seen_in_prose: set[str] = set()
    for m in _MARKER_USE_RE.finditer(prose):
        marker = m.group(1)
        if marker in parsed and marker not in seen_in_prose:
            prose_order.append(marker)
            seen_in_prose.add(marker)
    canonical_for_identity: dict[str, str] = {}
    canonical_per_marker: dict[str, str] = {}
    for marker in prose_order:
        doc_id, citation = parsed[marker]
        ident = _identity(doc_id, citation)
        canonical = canonical_for_identity.setdefault(ident, marker)
        canonical_per_marker[marker] = canonical
    # Markers defined but never cited keep themselves as canonical so
    # they still emit (they fall through the rewrite below untouched).
    for marker, (doc_id, citation) in parsed.items():
        canonical_per_marker.setdefault(marker, marker)
        canonical_for_identity.setdefault(_identity(doc_id, citation), marker)

    if any(c != m for m, c in canonical_per_marker.items()):
        def _rewrite_use(match: re.Match[str]) -> str:
            m = match.group(1)
            return f"[^{canonical_per_marker.get(m, m)}]"

        prose = _MARKER_USE_RE.sub(_rewrite_use, prose)

    # Emit one definition per canonical marker, in prose-first order.
    emitted_order: list[str] = []
    emitted: set[str] = set()
    for marker in prose_order:
        canonical = canonical_per_marker[marker]
        if canonical not in emitted:
            emitted_order.append(canonical)
            emitted.add(canonical)
    for marker in sorted(parsed):
        canonical = canonical_per_marker[marker]
        if canonical not in emitted:
            emitted_order.append(canonical)
            emitted.add(canonical)

    out = prose.split("\n")
    if out and out[-1].strip():
        out.append("")
    for marker in emitted_order:
        _doc_id, citation = parsed[marker]
        out.append(f"[^{marker}]: {citation}")
    return "\n".join(out)


_MARKER_USE_RE = re.compile(r"\[\^([^\]]+)\](?!:)")

# ``$$...$$`` display math, including multi-line. Each delimiter pair
# requires no flanking ``$`` on either side so ``$$$$x$$$$`` (a stray
# quad-dollar run) doesn't get swallowed as one block.
_DISPLAY_MATH_RE = re.compile(
    r"(?<!\$)\$\$(?!\$)(.+?)(?<!\$)\$\$(?!\$)", re.DOTALL,
)

# Fenced code blocks: matched as a whole so ``$$...$$`` inside code
# never participates in display-math isolation. Both backtick (```)
# and tilde (~~~) fences are recognized; the closing fence must use
# the same character at the same column.
_FENCED_CODE_RE = re.compile(
    r"(?ms)^(?P<fence>`{3,}|~{3,})[^\n]*\n.*?^(?P=fence)\s*$",
)


def _isolate_display_math(body: str) -> str:
    """Insert blank lines around ``$$...$$`` blocks.

    pymdownx.arithmatex's BlockProcessor only picks up display math
    when the ``$$`` delimiters sit in their own paragraph. Writers
    typically emit ``...sentence:\n$$eq$$\nnext sentence...`` which
    leaves the block adjacent to prose; arithmatex then falls back
    to inline matching and produces stray ``$`` glyphs flanking the
    rendered span.

    This pass surrounds every ``$$...$$`` with blank lines so the
    block matcher always wins. Fenced code blocks are excised before
    the substitution and restored verbatim afterward so example math
    inside a code block isn't rewritten.
    """
    # Replace fenced blocks with placeholders, apply substitution, restore.
    placeholders: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        placeholders.append(match.group(0))
        return f"\x00FENCE{len(placeholders) - 1}\x00"

    stashed = _FENCED_CODE_RE.sub(_stash, body)

    def _sub(match: re.Match[str]) -> str:
        return f"\n\n$$\n{match.group(1).strip()}\n$$\n\n"

    rewritten = _DISPLAY_MATH_RE.sub(_sub, stashed)

    def _restore(match: re.Match[str]) -> str:
        return placeholders[int(match.group(1))]

    return re.sub(r"\x00FENCE(\d+)\x00", _restore, rewritten)


def _format_evidence_body(
    line: str,
    *,
    doc_meta_map: dict[str, dict] | None = None,
    doc_index: HandleIndex | None = None,
) -> tuple[str, str]:
    """Return ``(doc_id, citation_text)`` for one ``[^eN]:`` definition.

    Input shape: ``[^eN]: chunk_id (doc_id) > "quote"``, or
    ``[^eN]: doc_id`` when no wrapper was attached (legacy / hand-
    written pages). The quote is discarded; only the doc_id is used.

    ``doc_id`` is what downstream dedup uses to identify the paper;
    ``citation_text`` is the rendered CS1 string. Decoupling the two
    means two papers that format to identical CS1 text (e.g. missing
    DOI / venue) don't accidentally collapse during dedup.
    """
    marker_end = line.index("]:") + 2
    rest = line[marker_end:].strip()

    # Strip the ' > "quote"' tail if present.
    sep = rest.find(' > "')
    head = rest[:sep].strip() if sep != -1 else rest

    doc_id = _extract_doc_id(head, doc_meta_map=doc_meta_map, doc_index=doc_index)
    return doc_id, _format_citation(doc_id, doc_meta_map=doc_meta_map)


def _extract_doc_id(
    head: str,
    *,
    doc_meta_map: dict[str, dict] | None = None,
    doc_index: HandleIndex | None = None,
) -> str:
    """Pull the doc_id out of a ``chunk_id (doc_id)`` head string.

    The wrapper is extracted via balanced-paren walk so titles
    containing ``(RRAM)`` etc. don't fool the parser. The unwrapped
    form is preferred only when it resolves against the doc index or
    the doc_meta_map; otherwise the entire head is treated as the
    doc_id. This keeps unwrapped legacy lines like
    ``[^e1]: [2020 Smith] Title (RRAM)`` from being mis-parsed as a
    wrapped reference.

    When a ``doc_index`` is supplied, short handles (``doc:<hex>`` or
    bare ``<hex>``) are resolved to the full canonical doc_id so the
    returned value can be used as a key into ``doc_meta_map`` directly.
    """
    if head.endswith(")"):
        depth = 0
        for i in range(len(head) - 1, -1, -1):
            ch = head[i]
            if ch == ")":
                depth += 1
            elif ch == "(":
                depth -= 1
                if depth == 0:
                    candidate = head[i + 1 : -1].strip()
                    if not candidate:
                        break
                    # Resolve candidate: strip optional "doc:" prefix,
                    # try the index first, then fall back to the map.
                    bare = candidate[4:] if candidate.startswith("doc:") else candidate
                    if doc_index is not None:
                        # An ambiguous short handle is not a render error:
                        # fall through to the map lookup / treat the head as
                        # the doc_id rather than crashing the page build.
                        try:
                            resolved = try_resolve(bare, doc_index)
                        except AmbiguousHandleError:
                            resolved = None
                        if resolved is not None:
                            return resolved
                    # Fallback: direct map lookup (covers exact full ids
                    # and legacy bundles without an index).
                    if (doc_meta_map or {}).get(candidate):
                        return candidate
                    break
    return head


def _format_citation(
    doc_id: str, *, doc_meta_map: dict[str, dict] | None = None,
) -> str:
    """Build the citation text for ``doc_id``.

    Prefers DB-backed CS1 rendering when ``doc_meta_map`` carries a
    row for this doc; falls back to ``_format_doc_id`` (legacy
    ``Author (Year). *Title.*`` extracted from the doc_id string) when
    no row is available -- this keeps tests and bundles whose corpus
    isn't available (or whose docs lack metadata) rendering cleanly.
    """
    meta = (doc_meta_map or {}).get(doc_id)
    if meta is None:
        meta = (doc_meta_map or {}).get(_CHUNK_HASH_RE.sub("", doc_id).strip().rstrip("_"))
    if meta and (meta.get("authors") or meta.get("title") or meta.get("year")):
        return format_cs1(meta, url=meta.get("url", ""))
    # Legacy fallback: parse [Year Author] Title from the doc_id string.
    return _format_doc_id(doc_id)


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


def _format_doc_id(doc_id: str) -> str:
    """Legacy fallback: parse ``[Year Author] Title`` from the doc_id string.

    Used only when the corpus has no metadata row for this doc (e.g.
    tests with synthetic doc_ids, or older bundles whose corpus is no
    longer reachable). Produces ``Author (Year). *Title.*``.
    """
    doc_id = _CHUNK_HASH_RE.sub("", doc_id).strip().rstrip("_")
    doc_id = _TRAILING_HASH_RE.sub("", doc_id).strip().rstrip("_")
    m = _DOC_YEAR_RE.match(doc_id)
    if m:
        year = m.group(1)
        author = m.group(2).strip()
        title = doc_id[m.end() :].strip().lstrip("_ ").replace("_", " ")
        title = _TRAILING_HASH_RE.sub("", title).strip().rstrip("_")
        title = title.replace("_", " ").strip()
        if title:
            return f"{author} ({year}). *{title}.*"
        return f"{author} ({year})."
    clean = doc_id.replace("_", " ").strip()
    return f"*{clean}.*" if clean else doc_id


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

# Matches body footnote refs emitted by python-markdown's footnotes extension:
#   <a class="footnote-ref" href="#fn:eN">3</a>
_FNREF_BODY_RE = re.compile(
    r'<a class="footnote-ref" href="#fn:([^"]+)">(\d+)</a>'
)
# Matches the display text inside a figure-citation sup that we want to renumber:
#   <a href="#fn:eN">[eN]</a>
_FNREF_FIG_RE = re.compile(
    r'(<sup class="figure-citation"><a href="#fn:([^"]+)">\[)([^\]]+)(\]</a></sup>)'
)


def _safe_asset_name(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return clean or "figure"


def _figure_alt_text(label: str, caption: str) -> str:
    """Human-readable alt text for a figure.

    Prefers a short label; else a length-bounded plain-text slice of the
    caption; else a generic fallback. Never the raw figure id (a doc-hex
    path is useless to a screen reader)."""
    label = (label or "").strip()
    if label:
        return label
    caption = " ".join((caption or "").split())  # collapse whitespace
    if caption:
        return caption[:160].rstrip() + ("..." if len(caption) > 160 else "")
    return "Figure"


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
        marker = str(fig.get("source_marker") or "").strip()
        if marker:
            # python-markdown's footnotes extension anchors definitions
            # at ``fn:<id>``; the figure-citation link targets that so a
            # reader can jump from the caption to the source quote.
            caption += (
                f'<sup class="figure-citation">'
                f'<a href="#fn:{escape(marker)}">[{escape(marker)}]</a>'
                f"</sup>"
            )
        alt = escape(
            _figure_alt_text(
                str(fig.get("label") or ""), str(fig.get("caption") or "")
            )
        )
        return (
            f'\n\n<figure class="wiki-figure" id="figure-{escape(anchor)}">'
            f'<img src="{url}" alt="{alt}">'
            f"<figcaption>{caption}</figcaption>"
            "</figure>\n\n"
        )

    return _FIGURE_PLACEHOLDER_RE.sub(_replace, body)


def _remap_figure_citation_numbers(html: str, *, page_id: str = "") -> str:
    """Rewrite ``[eN]`` display text in figure-citation sups to match the
    numeric index assigned by python-markdown's footnotes extension.

    python-markdown renumbers footnote refs sequentially (1, 2, 3…).  The
    body already shows the renumbered integer; figure captions still show the
    raw marker (e.g. ``[e6]``).  This pass scans the body for
    ``<a class="footnote-ref" href="#fn:eN">K</a>`` entries to build a
    marker->number map, then rewrites every
    ``<sup class="figure-citation"><a href="#fn:eN">[eN]</a></sup>``
    using that map.  Markers cited only in figures (no body ref) keep their
    raw display so the link still works; a warning is emitted for each.
    """
    marker_to_num: dict[str, str] = {}
    for m in _FNREF_BODY_RE.finditer(html):
        marker_to_num[m.group(1)] = m.group(2)

    orphan_markers: list[str] = []

    def _replace_fig(m: re.Match) -> str:
        prefix = m.group(1)   # '<sup class="figure-citation"><a href="#fn:eN">['
        marker = m.group(2)   # 'eN'
        _display = m.group(3) # current display (same as marker on first pass)
        suffix = m.group(4)   # ']</a></sup>'
        if marker in marker_to_num:
            return prefix + marker_to_num[marker] + suffix
        # Marker has no body reference — keep raw display, record for warning.
        orphan_markers.append(marker)
        return m.group(0)

    result = _FNREF_FIG_RE.sub(_replace_fig, html)

    if orphan_markers:
        label = f" (page {page_id})" if page_id else ""
        print(
            f"[html] figure-citation marker(s) with no body footnote ref{label}: "
            + ", ".join(sorted(set(orphan_markers)))
            + " -- add inline [^marker] citations in prose to get numeric display",
            file=sys.stderr,
        )

    return result


def _build_toc(html: str) -> list[dict[str, str]]:
    toc: list[dict[str, str]] = []
    for match in _HEADING_RE.finditer(html):
        heading_id = match.group(1) or ""
        text = _TAG_RE.sub("", match.group(2)).strip()
        if not heading_id:
            heading_id = _normalize(text)
        toc.append({"id": heading_id, "text": text})
    return toc


# Minimal client-side search. Reads the index from a global populated by
# the search-index.js sidecar (loaded via <script>), so search works when
# the site is opened directly from disk -- fetch() is blocked over file://.
_SEARCH_JS = """\
(function() {
  var input = document.getElementById('search-input');
  var results = document.getElementById('search-results');
  if (!input || !results) return;
  var idx = window.__WIKI_SEARCH_INDEX__ || [];
  function run(q) {
    if (!q) { results.innerHTML = ''; results.style.display = 'none'; return; }
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
  input.addEventListener('input', function() { run(input.value); });
  document.addEventListener('click', function(e) {
    if (!input.contains(e.target) && !results.contains(e.target)) {
      results.style.display = 'none';
    }
  });
})();
"""
