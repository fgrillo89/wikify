"""CrossRef API client for citation metadata resolution.

Two resolution strategies:
1. DOI lookup: ``GET /works/{doi}`` -- exact, authoritative.
2. Fuzzy query: ``GET /works?query.bibliographic={raw_text}`` -- best-effort,
   accepted only when the confidence score exceeds a threshold.

Results are cached on disk so re-runs are free.
"""

import hashlib
import json
import time
from pathlib import Path
from urllib.parse import quote

import httpx

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_CACHE_TTL_DAYS = 30


def _cache_dir(corpus_root: Path) -> Path:
    return corpus_root / ".crossref_cache"


def _cache_key(raw: str) -> str:
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _cache_get(corpus_root: Path, key: str) -> dict | None:
    p = _cache_dir(corpus_root) / f"{key}.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    age_days = (time.time() - data.get("_cached_at", 0)) / 86400
    if age_days > _CACHE_TTL_DAYS:
        return None
    return data


def _cache_put(corpus_root: Path, key: str, data: dict) -> None:
    d = _cache_dir(corpus_root)
    d.mkdir(parents=True, exist_ok=True)
    data["_cached_at"] = time.time()
    (d / f"{key}.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_BASE = "https://api.crossref.org/works"
_HEADERS = {
    "User-Agent": "wikify/1.0 (https://github.com/fgrillo89/wikify; "
    "mailto:fgrillo89@gmail.com) python-httpx",
}
_TIMEOUT = 10.0


def _get_json(url: str) -> dict | None:
    """GET with polite headers. Returns parsed JSON or None on any error."""
    try:
        resp = httpx.get(url, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
        if resp.status_code != 200:
            return None
        return resp.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Resolution strategies
# ---------------------------------------------------------------------------

def _parse_crossref_item(item: dict) -> dict:
    """Extract structured fields from a CrossRef work item."""
    authors = []
    for a in item.get("author", []):
        given = a.get("given", "")
        family = a.get("family", "")
        if family:
            authors.append(f"{given} {family}".strip())

    title_list = item.get("title", [])
    title = title_list[0] if title_list else ""

    venue = ""
    for field in ("container-title", "short-container-title"):
        v = item.get(field, [])
        if v:
            venue = v[0]
            break

    year = None
    for date_field in ("published-print", "published-online", "issued"):
        parts = (item.get(date_field) or {}).get("date-parts", [[]])
        if parts and parts[0] and parts[0][0]:
            year = parts[0][0]
            break

    doi = item.get("DOI", "")
    volume = item.get("volume", "")
    pages = item.get("page", "")
    publisher = item.get("publisher", "")

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "venue": venue,
        "doi": doi,
        "volume": volume,
        "pages": pages,
        "publisher": publisher,
    }


def resolve_by_doi(
    doi: str, *, corpus_root: Path | None = None,
) -> dict | None:
    """Resolve a DOI to structured metadata via CrossRef. Returns None on miss."""
    key = _cache_key(f"doi:{doi}")
    if corpus_root:
        cached = _cache_get(corpus_root, key)
        if cached:
            return None if cached.get("_not_found") else cached

    data = _get_json(f"{_BASE}/{quote(doi, safe='')}")
    if data is None or "message" not in data:
        if corpus_root:
            _cache_put(corpus_root, key, {"_not_found": True})
        return None

    result = _parse_crossref_item(data["message"])
    if corpus_root:
        _cache_put(corpus_root, key, result)
    return result


def resolve_by_query(
    raw_text: str,
    *,
    corpus_root: Path | None = None,
    confidence_threshold: float = 80.0,
) -> dict | None:
    """Fuzzy-match a raw citation string against CrossRef.

    Returns structured metadata if the top result's score exceeds
    *confidence_threshold*, otherwise None.
    """
    # Truncate for query (CrossRef has URL length limits)
    query = raw_text[:300].strip()
    if len(query) < 20:
        return None

    key = _cache_key(f"query:{query}")
    if corpus_root:
        cached = _cache_get(corpus_root, key)
        if cached:
            return None if cached.get("_not_found") else cached

    url = f"{_BASE}?query.bibliographic={quote(query)}&rows=1"
    data = _get_json(url)
    if data is None or "message" not in data:
        if corpus_root:
            _cache_put(corpus_root, key, {"_not_found": True})
        return None

    items = data["message"].get("items", [])
    if not items:
        if corpus_root:
            _cache_put(corpus_root, key, {"_not_found": True})
        return None

    item = items[0]
    score = item.get("score", 0)
    if score < confidence_threshold:
        if corpus_root:
            _cache_put(corpus_root, key, {"_not_found": True})
        return None

    result = _parse_crossref_item(item)
    result["crossref_score"] = score
    if corpus_root:
        _cache_put(corpus_root, key, result)
    return result


# ---------------------------------------------------------------------------
# Batch resolution
# ---------------------------------------------------------------------------

def resolve_citation(
    cit: dict,
    *,
    corpus_root: Path | None = None,
    confidence_threshold: float = 80.0,
) -> dict:
    """Try to resolve one citation dict via CrossRef. Mutates and returns it.

    Strategy: DOI first, then fuzzy query.  Sets ``crossref_resolved``
    to True/False and populates structured fields on success.
    """
    resolved = None

    # Strategy 1: DOI lookup (authoritative)
    doi = cit.get("doi")
    if doi:
        resolved = resolve_by_doi(doi, corpus_root=corpus_root)

    # Strategy 2: fuzzy query (best-effort)
    if resolved is None:
        raw = cit.get("raw_text", "")
        if raw:
            resolved = resolve_by_query(
                raw,
                corpus_root=corpus_root,
                confidence_threshold=confidence_threshold,
            )

    if resolved is not None:
        cit["crossref_resolved"] = True
        cit["title"] = resolved.get("title", "")
        cit["authors"] = resolved.get("authors", [])
        cit["venue"] = resolved.get("venue", "")
        cit["volume"] = resolved.get("volume", "")
        cit["pages"] = resolved.get("pages", "")
        cit["publisher"] = resolved.get("publisher", "")
        if resolved.get("doi") and not cit.get("doi"):
            cit["doi"] = resolved["doi"]
        if resolved.get("year") and not cit.get("year"):
            cit["year"] = resolved["year"]
        if resolved.get("crossref_score"):
            cit["crossref_score"] = resolved["crossref_score"]
    else:
        cit["crossref_resolved"] = False

    return cit


def resolve_citations_batch(
    citations: list[dict],
    *,
    corpus_root: Path | None = None,
    confidence_threshold: float = 80.0,
    rate_limit: float = 0.04,
) -> list[dict]:
    """Resolve a batch of citations via CrossRef with rate limiting.

    Default rate_limit=0.04s (25 req/s) is well below CrossRef's polite
    pool ceiling of 50 req/s. Cached lookups skip the sleep entirely.
    DOIs seen multiple times are resolved once and shared across citations.
    """
    import sys

    from tqdm import tqdm

    total = len(citations)
    resolved_count = 0
    # Track DOIs already resolved in this batch to avoid redundant lookups
    doi_resolved: dict[str, dict] = {}

    bar = tqdm(
        citations,
        desc="[crossref] resolving",
        unit="cit",
        file=sys.stderr,
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]",
    )

    for cit in bar:
        doi = cit.get("doi")

        # Fast path: DOI already resolved in this batch
        if doi and doi in doi_resolved:
            prev = doi_resolved[doi]
            if prev:
                cit["crossref_resolved"] = True
                for k in ("title", "authors", "venue", "volume",
                           "pages", "publisher"):
                    if prev.get(k):
                        cit[k] = prev[k]
                if prev.get("year") and not cit.get("year"):
                    cit["year"] = prev["year"]
            else:
                cit["crossref_resolved"] = False
            if cit.get("crossref_resolved"):
                resolved_count += 1
            bar.update(0)
            continue

        # Check if this will be a cache hit (no HTTP needed)
        needs_http = True
        if corpus_root:
            if doi:
                key = _cache_key(f"doi:{doi}")
                if _cache_get(corpus_root, key) is not None:
                    needs_http = False
            if needs_http:
                raw = cit.get("raw_text", "")
                if raw:
                    key = _cache_key(f"query:{raw[:300].strip()}")
                    if _cache_get(corpus_root, key) is not None:
                        needs_http = False

        resolve_citation(
            cit,
            corpus_root=corpus_root,
            confidence_threshold=confidence_threshold,
        )

        if cit.get("crossref_resolved"):
            resolved_count += 1
            # Remember resolved DOI for batch dedup
            if doi:
                doi_resolved[doi] = {
                    k: cit.get(k)
                    for k in ("title", "authors", "venue", "volume",
                              "pages", "publisher", "year")
                }
        else:
            if doi:
                doi_resolved[doi] = {}

        # Rate limit only for uncached HTTP requests
        if needs_http and rate_limit > 0:
            time.sleep(rate_limit)

    print(
        f"[crossref] {resolved_count}/{total} citations resolved",
        file=sys.stderr,
    )
    return citations
