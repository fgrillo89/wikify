"""Markdown / plain-text parser. Stdlib only.

Returns ParseResult with the file body as markdown, a section index built
from the heading tree, plus any frontmatter metadata. No images extracted
(markdown image syntax is rare in our corpora; can be added later).
"""

import re
from pathlib import Path

from ._sections import section_spans
from .registry import ParseResult

_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse(path: Path) -> ParseResult:
    raw = path.read_text(encoding="utf-8", errors="replace")
    meta, body, _body_offset = _split_frontmatter(raw)
    title = _extract_title(meta, body, path.stem)
    return ParseResult(
        markdown=body,
        sections=section_spans(body),
        images=[],
        metadata=meta,
        title=title,
    )


def _split_frontmatter(text: str) -> tuple[dict, str, int]:
    m = _FM_RE.match(text)
    if not m:
        return {}, text, 0
    meta: dict = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        v = v.strip().strip('"').strip("'")
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            meta[k.strip()] = (
                [x.strip().strip('"').strip("'") for x in inner.split(",")] if inner else []
            )
        else:
            meta[k.strip()] = v
    return meta, text[m.end() :], m.end()


def _extract_title(meta: dict, body: str, stem: str) -> str:
    if meta.get("title"):
        return str(meta["title"])
    m = re.search(r"^#\s+(.+?)\s*$", body, re.MULTILINE)
    return m.group(1) if m else stem
