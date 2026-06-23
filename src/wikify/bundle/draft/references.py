"""Deterministic reference normalization for draft responses."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ...api import Bundle
from .artifact import (
    draft_path,
    read_json,
    response_path,
    strip_draft_envelope,
    write_json,
)
from .schema import WriteRequest

_REFERENCES_HEADING_RE = re.compile(r"(?im)^##\s+References\s*$")
_PROSE_MARKER_RE = re.compile(r"\[\^e(\d+)\]")
_REF_DEF_RE = re.compile(
    r'^\[\^e(\d+)\]:\s*(?P<body>.*?)\s*>\s*"(?P<quote>.+?)"\s*$',
    re.MULTILINE,
)


@dataclass(frozen=True)
class ReferenceNormalizationResult:
    response_path: str
    markers: list[str]
    reference_count: int


def _split_references(body: str) -> tuple[str, str]:
    matches = list(_REFERENCES_HEADING_RE.finditer(body))
    if not matches:
        return body.rstrip(), ""
    match = matches[-1]
    return body[: match.start()].rstrip(), body[match.end() :].strip()


def _parse_existing_quotes(refs_body: str) -> dict[int, str]:
    return {int(m.group(1)) - 1: m.group("quote") for m in _REF_DEF_RE.finditer(refs_body)}


def _quote_from_chunk(chunk_text: str) -> str:
    """First substantive single-line span of the chunk.

    Skips lines shorter than 40 chars (likely headers, ISSN banners,
    affiliation fragments) on the first pass; falls back to the first
    non-empty line if every line is short.
    """
    lines = [ln.strip() for ln in chunk_text.splitlines()]
    for line in lines:
        if len(line) >= 40:
            return line[:240].strip()
    for line in lines:
        if line:
            return line[:240].strip()
    return chunk_text.strip()[:240].strip()


def _canonical_quote(evidence, existing_quote: str = "") -> str:
    """Pick a single-line verbatim quote.

    The validator parses each ``[^eN]: ... > "<quote>"`` line with a
    single-line regex, so a multi-line quote silently fails the
    ``quote_not_in_body`` check. Prefer a quote that already lives on
    one physical line of ``chunk_text``; fall back to the first
    non-empty line of the chunk.
    """
    chunk_text = evidence.chunk_text or ""
    quote = (evidence.quote or "").strip()
    if quote and "\n" not in quote and (not chunk_text or quote in chunk_text):
        return quote
    existing_quote = existing_quote.strip()
    if (
        existing_quote
        and "\n" not in existing_quote
        and (not chunk_text or existing_quote in chunk_text)
    ):
        return existing_quote
    return _quote_from_chunk(chunk_text)


def normalize_response_references(bundle: Bundle, slug: str) -> ReferenceNormalizationResult:
    """Rewrite response references from the draft evidence index.

    The writer owns prose and marker placement. This primitive only makes
    the response artifact's reference definitions match the existing
    ``[^eN]`` prose markers, where ``N`` maps to ``draft.evidence[N - 1]``.
    """
    draft_payload = strip_draft_envelope(read_json(draft_path(bundle, slug)))
    draft = WriteRequest.model_validate(draft_payload)
    response_p = response_path(bundle, slug)
    response = read_json(response_p)

    body = response.get("body_markdown")
    if not isinstance(body, str):
        raise ValueError("response.body_markdown must be a string")

    prose_body, refs_body = _split_references(body)
    marker_indexes = sorted({int(m.group(1)) - 1 for m in _PROSE_MARKER_RE.finditer(prose_body)})
    existing_quotes = _parse_existing_quotes(refs_body)

    ref_lines: list[str] = []
    for idx in marker_indexes:
        if idx < 0 or idx >= len(draft.evidence):
            continue
        evidence = draft.evidence[idx]
        quote = _canonical_quote(evidence, existing_quotes.get(idx, ""))
        if not quote:
            raise ValueError(f"no quote available for e{idx + 1}")
        ref_lines.append(f'[^e{idx + 1}]: {evidence.chunk_id} ({evidence.doc_id}) > "{quote}"')

    normalized = prose_body.rstrip()
    if ref_lines:
        normalized = normalized + "\n\n## References\n\n" + "\n".join(ref_lines) + "\n"
    else:
        normalized = normalized + "\n\n## References\n"

    response["body_markdown"] = normalized
    response["used_markers"] = [f"e{idx + 1}" for idx in marker_indexes]
    write_json(response_p, response)
    return ReferenceNormalizationResult(
        response_path=str(response_p),
        markers=response["used_markers"],
        reference_count=len(ref_lines),
    )
