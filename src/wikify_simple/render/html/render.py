"""Static HTML site renderer for a wikify_simple bundle.

Trimmed port of ``src/wikify/wiki/presentation/html.py``: walks a
bundle's ``concepts/`` and ``people/`` directories, parses each page
through the canonical ``eval.bundle._parse_page`` parser, runs the
markdown body through ``python-markdown`` (with the ``footnotes``,
``tables``, ``attr_list``, ``def_list``, and ``pymdownx.superfences``
extensions), resolves ``[[wikilinks]]`` against the bundle's
``WikiIndex``, copies inline ``![Figure N](path)`` images into the
output's ``assets/`` tree, and emits one HTML file per page plus an
index landing page.

The legacy renderer also emits people/random/recent/categories/domain
pages and a metrics dashboard. wikify_simple has no run metrics on the
bundle and no domain field, so those special pages are intentionally
omitted. Categories are derived from ``page_kind`` only.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ...eval.bundle import Bundle, Page, load_bundle
from ...paths import BundlePaths
from ...store.page_naming import url_slug
from ...store.wiki_index import WikiIndex, _normalize

WIKI_NAME = "Wikify Simple"

_MD_EXTENSIONS = [
    "tables",
    "fenced_code",
    "attr_list",
    "def_list",
    "footnotes",
    "pymdownx.superfences",
]

_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_FIGURE_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_HEADING_RE = re.compile(r'<h2(?:\s+id="([^"]*)")?[^>]*>(.+?)</h2>', re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_TEMPLATES_DIR = Path(__file__).parent / "templates"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_site(
    bundle: BundlePaths | Bundle,
    out_dir: Path,
    *,
    corpus_root: Path | None = None,
) -> Path:
    """Render a wikify_simple bundle to a static HTML site under ``out_dir``.

    Accepts either a ``BundlePaths`` (which we then load) or a
    pre-loaded ``Bundle``. Returns ``out_dir``.
    """
    if isinstance(bundle, BundlePaths):
        loaded = load_bundle(bundle.root)
        wiki_index = WikiIndex.load(bundle)
    else:
        loaded = bundle
        wiki_index = WikiIndex.load(BundlePaths(root=loaded.root))

    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "static").mkdir(exist_ok=True)
    (out_dir / "assets").mkdir(exist_ok=True)

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )

    page_views = [_PageView.from_page(p) for p in loaded.pages]
    concepts = sorted(
        [pv for pv in page_views if pv.kind == "concept"],
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

    for pv, page in zip(page_views, loaded.pages, strict=False):
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

    # Static assets: CSS verbatim from the legacy template, search.js stub.
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

    @classmethod
    def from_page(cls, page: Page) -> _PageView:
        sub = "concepts" if page.kind == "concept" else "people"
        excerpt = ""
        for line in page.body_clean.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("|"):
                excerpt = stripped[:200]
                break
        return cls(
            id=page.id,
            kind=page.kind,
            title=page.title,
            aliases=list(page.aliases),
            url=f"{sub}/{url_slug(page.id)}.html",
            n_evidence=len(page.evidence),
            excerpt=excerpt,
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

    # Resolve [[wikilinks]] BEFORE markdown conversion so they emit
    # plain <a> tags rather than literal "[[...]]" text.
    body_md = _resolve_wikilinks(
        body_md,
        slug_to_url=slug_to_url,
        alias_to_id=alias_to_id,
        root=root,
    )

    md = markdown.Markdown(extensions=_MD_EXTENSIONS)
    body_html = md.convert(body_md)

    toc = _build_toc(body_html)
    categories = [pv.kind]

    template = env.get_template("article.html")
    return template.render(
        title=pv.title,
        aliases=pv.aliases,
        content=body_html,
        toc=toc,
        categories=categories,
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
        return f'<a href="#" class="wiki-redlink" title="Article not found">{text}</a>'

    return _WIKILINK_RE.sub(_replace, body)


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
