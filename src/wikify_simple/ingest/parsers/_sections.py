"""Shared helper: build (heading_path, start, end) spans from a markdown string.

Used by all parsers (pdf, docx, pptx, html, markdown) so they share one
section-extraction implementation. The chunker consumes these spans
directly.
"""

import re

_H_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def section_spans(body: str) -> list[tuple[list[str], int, int]]:
    matches = list(_H_RE.finditer(body))
    if not matches:
        return [(["body"], 0, len(body))]
    spans: list[tuple[list[str], int, int]] = []
    stack: list[tuple[int, str]] = []
    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        path = [t for _, t in stack]
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        spans.append((path, start, end))
    if matches[0].start() > 0:
        spans.insert(0, (["preamble"], 0, matches[0].start()))
    return spans
