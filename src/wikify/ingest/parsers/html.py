"""HTML parser.

Uses trafilatura for main-text extraction; falls back to a naive tag
stripper. ``<img>`` tags are parsed from the raw HTML and emitted as
typed ``RawImage`` records (no fetch performed here).
"""

import datetime
import re
from pathlib import Path

from ._sections import section_spans
from .registry import ParseResult, RawImage


def parse(path: Path) -> ParseResult:
    html = path.read_text(encoding="utf-8", errors="replace")
    body = _extract_text(html)
    title = _extract_title(html) or path.stem
    year = _extract_year(html)
    authors = _extract_author(html)
    description = _extract_meta_tag(html, prop="og:description") or _extract_meta_tag(
        html, name="description"
    )

    raw_images = _extract_html_images(html, path)
    metadata = {
        "title": title,
        "authors": authors,
        "summary": description or None,
        "year": year,
        "doi": None,
    }
    return ParseResult(
        markdown=body,
        sections=section_spans(body),
        raw_images=raw_images,
        metadata=metadata,
        title=title,
    )


def _extract_html_images(html: str, source_path: Path) -> list[RawImage]:
    """Return typed RawImage records parsed from <img> tags.

    Local src refs (relative or file://) are resolved against the HTML
    file's directory and copied into the corpus as real bytes. Remote
    refs are recorded as URL-only records that ``save_doc_images`` will
    persist as a sidecar JSON pointing at the URL.
    """
    out: list[RawImage] = []
    base = source_path.resolve().parent
    for m in re.finditer(r"<img\b([^>]*)>", html, re.IGNORECASE):
        attrs = m.group(1)
        src_m = re.search(r'src=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
        if not src_m:
            continue
        src = src_m.group(1).strip()
        alt_m = re.search(r'alt=["\']([^"\']*)["\']', attrs, re.IGNORECASE)
        alt = alt_m.group(1) if alt_m else ""
        is_remote = bool(re.match(r"^(https?:|data:|//)", src, re.IGNORECASE))
        if is_remote:
            out.append(RawImage(url=src, alt_text=alt))
            continue
        # Local: try to resolve against the HTML file dir.
        local = (base / src.lstrip("/")).resolve()
        if not local.exists():
            alt_local = (base / src).resolve()
            if alt_local.exists():
                local = alt_local
            else:
                out.append(RawImage(url=src, alt_text=alt))
                continue
        try:
            blob = local.read_bytes()
        except OSError:
            continue
        ext = local.suffix.lstrip(".").lower() or "png"
        out.append(RawImage(data=blob, ext=ext, alt_text=alt))
    return out


def _extract_with_trafilatura(html: str) -> str:
    try:
        import trafilatura

        result = trafilatura.extract(html, include_comments=False, include_tables=True)
        return result or ""
    except ImportError:
        return ""


def _strip_html_fallback(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_text(html: str) -> str:
    text = _extract_with_trafilatura(html)
    if not text or len(text) < 100:
        text = _strip_html_fallback(html)
    return text


def _extract_meta_tag(html: str, prop: str = "", name: str = "") -> str:
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
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return ""


def _extract_year(html: str) -> int | None:
    cands: list[str] = []
    cands.append(_extract_meta_tag(html, prop="article:published_time"))
    cands.append(_extract_meta_tag(html, prop="og:updated_time"))
    cands.append(_extract_meta_tag(html, name="date"))
    cands.append(_extract_meta_tag(html, name="pubdate"))
    cands.append(_extract_meta_tag(html, name="DC.date"))
    for val in cands:
        if not val:
            continue
        m = re.search(r"(\d{4})", val)
        if m:
            yr = int(m.group(1))
            if 1900 <= yr <= datetime.date.today().year + 1:
                return yr
    return None


def _extract_author(html: str) -> list[str]:
    author = _extract_meta_tag(html, name="author")
    if not author:
        author = _extract_meta_tag(html, prop="article:author")
    if not author:
        author = _extract_meta_tag(html, name="DC.creator")
    if author:
        return [a.strip() for a in author.split(",") if a.strip()]
    return []
