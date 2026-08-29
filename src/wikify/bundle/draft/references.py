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
# A definition STARTS at a line beginning with ``[^eN]:``. Parsing is done in
# two stages — find the starts, then parse each block between them — rather
# than with one multi-line regex over the whole body.
#
# That structure is the point. A single regex with DOTALL lets a malformed
# definition (no ``> "quote"`` tail, or an odd number of quote characters)
# run past its own line and swallow every definition after it, until it finds
# some closing quote further down. Both the validator and the normalizer read
# these definitions, so they would go blind together and AGREE while both were
# wrong: the check would report nothing and normalization would then rewrite
# the swallowed definitions from the marker index — silently repointing the
# very citations the mismatch check exists to catch. Block-bounded parsing
# confines a malformed definition to its own block.
_REF_DEF_START_RE = re.compile(r"^\[\^e(\d+)\]:", re.MULTILINE)
# Within one block: an optional body, then the ``> "quote"`` tail. Anchored at
# the block start (``\A``) and closing at the FIRST line end that follows a
# quote character. A block can carry trailing content (a following heading, a
# blank line, prose that itself ends in a quote), so the quote must be
# non-greedy: a greedy one runs past its own line and swallows that trailing
# text, which then fails the grounding check on a page that is correct.
_REF_DEF_BODY_RE = re.compile(
    r'\A(?P<body>.*?)\s*>\s*"(?P<quote>.+?)"\s*$', re.DOTALL | re.MULTILINE
)


def iter_ref_defs(body: str):
    """Yield ``(index, block_text)`` for every ``[^eN]:`` definition.

    ``index`` is 0-based (``[^e1]`` -> 0). The block runs to the next
    definition start or the end of the body, so a malformed definition cannot
    consume its successors.
    """
    starts = list(_REF_DEF_START_RE.finditer(body))
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(body)
        yield int(m.group(1)) - 1, body[m.end():end].strip()


def parse_ref_defs(body: str) -> dict[int, tuple[str, str]]:
    """``{index: (definition_body, quote)}`` for well-formed definitions.

    A block with no parsable ``> "quote"`` tail is omitted — it is malformed,
    not a definition of something else.
    """
    out: dict[int, tuple[str, str]] = {}
    for idx, block in iter_ref_defs(body):
        m = _REF_DEF_BODY_RE.match(block)
        if m:
            out[idx] = (m.group("body").strip(), m.group("quote"))
    return out


def parse_ref_chunk_ids(body: str) -> dict[int, str]:
    """Map each ``[^eN]:`` definition to the chunk id it names.

    The chunk id is the first whitespace-delimited token of the definition
    body. Definitions whose body is empty or does not start with a bare id
    token are omitted; callers treat a missing entry as "not asserted".
    """
    out: dict[int, str] = {}
    for idx, (def_body, _quote) in parse_ref_defs(body).items():
        head = def_body.split(maxsplit=1)
        if head and not head[0].startswith("("):
            out[idx] = head[0]
    return out


def evidence_indices_by_chunk_id(evidence) -> dict[str, list[int]]:
    """``{chunk_id: [every index holding it]}``.

    All indices, not the last: a chunk gathered twice is a normal state
    (``append_evidence`` does not dedup; ``dedup_evidence`` is a separate pass
    only ``work tend`` runs), and a last-wins map makes a correct marker
    pointing at the first occurrence look like a mismatch with the second.
    """
    out: dict[str, list[int]] = {}
    for i, ev in enumerate(evidence):
        if ev.chunk_id:
            out.setdefault(ev.chunk_id, []).append(i)
    return out


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
    return {idx: quote for idx, (_body, quote) in parse_ref_defs(refs_body).items()}


def _assert_markers_match_evidence(body: str, draft: WriteRequest) -> None:
    """Refuse to normalize when a footnote names a different evidence entry.

    Normalization REWRITES every definition from the marker index, so a marker
    numbered by hand rather than by position silently acquires a well-formed
    citation pointing at the wrong source - and the grounding check that runs
    afterwards sees only the rewritten, self-consistent result. Fail here
    instead, while the writer's own chunk id is still on the page.

    Scans the WHOLE body through the shared parser, not just the text after
    the ``## References`` heading: a definition this misses is a definition
    normalization repoints unchecked, and ``draft check`` must reach the same
    verdict on the same input.
    """
    by_chunk_id = evidence_indices_by_chunk_id(draft.evidence)
    for idx, chunk_id in parse_ref_chunk_ids(body).items():
        named = by_chunk_id.get(chunk_id)
        if not named or idx in named or not (0 <= idx < len(draft.evidence)):
            continue
        wanted = ", ".join(f"e{i + 1}" for i in named)
        raise ValueError(
            f"marker 'e{idx + 1}' resolves POSITIONALLY to evidence[{idx}] "
            f"({draft.evidence[idx].chunk_id}), but its definition names "
            f"{chunk_id}, which is evidence[{named[0]}] - renumber the marker "
            f"to {wanted} (markers are 1-based indices into draft.json's "
            "evidence array, not free labels)"
        )


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
    _assert_markers_match_evidence(body, draft)
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
