"""Static HTML site generator: render wiki markdown into a Wikipedia-style site."""

from __future__ import annotations

import http.server
import json
import logging
import re
import shutil
import socketserver
from pathlib import Path

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape

from wikify.wiki.presentation.layout import iter_visible_page_files, normalize_page_type
from wikify.wiki.builder import read_article_frontmatter, slugify

logger = logging.getLogger(__name__)

# Fields that should never appear in rendered HTML (machine-readable hashes, etc.)
_HIDDEN_FIELDS = frozenset(
    {
        "wiki_id",
        "sources",
        "source_ids",
        "model",
        "concept_type",
    }
)

# Fields to show in the infobox
_INFOBOX_FIELDS = [
    "page_type",
    "domain",
    "domains",
    "status",
    "importance",
    "created",
    "updated",
    "updated_at",
    "confidence",
    "affiliations",
    "formula",
]

WIKI_NAME = "Wikify"

# Markdown extensions for rendering
_MD_EXTENSIONS = [
    "tables",
    "fenced_code",
    "toc",
    "attr_list",
    "def_list",
    "footnotes",
    "pymdownx.superfences",
    "pymdownx.tasklist",
]

_MD_EXTENSION_CONFIGS: dict = {
    "pymdownx.tasklist": {
        "custom_checkbox": True,
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_site(wiki_dir: Path, output_dir: Path | None = None) -> Path:
    """Build static HTML site from wiki markdown files.

    Args:
        wiki_dir: Path to the wiki root (e.g. data/wiki/).
        output_dir: Where to write HTML (default: wiki_dir / "_site").

    Returns:
        Path to the built site directory.
    """
    if output_dir is None:
        output_dir = wiki_dir / "_site"

    output_dir.mkdir(parents=True, exist_ok=True)

    template_dir = Path(__file__).parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html"]),
    )

    # Collect all articles
    slug_map = build_slug_map(wiki_dir)
    articles = _collect_articles(wiki_dir)

    # Derive domains and stats
    domains = _extract_domains(articles)
    stats = _compute_stats(articles, wiki_dir)

    # Shared template context
    shared_ctx = {
        "wiki_name": WIKI_NAME,
        "domains": domains,
        "stats": stats,
    }

    # Render each article
    rendered_count = 0
    for article in articles:
        rel_html = slug_map.get(article["title_lower"], "")
        if not rel_html:
            rel_html = _article_html_relpath(article)

        html_path = output_dir / rel_html
        html_path.parent.mkdir(parents=True, exist_ok=True)

        depth = len(Path(rel_html).parts) - 1
        root = "../" * depth if depth > 0 else ""

        html_content = render_article(article, env, slug_map, root, shared_ctx)
        html_path.write_text(html_content, encoding="utf-8")
        rendered_count += 1

    # Generate special pages
    _write_page(
        output_dir / "index.html",
        generate_main_page(articles, env, shared_ctx),
    )
    _write_page(
        output_dir / "recent.html",
        generate_recent_changes(articles, env, shared_ctx),
    )
    _write_page(
        output_dir / "random.html",
        generate_random_page(articles, env, shared_ctx),
    )
    _write_page(
        output_dir / "categories.html",
        generate_categories_index(articles, env, shared_ctx),
    )
    _write_page(
        output_dir / "people.html",
        generate_people_index(articles, env, shared_ctx),
    )

    # Category pages
    cat_dir = output_dir / "categories"
    cat_dir.mkdir(parents=True, exist_ok=True)
    for slug, html_str in generate_category_pages(articles, env, shared_ctx).items():
        _write_page(cat_dir / f"{slug}.html", html_str)

    # Domain index pages
    for domain in domains:
        domain_dir = output_dir / "domains" / domain["slug"]
        domain_dir.mkdir(parents=True, exist_ok=True)
        domain_articles = [a for a in articles if a.get("domain") == domain["slug"]]
        _write_page(
            domain_dir / "index.html",
            _generate_domain_index(domain, domain_articles, env, shared_ctx),
        )

    # Copy CSS
    css_src = template_dir / "wiki.css"
    shutil.copy2(str(css_src), str(output_dir / "wiki.css"))

    # Copy figures if any exist
    _copy_figures(wiki_dir, output_dir)

    # Build search index
    search_index = _build_search_index(articles, slug_map)
    (output_dir / "search-index.json").write_text(
        json.dumps(search_index, ensure_ascii=False, indent=None),
        encoding="utf-8",
    )

    # Write search JS
    _write_page(output_dir / "search.js", _generate_search_js())

    logger.info("Built %d articles -> %s", rendered_count, output_dir)
    return output_dir


def serve_site(site_dir: Path, port: int = 8080) -> None:
    """Serve the built site locally using Python's built-in HTTP server."""
    handler = http.server.SimpleHTTPRequestHandler

    class _Handler(handler):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, directory=str(site_dir), **kwargs)  # type: ignore[arg-type]

    with socketserver.TCPServer(("", port), _Handler) as httpd:
        logger.info("Serving wiki at http://localhost:%d", port)
        print(f"Serving wiki at http://localhost:{port} (Ctrl+C to stop)")  # noqa: T201
        httpd.serve_forever()


# ---------------------------------------------------------------------------
# Article rendering
# ---------------------------------------------------------------------------


def render_article(
    article: dict,
    template_env: Environment,
    slug_map: dict[str, str],
    root: str,
    shared_ctx: dict,
) -> str:
    """Render a single wiki article dict to HTML string."""
    body_md = article["body"]

    # Convert markdown to HTML
    md = markdown.Markdown(
        extensions=_MD_EXTENSIONS,
        extension_configs=_MD_EXTENSION_CONFIGS,
    )
    body_html = md.convert(body_md)

    # Resolve wikilinks
    body_html = resolve_wikilinks(body_html, slug_map, root)

    # Build TOC from headings
    toc = build_toc(body_html)

    # Build infobox
    infobox = build_infobox(article["frontmatter"])

    # Categories from topics + concept_type + domain
    categories = _build_categories(article["frontmatter"])

    template = template_env.get_template("article.html")
    return template.render(
        title=article["title"],
        content=body_html,
        toc=toc,
        infobox=infobox,
        categories=categories,
        root=root,
        **shared_ctx,
    )


def resolve_wikilinks(html: str, slug_map: dict[str, str], root: str = "") -> str:
    """Replace [[concept name]] with proper <a href> links.

    Blue links for existing articles, red links for missing ones.
    """

    def _replace(match: re.Match) -> str:  # type: ignore[type-arg]
        link_text = match.group(1)
        lookup_key = link_text.lower().strip()
        rel_path = slug_map.get(lookup_key)
        if rel_path is None:
            # Also try slugified version
            rel_path = slug_map.get(slugify(lookup_key))
        if rel_path is not None:
            return f'<a href="{root}{rel_path}">{link_text}</a>'
        return f'<a href="#" class="wiki-redlink" title="Article not found">{link_text}</a>'

    return re.sub(r"\[\[([^\]]+)\]\]", _replace, html)


def build_infobox(frontmatter: dict) -> dict | None:
    """Build infobox key-value pairs, filtering out machine-readable fields."""
    infobox: dict[str, str] = {}
    for field in _INFOBOX_FIELDS:
        val = frontmatter.get(field)
        if val is not None and val != "" and val != []:
            if isinstance(val, list):
                infobox[field.replace("_", " ").title()] = ", ".join(str(v) for v in val)
            elif isinstance(val, float):
                infobox[field.replace("_", " ").title()] = f"{val:.2f}"
            else:
                infobox[field.replace("_", " ").title()] = str(val)
    return infobox if infobox else None


def build_toc(html: str) -> list[dict]:
    """Extract h2/h3 headings from HTML and build a table of contents."""
    # Match headings with optional id attribute
    heading_re = re.compile(r'<(h[23])(?:\s+[^>]*?id="([^"]*)"[^>]*)?>(.+?)</\1>', re.IGNORECASE)
    toc: list[dict] = []
    current_h2: dict | None = None

    for match in heading_re.finditer(html):
        tag = match.group(1).lower()
        heading_id = match.group(2) or ""
        text = re.sub(r"<[^>]+>", "", match.group(3)).strip()
        if not heading_id:
            heading_id = slugify(text)

        if tag == "h2":
            current_h2 = {"id": heading_id, "text": text, "children": []}
            toc.append(current_h2)
        elif tag == "h3" and current_h2 is not None:
            current_h2["children"].append({"id": heading_id, "text": text})

    return toc


def build_slug_map(wiki_dir: Path) -> dict[str, str]:
    """Map article titles (lowercased) to their relative HTML paths.

    Reads all .md files, extracts title from frontmatter.
    Returns {name_lower: relative_html_path}.
    """
    slug_map: dict[str, str] = {}
    for md_file in iter_visible_page_files(wiki_dir):
        meta = read_article_frontmatter(md_file)
        title = meta.get("title") or md_file.stem.replace("_", " ").title()
        rel = md_file.relative_to(wiki_dir)
        html_rel = str(rel.with_suffix(".html")).replace("\\", "/")

        # Index by lowercase title
        slug_map[title.lower()] = html_rel
        # Also index by slug
        slug_map[md_file.stem.lower()] = html_rel
        # Also index by slug with underscores replaced
        slug_map[md_file.stem.lower().replace("_", " ")] = html_rel

    return slug_map


# ---------------------------------------------------------------------------
# Special pages
# ---------------------------------------------------------------------------


def generate_main_page(articles: list[dict], env: Environment, shared_ctx: dict) -> str:
    """Main page with featured articles, stats, and recent changes."""
    # Sort by importance (descending), take top featured
    featured = sorted(
        [a for a in articles if a.get("importance")],
        key=lambda a: float(a.get("importance", 0)),
        reverse=True,
    )[:10]

    # Recent (by updated date)
    recent = sorted(
        articles,
        key=lambda a: a["frontmatter"].get("updated", ""),
        reverse=True,
    )[:15]

    template = env.get_template("index_page.html")
    return template.render(
        title="Main Page",
        root="",
        featured=featured,
        recent=recent,
        total_articles=len(articles),
        **shared_ctx,
    )


def generate_recent_changes(articles: list[dict], env: Environment, shared_ctx: dict) -> str:
    """Recent changes page sorted by updated date."""
    recent = sorted(
        articles,
        key=lambda a: a["frontmatter"].get("updated", ""),
        reverse=True,
    )

    template = env.get_template("recent.html")
    return template.render(
        title="Recent Changes",
        root="",
        articles=recent,
        **shared_ctx,
    )


def generate_random_page(articles: list[dict], env: Environment, shared_ctx: dict) -> str:
    """Page with JavaScript redirect to a random article."""
    paths = []
    for a in articles:
        rel = _article_html_relpath(a)
        paths.append(rel)

    template = env.get_template("random.html")
    return template.render(
        title="Random Article",
        root="",
        article_paths=json.dumps(paths),
        **shared_ctx,
    )


def generate_categories_index(articles: list[dict], env: Environment, shared_ctx: dict) -> str:
    """Index page listing all categories."""
    cats: dict[str, int] = {}
    for a in articles:
        for cat in _build_categories(a["frontmatter"]):
            cats[cat["name"]] = cats.get(cat["name"], 0) + 1

    sorted_cats = sorted(cats.items(), key=lambda x: (-x[1], x[0]))
    cat_list = [
        {"name": name, "slug": slugify(name), "count": count} for name, count in sorted_cats
    ]

    template = env.get_template("categories_index.html")
    return template.render(
        title="Categories",
        root="",
        categories=cat_list,
        **shared_ctx,
    )


def generate_category_pages(
    articles: list[dict], env: Environment, shared_ctx: dict
) -> dict[str, str]:
    """Generate one HTML page per category. Returns {slug: html_string}."""
    cat_articles: dict[str, list[dict]] = {}
    for a in articles:
        for cat in _build_categories(a["frontmatter"]):
            cat_articles.setdefault(cat["name"], []).append(a)

    result: dict[str, str] = {}
    template = env.get_template("category.html")
    for name, arts in cat_articles.items():
        cat_slug = slugify(name)
        arts_sorted = sorted(arts, key=lambda a: a["title"].lower())
        result[cat_slug] = template.render(
            title=f"Category: {name}",
            category_name=name,
            articles=arts_sorted,
            root="../",
            **shared_ctx,
        )
    return result


def generate_people_index(articles: list[dict], env: Environment, shared_ctx: dict) -> str:
    """Index page for people articles."""
    people = [
        a
        for a in articles
        if a["frontmatter"].get("concept_type") == "person"
        or (
            normalize_page_type(
                a["frontmatter"].get("page_type") or a["frontmatter"].get("type"),
                fallback_category=a.get("subfolder", ""),
            )
            == "entity"
        )
    ]
    people.sort(key=lambda a: a["title"].lower())

    template = env.get_template("people.html")
    return template.render(
        title="People",
        root="",
        people=people,
        **shared_ctx,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _collect_articles(wiki_dir: Path) -> list[dict]:
    """Read all markdown articles under wiki_dir into structured dicts."""
    articles: list[dict] = []
    for md_file in iter_visible_page_files(wiki_dir):

        text = md_file.read_text(encoding="utf-8", errors="replace")
        frontmatter = read_article_frontmatter(md_file)
        body = _strip_frontmatter(text)
        title = frontmatter.get("title") or md_file.stem.replace("_", " ").title()

        # Determine category/subfolder
        rel = md_file.relative_to(wiki_dir)
        subfolder = str(rel.parent).replace("\\", "/") if rel.parent != Path(".") else ""

        articles.append(
            {
                "title": str(title),
                "title_lower": str(title).lower(),
                "slug": md_file.stem,
                "body": body,
                "frontmatter": frontmatter,
                "rel_path": str(rel).replace("\\", "/"),
                "subfolder": subfolder,
                "domain": frontmatter.get("domain", ""),
                "domains": frontmatter.get("domains", []),
                "page_type": frontmatter.get("page_type") or frontmatter.get("type") or "concept",
                "importance": frontmatter.get("importance", 0),
                "status": frontmatter.get("status", ""),
            }
        )
    return articles


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter from markdown text."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].lstrip("\n")
    return text


def _article_html_relpath(article: dict) -> str:
    """Compute the relative HTML path for an article."""
    return article["rel_path"].replace(".md", ".html")


def _extract_domains(articles: list[dict]) -> list[dict]:
    """Extract unique domains from articles."""
    domain_set: set[str] = set()
    for a in articles:
        domains = a.get("domains")
        if isinstance(domains, list):
            for d in domains:
                if d:
                    domain_set.add(str(d))
        else:
            d = a.get("domain")
            if d:
                domain_set.add(str(d))
    return sorted(
        [{"slug": d, "label": d.replace("_", " ").title()} for d in domain_set],
        key=lambda x: x["label"],
    )


def _compute_stats(articles: list[dict], wiki_dir: Path) -> dict:
    """Compute wiki statistics."""
    source_set: set[str] = set()
    for a in articles:
        sources = a["frontmatter"].get("source_ids") or a["frontmatter"].get("sources")
        if isinstance(sources, list):
            for s in sources:
                if isinstance(s, str):
                    source_set.add(s)

    epoch = 0
    epoch_file = wiki_dir / "_epoch.json"
    if epoch_file.exists():
        try:
            data = json.loads(epoch_file.read_text(encoding="utf-8"))
            epoch = data.get("epoch", 0)
        except (json.JSONDecodeError, KeyError):
            pass

    return {
        "total_articles": len(articles),
        "total_sources": len(source_set),
        "current_epoch": epoch,
    }


def _build_categories(frontmatter: dict) -> list[dict]:
    """Build category list from frontmatter topics, page role, concept_type, and status."""
    categories: list[dict] = []

    # Topics
    topics = frontmatter.get("topics")
    if isinstance(topics, list):
        for t in topics:
            t_str = str(t).strip()
            if t_str:
                categories.append({"name": t_str, "slug": slugify(t_str)})
    elif isinstance(topics, str):
        # Sometimes topics is a string like "[method]"
        cleaned = topics.strip("[]")
        for t in cleaned.split(","):
            t_str = t.strip()
            if t_str:
                categories.append({"name": t_str, "slug": slugify(t_str)})

    page_type = frontmatter.get("page_type") or frontmatter.get("type")
    if page_type:
        categories.append({"name": str(page_type), "slug": slugify(str(page_type))})

    # Concept type
    ct = frontmatter.get("concept_type")
    if ct:
        categories.append({"name": str(ct), "slug": slugify(str(ct))})

    domains = frontmatter.get("domains")
    if isinstance(domains, list):
        for domain in domains:
            d = str(domain).strip()
            if d:
                categories.append({"name": f"Domain: {d}", "slug": slugify(f'domain {d}')})

    # Status
    status = frontmatter.get("status")
    if status:
        categories.append({"name": f"Status: {status}", "slug": f"status_{slugify(str(status))}"})

    return categories


def _copy_figures(wiki_dir: Path, output_dir: Path) -> None:
    """Copy any figure images from wiki into the site output."""
    figures_src = wiki_dir / "figures"
    if figures_src.is_dir():
        figures_dst = output_dir / "figures"
        if figures_dst.exists():
            shutil.rmtree(str(figures_dst))
        shutil.copytree(str(figures_src), str(figures_dst))

    # Also check for images directly in the wiki root or subdirs
    for ext in ("*.png", "*.jpg", "*.jpeg", "*.gif", "*.svg"):
        for img in wiki_dir.rglob(ext):
            if "_site" in str(img):
                continue
            rel = img.relative_to(wiki_dir)
            dst = output_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(img), str(dst))


def _build_search_index(articles: list[dict], slug_map: dict[str, str]) -> list[dict]:
    """Build a JSON search index with titles, first paragraphs, and tags."""
    index: list[dict] = []
    for a in articles:
        # Extract first paragraph from body
        first_para = ""
        for line in a["body"].split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
                first_para = stripped[:200]
                break

        topics = a["frontmatter"].get("topics", [])
        if isinstance(topics, str):
            topics = [t.strip() for t in topics.strip("[]").split(",") if t.strip()]

        index.append(
            {
                "title": a["title"],
                "url": _article_html_relpath(a),
                "excerpt": first_para,
                "tags": (
                    topics if isinstance(topics, list) else []
                )
                + [str(a["frontmatter"].get("page_type") or a["frontmatter"].get("type") or "")],
            }
        )
    return index


def _generate_search_js() -> str:
    """Generate client-side search JavaScript."""
    return """\
(function() {
  var searchIndex = null;
  var input = document.getElementById('search-input');
  var resultsDiv = document.getElementById('search-results');

  if (!input) return;

  // Create results dropdown
  if (!resultsDiv) {
    resultsDiv = document.createElement('div');
    resultsDiv.id = 'search-results';
    resultsDiv.className = 'search-results';
    input.parentNode.appendChild(resultsDiv);
  }

  function loadIndex() {
    if (searchIndex) return Promise.resolve(searchIndex);
    // Figure out root path from current page
    var scripts = document.getElementsByTagName('script');
    var searchScript = null;
    for (var i = 0; i < scripts.length; i++) {
      if (scripts[i].src && scripts[i].src.indexOf('search.js') !== -1) {
        searchScript = scripts[i];
        break;
      }
    }
    var root = '';
    if (searchScript) {
      root = searchScript.src.replace(/search\\.js.*$/, '');
    }
    return fetch(root + 'search-index.json')
      .then(function(r) { return r.json(); })
      .then(function(data) { searchIndex = data; return data; });
  }

  function search(query) {
    if (!searchIndex || !query) {
      resultsDiv.innerHTML = '';
      resultsDiv.style.display = 'none';
      return;
    }
    var q = query.toLowerCase();
    var matches = searchIndex.filter(function(item) {
      return item.title.toLowerCase().indexOf(q) !== -1 ||
             item.excerpt.toLowerCase().indexOf(q) !== -1 ||
             item.tags.some(function(t) { return t.toLowerCase().indexOf(q) !== -1; });
    }).slice(0, 10);

    if (matches.length === 0) {
      resultsDiv.innerHTML = '<div class="search-no-results">No results found</div>';
      resultsDiv.style.display = 'block';
      return;
    }

    var rootAttr = document.documentElement.getAttribute('data-root') || '';
    var html = matches.map(function(m) {
      return '<a class="search-result" href="' + rootAttr + m.url + '">' +
             '<div class="search-result-title">' + m.title + '</div>' +
             '<div class="search-result-excerpt">' + m.excerpt + '</div>' +
             '</a>';
    }).join('');
    resultsDiv.innerHTML = html;
    resultsDiv.style.display = 'block';
  }

  input.addEventListener('focus', function() { loadIndex(); });
  input.addEventListener('input', function() {
    loadIndex().then(function() { search(input.value); });
  });
  document.addEventListener('click', function(e) {
    if (!input.contains(e.target) && !resultsDiv.contains(e.target)) {
      resultsDiv.style.display = 'none';
    }
  });
})();
"""


def _generate_domain_index(
    domain: dict,
    articles: list[dict],
    env: Environment,
    shared_ctx: dict,
) -> str:
    """Generate an index page for a single domain."""
    articles_sorted = sorted(articles, key=lambda a: a["title"].lower())
    # Compute root relative to domains/<slug>/
    root = "../../"
    template = env.get_template("domain_index.html")
    return template.render(
        title=f"Domain: {domain['label']}",
        domain=domain,
        articles=articles_sorted,
        root=root,
        **shared_ctx,
    )


def _write_page(path: Path, content: str) -> None:
    """Write an HTML page to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
