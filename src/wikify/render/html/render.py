"""Static HTML site renderer for a wikify bundle.

Walks a
bundle's ``articles/`` and ``people/`` directories, parses each page
through the canonical ``store.wiki_bundle.parse_page`` parser, runs the
markdown body through ``python-markdown`` (with the ``footnotes``,
``tables``, ``attr_list``, ``def_list``, and ``pymdownx.superfences``
extensions), resolves ``[[wikilinks]]`` against the bundle's
``WikiIndex``, copies inline ``![Figure N](path)`` images into the
output's ``assets/`` tree, and emits one HTML file per page plus an
index landing page.

Special pages (random/recent/categories/domain/metrics) are intentionally
omitted. Categories are derived from ``page_kind`` only.
"""

import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape

from wikify.api import LegacyBundle
from wikify.bundle.wiki.index import WikiIndex, _normalize
from wikify.bundle.wiki.page import Bundle, Page, load_bundle
from wikify.bundle.wiki.page_naming import url_slug
from wikify.ingest.metadata import _is_valid_author

WIKI_NAME = "Wikify Simple"

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
    bundle: LegacyBundle | Bundle,
    out_dir: Path,
    *,
    corpus_root: Path | None = None,
) -> Path:
    """Render a wikify bundle to a static HTML site under ``out_dir``.

    Accepts either a ``LegacyBundle`` (which we then load) or a
    pre-loaded ``Bundle``. Returns ``out_dir``.
    """
    if isinstance(bundle, LegacyBundle):
        loaded = load_bundle(bundle.root)
        wiki_index = WikiIndex.load(bundle)
    else:
        loaded = bundle
        wiki_index = WikiIndex.load(LegacyBundle(root=loaded.root))

    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "static").mkdir(exist_ok=True)
    (out_dir / "assets").mkdir(exist_ok=True)

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
    stats = {
        "total_articles": len(page_views),
        "total_concepts": len(concepts),
        "total_people": len(people),
    }
    shared_ctx = {
        "wiki_name": WIKI_NAME,
        "stats": stats,
        "concepts": concepts,
        "people": people,
    }

    slug_to_url = {pv.id: pv.url for pv in page_views}
    alias_to_id = {_normalize(name): pv.id for pv in page_views for name in (pv.title, *pv.aliases)}

    # Build a lookup from page_view id to the source Page object.
    page_by_id = {p.id: p for p in loaded.pages}

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

    # Wiki index projection from the bundle, for completeness.
    if wiki_index is not None and len(wiki_index) > 0:
        # No-op: WikiIndex is consulted via alias_to_id above. We keep
        # the load() call so a stale on-disk index is rebuilt as a side
        # effect.
        pass

    return out_dir


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
                excerpt = stripped[:200]
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

    # Clean up evidence footnote lines: format as bibliographic references.
    body_md = _clean_evidence_lines(body_md)

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
    return template.render(
        title=pv.title,
        aliases=pv.aliases,
        content=body_html,
        toc=toc,
        categories=categories,
        see_also=see_also[:10],  # cap at 10 links
        infobox=infobox if infobox else None,
        root=root,
        **shared_ctx,
    )


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


def _clean_evidence_lines(body: str) -> str:
    """Reformat evidence footnote definitions as bibliographic references.

    Transforms raw evidence like:
        ``[^e1]: chunk_hash (doc_id) > "quote"``
    into clean references like:
        ``[^e1]: Author (Year). *Paper Title.* "quote"``
    """
    lines = body.split("\n")
    out: list[str] = []
    for line in lines:
        if line.startswith("[^") and "]:" in line:
            line = _CHUNK_HASH_RE.sub("", line)
            line = _format_evidence_as_reference(line)
        out.append(line)
    return "\n".join(out)


def _format_evidence_as_reference(line: str) -> str:
    """Format a single evidence footnote line as a bibliographic reference."""
    # Extract marker prefix
    marker_end = line.index("]:") + 2
    marker = line[:marker_end]
    rest = line[marker_end:].strip()

    # Parse the evidence value: look for ' > "quote"'
    sep = rest.find(' > "')
    if sep == -1:
        # No quote separator -- just clean up what we have
        return f"{marker} {_format_doc_id(rest)}"

    head = rest[:sep].strip()
    quote = rest[sep + 4 :].rstrip('"').strip()

    # Head may be "chunk_id (doc_id)" or just "doc_id"
    doc_id = head
    paren_open = head.rfind("(")
    paren_close = head.rfind(")")
    if paren_open > 0 and paren_close > paren_open:
        doc_id = head[paren_open + 1 : paren_close].strip()

    formatted = _format_doc_id(doc_id)
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


def _format_doc_id(doc_id: str) -> str:
    """Turn a doc_id like '[2020 Liu] Paper Title_hash' into 'Liu (2020). *Paper Title*.'"""
    # Strip chunk and content hash suffixes
    doc_id = _CHUNK_HASH_RE.sub("", doc_id).strip().rstrip("_")
    doc_id = _TRAILING_HASH_RE.sub("", doc_id).strip().rstrip("_")

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
            return f"{author} ({year}). *{title}.*"
        return f"{author} ({year})."

    # Fallback: just clean underscores and present as-is
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
