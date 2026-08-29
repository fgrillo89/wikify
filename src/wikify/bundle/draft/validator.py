"""Validator — schema + structural + quote-grounding checks for response.json.

Owns the validation logic: schema checks, structural
checks (``_check_wikipedia_structure`` / ``_check_figure_mentions``),
and verbatim quote-grounding. Reads ``draft.json`` + ``response.json``
from the concept folder; writes ``validation.json``.

The verdict has the shape::

    {
      "schema_version": 1,
      "ok": bool,
      "page_id": str,
      "response_path": str,
      "draft_path": str,
      "errors": [{"path": str, "code": str, "message": str}],
      "warnings": [{"path": str, "code": str, "message": str}],
      "structural_checks": {<check>: <bool>},
      "checked_at": ISO8601,
    }

``errors`` gate the commit (they flip ``ok`` to false); ``warnings`` are
quality signals that surface in the verdict without blocking the commit.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

from pydantic import ValidationError

from ...api import Bundle
from ...grounding import is_grounded, normalize_grounding_text
from .artifact import (
    draft_path,
    read_json,
    response_path,
    strip_draft_envelope,
    validation_path,
    write_json,
)
from .references import (
    evidence_indices_by_chunk_id,
    parse_ref_chunk_ids,
    parse_ref_defs,
)
from .schema import (
    QuoteNotInChunkError,
    WriteRequest,
    WriteResponse,
    _check_figure_mentions,
    _check_wikipedia_structure,
)

VALIDATION_SCHEMA_VERSION = 1


# The reference-definition parse is SHARED with ``normalize_response_references``
# (which rewrites definitions from the marker index). Two copies drifted apart
# once already, and a definition one side cannot see is one the other silently
# repoints.
_PROSE_MARKER_RE = re.compile(r"\[\^e(\d+)\]")

# Grounding-match normalization is shared with the data-harvest verifier
# (`data/verify.py`) so a quote grounds identically at both gates.
_ground_norm = normalize_grounding_text
_quote_is_grounded = is_grounded

_FIGURE_PLACEHOLDER_RE = re.compile(r"\{\{figure:([A-Za-z0-9_.-]+)\}\}")
# Literal \uXXXX escape sequences in prose (six chars: backslash u + 4 hex digits).
_UNICODE_ESCAPE_RE = re.compile(r"\\u[0-9a-fA-F]{4}")
# A Python/JSON mapping literal leaking into prose, e.g. a writer accidentally
# pasting a citation-context dict: ``{'e1_cid': '...', 'e1_doc': '...'}``. We
# match an opening brace immediately followed by a quoted key and a colon.
_STRAY_MAPPING_RE = re.compile(r"\{\s*['\"][^'\"]+['\"]\s*:\s*['\"]")


def _strip_references_section(body: str) -> str:
    """Return *body* with the trailing ``## References`` block removed.

    Footnote bodies legitimately carry chunk/doc identifiers; prose-integrity
    checks run over the reader-facing text only.
    """
    m = re.search(r"(?im)^##\s+references\s*$", body)
    return body[: m.start()] if m else body


def _utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


_strip_envelope = strip_draft_envelope


def _pydantic_errors(exc: ValidationError) -> list[dict]:
    return [
        {
            "path": "/".join(str(part) for part in err.get("loc", ())),
            "code": err.get("type", "validation_error"),
            "message": err.get("msg", ""),
        }
        for err in exc.errors()
    ]


def _parse_ref_quotes(body: str) -> dict[int, str]:
    return {idx: quote for idx, (_body, quote) in parse_ref_defs(body).items()}


def _parse_prose_markers(body: str) -> set[int]:
    return {int(m.group(1)) - 1 for m in _PROSE_MARKER_RE.finditer(body)}


def _marker_to_index(marker: str) -> int | None:
    if not marker:
        return None
    s = marker.strip().lstrip("[").lstrip("^").lstrip("e").rstrip("]")
    try:
        return int(s) - 1
    except ValueError:
        return None


def _marker_mismatch_error(
    idx: int, draft: WriteRequest, ref_chunk_id: str, body_quote: str
) -> dict | None:
    """Flag a citation whose marker index selects the wrong evidence entry.

    ``[^eN]`` resolves POSITIONALLY to ``draft.evidence[N-1]``. A writer that
    numbers markers freely (by order of first use in prose, say) produces a
    footnote that names one chunk while the marker points at another. The
    generic ``quote_not_in_source`` check misses this whenever the quote also
    happens to occur in the entry the marker landed on — overlapping chunks
    from one paper make that common — and ``normalize-references`` then
    REWRITES the footnote from the marker index, so the wrong-source citation
    ends up looking perfectly well-formed.

    Two independent signals, in order of reliability:

    1. The footnote names a chunk id that belongs to a DIFFERENT evidence
       entry. Deterministic; catches the case even when both entries contain
       the quote.
    2. No usable chunk id, but the quote grounds in exactly one OTHER entry
       and not in the selected one. Reports the intended index instead of the
       generic "fabricated or corrupted citation".
    """
    marker = f"e{idx + 1}"
    selected = draft.evidence[idx]
    if ref_chunk_id and ref_chunk_id != selected.chunk_id:
        # Shared with the normalizer's assert so both reach the same verdict on
        # duplicates (a chunk gathered twice occupies several indices).
        named = evidence_indices_by_chunk_id(draft.evidence).get(ref_chunk_id)
        if named:
            wanted = ", ".join(f"e{i + 1}" for i in named)
            return {
                "path": f"body_markdown/[^{marker}]",
                "code": "marker_evidence_mismatch",
                "message": (
                    f"marker {marker!r} resolves POSITIONALLY to evidence"
                    f"[{idx}] ({selected.chunk_id}), but its `[^{marker}]:` "
                    f"definition names {ref_chunk_id}, which is evidence"
                    f"[{named[0]}] - renumber the marker to "
                    f"{wanted} (markers are 1-based indices into "
                    "draft.json's evidence array, not free labels)"
                ),
            }
    if ref_chunk_id or not body_quote:
        return None
    if _quote_is_grounded(body_quote, selected.chunk_text or ""):
        return None
    grounded_in = [
        i
        for i, ev in enumerate(draft.evidence)
        if i != idx and _quote_is_grounded(body_quote, ev.chunk_text or "")
    ]
    if len(grounded_in) != 1:
        return None
    other = grounded_in[0]
    return {
        "path": f"body_markdown/[^{marker}]",
        "code": "marker_evidence_mismatch",
        "message": (
            f"marker {marker!r} resolves POSITIONALLY to evidence[{idx}], "
            f"whose chunk does not contain the quote; the quote is verbatim "
            f"in evidence[{other}] - renumber the marker to e{other + 1} "
            "(markers are 1-based indices into draft.json's evidence array, "
            "not free labels)"
        ),
    }


def _quote_grounding_errors(
    draft: WriteRequest, response: WriteResponse
) -> list[dict]:
    """Verify every ``[^eN]:`` definition in the body is grounded in
    ``evidence[i].chunk_text``.

    Each marker resolves 1:1 to a ``[^eN]:`` definition, the body quote
    must be a verbatim substring of the evidence chunk's source text,
    and ``used_markers`` must match the prose markers exactly.
    """
    body_quotes = _parse_ref_quotes(response.body_markdown)
    ref_chunk_ids = parse_ref_chunk_ids(response.body_markdown)
    prose_markers = _parse_prose_markers(response.body_markdown)
    declared_markers = {
        idx
        for m in response.used_markers
        if (idx := _marker_to_index(m)) is not None
    }
    errors: list[dict] = []

    undeclared = sorted(prose_markers - declared_markers)
    if undeclared:
        errors.append(
            {
                "path": "used_markers",
                "code": "undeclared_prose_marker",
                "message": (
                    f"body uses marker(s) {sorted(f'e{i + 1}' for i in undeclared)} "
                    "that are missing from used_markers"
                ),
            }
        )
    spurious = sorted(declared_markers - prose_markers)
    if spurious:
        errors.append(
            {
                "path": "used_markers",
                "code": "spurious_used_marker",
                "message": (
                    f"used_markers contains {sorted(f'e{i + 1}' for i in spurious)} "
                    "with no corresponding `[^eN]` in prose"
                ),
            }
        )

    if not prose_markers and not declared_markers:
        errors.append(
            {
                "path": "body_markdown",
                "code": "no_markers",
                "message": "response body has no [^eN] markers; grounding cannot be verified",
            }
        )
        return errors

    checked = prose_markers | declared_markers
    for idx in sorted(checked):
        marker = f"e{idx + 1}"
        if idx < 0 or idx >= len(draft.evidence):
            errors.append(
                {
                    "path": f"markers/{marker}",
                    "code": "unknown_marker",
                    "message": f"marker {marker!r} has no matching evidence entry",
                }
            )
            continue
        body_quote = body_quotes.get(idx)
        if not body_quote:
            errors.append(
                {
                    "path": f"body_markdown/[^{marker}]",
                    "code": "quote_not_in_body",
                    "message": (
                        f"marker {marker!r} has no `[^{marker}]:` definition "
                        "in the body References block"
                    ),
                }
            )
            continue
        evidence = draft.evidence[idx]
        chunk_text = evidence.chunk_text or ""
        if not chunk_text:
            errors.append(
                {
                    "path": f"evidence/{idx}/chunk_text",
                    "code": "chunk_text_missing",
                    "message": (
                        f"evidence[{idx}] has no chunk_text; cannot verify source grounding"
                    ),
                }
            )
            continue
        # A wrong marker number is checked BEFORE grounding: the quote can be
        # verbatim in the entry the marker landed on and still cite the wrong
        # source, which the grounding check alone cannot distinguish.
        mismatch = _marker_mismatch_error(
            idx, draft, ref_chunk_ids.get(idx, ""), body_quote
        )
        if mismatch is not None:
            errors.append(mismatch)
            continue
        if not _quote_is_grounded(body_quote, chunk_text):
            errors.append(
                {
                    "path": f"body_markdown/[^{marker}]",
                    "code": "quote_not_in_source",
                    "message": (
                        f"body quote for {marker!r} is not a substring of "
                        f"evidence[{idx}].chunk_text — fabricated or corrupted citation"
                    ),
                }
            )
    return errors


def _figure_selection_errors(
    draft: WriteRequest, response: WriteResponse
) -> list[dict]:
    request_by_id = {fig.id: fig for fig in draft.figures}
    placeholders = _FIGURE_PLACEHOLDER_RE.findall(response.body_markdown)
    placeholder_set = set(placeholders)
    selected_by_anchor = {fig.placement_anchor: fig for fig in response.figures}
    errors: list[dict] = []

    if len(selected_by_anchor) != len(response.figures):
        errors.append(
            {
                "path": "figures",
                "code": "duplicate_figure_anchor",
                "message": "figures must use unique placement_anchor values",
            }
        )

    for anchor in sorted(placeholder_set - set(selected_by_anchor)):
        errors.append(
            {
                "path": "body_markdown",
                "code": "unknown_figure_placeholder",
                "message": f"figure placeholder {anchor!r} has no matching figures entry",
            }
        )

    for fig in response.figures:
        if fig.placement_anchor not in placeholder_set:
            errors.append(
                {
                    "path": "figures",
                    "code": "unused_selected_figure",
                    "message": (
                        f"selected figure {fig.figure_id!r} has no "
                        f"{{{{figure:{fig.placement_anchor}}}}} placeholder"
                    ),
                }
            )
        allowed = request_by_id.get(fig.figure_id)
        if allowed is None:
            errors.append(
                {
                    "path": "figures",
                    "code": "unknown_figure_id",
                    "message": (
                        f"selected figure {fig.figure_id!r} is not in "
                        "the draft figures list"
                    ),
                }
            )
            continue
        if fig.path.replace("\\", "/") != allowed.path.replace("\\", "/"):
            errors.append(
                {
                    "path": "figures",
                    "code": "figure_path_mismatch",
                    "message": (
                        f"selected figure {fig.figure_id!r} path does not match "
                        "the draft figure candidate"
                    ),
                }
            )
        if not fig.source_marker:
            errors.append(
                {
                    "path": "figures",
                    "code": "missing_figure_source_marker",
                    "message": (
                        f"selected figure {fig.figure_id!r} must set "
                        "source_marker to the [^eN] marker whose evidence "
                        "chunk the figure is sourced from; the renderer "
                        "uses it to cite the source paper in the caption"
                    ),
                }
            )
        elif fig.source_marker not in response.used_markers:
            errors.append(
                {
                    "path": "figures",
                    "code": "unknown_figure_source_marker",
                    "message": (
                        f"selected figure {fig.figure_id!r} cites source_marker "
                        f"{fig.source_marker!r}, which is not in used_markers"
                    ),
                }
            )
    return errors


# --- Evidence-coverage (breadth-of-use) check ------------------------------
# A writer can gather many source documents yet cite only a few of them for
# every claim. These thresholds flag a committed page that under-uses the
# breadth of evidence it was handed, so richness is enforced at write time
# rather than merely encouraged. Deliberately conservative: the check stays
# silent until enough distinct source documents were available that narrow
# citation is unlikely to reflect a legitimately focused page. Person pages
# are author-focused but still gather multiple documents; the same thresholds
# apply to them unchanged.
EVIDENCE_COVERAGE_MIN_DOCS = 5  # floor on available docs before the check fires
EVIDENCE_COVERAGE_MIN_CITED = 3  # absolute floor on distinct cited docs
EVIDENCE_COVERAGE_FRACTION = 0.5  # cite at least this share of available docs


def _evidence_coverage_findings(
    draft: WriteRequest, response: WriteResponse
) -> list[dict]:
    """Warn when a page under-uses the breadth of gathered evidence.

    ``available_docs`` is the set of distinct source documents the writer was
    handed (one per evidence record). ``cited_docs`` is the subset whose
    ``[^eN]`` marker is actually cited in the reader-facing prose. A page that
    gathered many documents but leans on only a couple is flagged. This is a
    quality signal, not a correctness error: the finding is returned as a
    warning and does not flip ``ok``.
    """
    available_docs = {ev.doc_id for ev in draft.evidence if ev.doc_id}
    if len(available_docs) < EVIDENCE_COVERAGE_MIN_DOCS:
        return []
    cited_markers = _parse_prose_markers(
        _strip_references_section(response.body_markdown)
    )
    cited_docs = {
        draft.evidence[idx].doc_id
        for idx in cited_markers
        if 0 <= idx < len(draft.evidence) and draft.evidence[idx].doc_id
    }
    threshold = max(
        EVIDENCE_COVERAGE_MIN_CITED,
        math.ceil(EVIDENCE_COVERAGE_FRACTION * len(available_docs)),
    )
    if len(cited_docs) >= threshold:
        return []
    return [
        {
            "path": "body_markdown",
            "code": "evidence_underuse",
            "message": (
                f"page cites {len(cited_docs)} of {len(available_docs)} gathered "
                "source documents; draw on more of the gathered evidence "
                f"(cite at least {threshold})"
            ),
        }
    ]


# --- Undelimited maths (presentation warning) ------------------------------
# The renderer typesets `$...$` / `$$...$$` / `\(...\)` / `\[...\]` with KaTeX.
# Writers reliably delimit DISPLAY equations but write inline symbols and
# relations as bare prose (`G_{i,j} ~ (t_i - t_j)^(-b)`, `sigma_D`, `Q^(-5/2)`,
# and pasted Unicode `σ β ≈ ∝ −`), which renders as literal gibberish. This is
# presentation, not correctness, so it warns rather than blocking the commit.

# Spans whose contents are not prose and must not be scanned: fenced code,
# every math delimiter pair, inline code, and inline quotations (a verbatim
# quote reproduces the source's own notation and is not the writer's to fix).
#
# Two alternatives are deliberately narrow, because a greedy version of either
# masks ordinary prose and silences the whole detector:
#
# - Inline `$...$` requires the opening `$` not to be followed by whitespace
#   OR A DIGIT, and the span to contain a maths character (`\\`, `_`, `^`,
#   `{`). Both conditions are needed. Two currency amounts on one line ("a $10
#   million order moves the price by sigma_D, while a $5 million order...")
#   otherwise pair into a fake math span that blanks everything between them,
#   and the maths-char test alone does not stop it -- the subscript the
#   detector is hunting for is itself the maths char that lets the span pair.
#   Market-impact prose is full of dollar amounts, so this direction matters
#   more than recognising the rare `$2 t_i$`, which is reported as undelimited.
# - The quotation alternative requires the opening `"` NOT to follow a digit,
#   so an inch/second mark (`6"`) cannot pair with the next real quote and
#   swallow the text in between.
_MATH_EXEMPT_SPAN_RE = re.compile(
    r"```.*?```|~~~.*?~~~|\$\$.*?\$\$|\\\[.*?\\\]|\\\(.*?\\\)"
    r"|\$(?![\s\d])(?=[^$\n]*[\\\\_^{])[^$\n]*?\$|`[^`\n]+`|(?<!\d)\"[^\"\n]*\"",
    re.DOTALL,
)

# A symbol carrying a sub- or superscript. Three shapes, tuned to catch the
# maths writers actually produce without sweeping up snake_case identifiers:
#   1. a ONE-char base with any script — `t_i`, `Q^2`, `G_{i,j}`, `Q^(-5/2)`;
#   2. a TWO-char base, unless the script is a lowercase word — keeps `Re_x`,
#      drops `is_boilerplate` and `to_date`;
#   3. a LONGER base whose script starts uppercase / digit / bracket — the
#      shape spelled-out Greek takes (`sigma_D`, `sigma^{2}`) and that a
#      snake_case identifier does not. (`Q_nu` needs no rule of its own:
#      its one-character base already matches alternative 1.)
_UNDELIMITED_SCRIPT_RE = re.compile(
    r"(?<![\w\\])(?:"
    r"[A-Za-zͰ-Ͽ][_^][A-Za-z0-9{(−+-]"
    r"|[A-Za-zͰ-Ͽ]{2}[_^](?![a-z]{3})[A-Za-z0-9{(−+-]"
    r"|[A-Za-zͰ-Ͽ]{3,12}[_^][A-Z0-9{(]"
    r")"
)

# Maths characters pasted as Unicode instead of written as LaTeX commands.
# U+00B5 (micro sign) is excluded outright and U+03BC (mu) is exempt before an
# ASCII letter, so unit strings like `100 µm` / `5 μs` stay plain text.
_UNICODE_MATH_RE = re.compile(
    r"[Α-Ωα-λν-ω]"
    r"|μ(?![A-Za-z])"
    r"|[−≈∝∼≃≅≤≥≠±∓"
    r"×÷∫∑∏√∞∂∇⟨⟩⋅]"
)

MATH_WARNING_EXAMPLES = 5


def _undelimited_math_findings(body: str) -> list[dict]:
    """Count mathematical notation left outside a math region in the prose."""
    prose = _MATH_EXEMPT_SPAN_RE.sub(
        lambda m: " " * (m.end() - m.start()), _strip_references_section(body)
    )
    hits = sorted(
        [(m.start(), m.group(0)) for m in _UNDELIMITED_SCRIPT_RE.finditer(prose)]
        + [(m.start(), m.group(0)) for m in _UNICODE_MATH_RE.finditer(prose)]
    )
    if not hits:
        return []
    examples = [
        " ".join(prose[max(0, pos - 30): pos + 30].split())
        for pos, _ in hits[:MATH_WARNING_EXAMPLES]
    ]
    return [
        {
            "path": "body_markdown",
            "code": "undelimited_math",
            "message": (
                f"{len(hits)} mathematical symbol(s) in prose are not inside a "
                "math region; KaTeX renders only `$...$` / `$$...$$` / "
                "`\\(...\\)` / `\\[...\\]`, so these appear as literal text. "
                "Delimit every symbol, subscript, superscript and relation, "
                "and write maths characters as LaTeX commands "
                "(`\\sigma`, `\\approx`, `\\propto`) rather than pasting "
                f"Unicode. First occurrences: {examples}"
            ),
        }
    ]


def validate_response_data(draft_data: dict, response_data: dict) -> dict:
    """Run every check on raw draft + response dicts. Does not touch disk.

    Used by ``wikify draft check --dry-run`` so a writer subagent can
    pre-validate a response candidate before committing it to disk.
    Returns the verdict dict in the same shape as ``validate_response``.
    """
    draft_data = _strip_envelope(draft_data)
    response_data = _strip_envelope(response_data)
    return _run_checks(draft_data, response_data, draft_p="", response_p="")


def validate_response(bundle: Bundle, slug: str) -> dict:
    """Run every check on draft.json + response.json and write
    validation.json. Returns the verdict dict.
    """
    draft_p = draft_path(bundle, slug)
    response_p = response_path(bundle, slug)

    draft_data = _strip_envelope(read_json(draft_p))
    response_data = _strip_envelope(read_json(response_p))

    verdict = _run_checks(
        draft_data, response_data,
        draft_p=str(draft_p), response_p=str(response_p),
    )
    write_json(validation_path(bundle, slug), verdict)
    return verdict


def _run_checks(
    draft_data: dict,
    response_data: dict,
    *,
    draft_p: str,
    response_p: str,
) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []
    structural: dict[str, bool] = {}

    # --- WriteRequest ----------------------------------------------------
    try:
        draft = WriteRequest.model_validate(draft_data)
        structural["draft_schema"] = True
    except ValidationError as exc:
        errors.extend(_pydantic_errors(exc))
        structural["draft_schema"] = False
        draft = None

    # --- WriteResponse ---------------------------------------------------
    try:
        response = WriteResponse.model_validate(response_data)
        structural["response_schema"] = True
    except ValidationError as exc:
        errors.extend(_pydantic_errors(exc))
        structural["response_schema"] = False
        response = None

    page_id = ""
    if draft is not None:
        page_id = draft.page_id
    elif response is not None:
        page_id = response.page_id

    # --- Structural checks ---------------------------------------------
    if response is not None:
        try:
            _check_wikipedia_structure(response.body_markdown, page_kind=response.page_kind)
            structural["wikipedia_structure"] = True
        except (ValueError, ValidationError) as exc:
            structural["wikipedia_structure"] = False
            errors.append(
                {
                    "path": "body_markdown",
                    "code": "wikipedia_structure",
                    "message": str(exc),
                }
            )
        try:
            _check_figure_mentions(response.body_markdown)
            structural["figure_mentions"] = True
        except (ValueError, ValidationError) as exc:
            structural["figure_mentions"] = False
            errors.append(
                {
                    "path": "body_markdown",
                    "code": "figure_mentions",
                    "message": str(exc),
                }
            )
        # Reject literal \uXXXX escape sequences in prose fields. JSON
        # output is UTF-8; emit unicode characters directly instead of
        # JSON-style escapes (they render as six-character garbage).
        prose_fields = {"body_markdown": response.body_markdown}
        for field_name, field_text in prose_fields.items():
            hit = _UNICODE_ESCAPE_RE.search(field_text)
            if hit:
                structural["no_unicode_escapes"] = False
                errors.append(
                    {
                        "path": field_name,
                        "code": "literal_unicode_escape",
                        "message": (
                            f"prose contains a literal \\uXXXX escape sequence "
                            f"({hit.group()!r}); emit the unicode character "
                            "directly instead"
                        ),
                    }
                )
            else:
                structural.setdefault("no_unicode_escapes", True)

        # Reject internal machinery leaked into prose: a Python/JSON mapping
        # literal (e.g. a writer pasting its citation-context scratch dict
        # ``{'e1_cid': '...'}`` into the body). Prose is for readers; internal
        # identifiers belong only in the ``## References`` footnote bodies.
        body_wo_refs = _strip_references_section(response.body_markdown)
        map_hit = _STRAY_MAPPING_RE.search(body_wo_refs)
        if map_hit:
            structural["no_stray_machinery"] = False
            errors.append(
                {
                    "path": "body_markdown",
                    "code": "stray_internal_machinery",
                    "message": (
                        "prose contains a leaked mapping literal "
                        f"({map_hit.group()[:40]!r}...); remove internal "
                        "data structures from the article text"
                    ),
                }
            )
        else:
            structural.setdefault("no_stray_machinery", True)

        # Presentation signal (warning only): maths left outside a math region
        # renders as literal text. Does not flip ``ok``.
        math_findings = _undelimited_math_findings(response.body_markdown)
        warnings.extend(math_findings)
        structural["math_delimited"] = not math_findings

    # --- Quote grounding ------------------------------------------------
    if draft is not None and response is not None:
        try:
            grounding_errors = _quote_grounding_errors(draft, response)
        except QuoteNotInChunkError as exc:
            grounding_errors = [
                {
                    "path": "body_markdown",
                    "code": "quote_not_in_source",
                    "message": str(exc),
                }
            ]
        errors.extend(grounding_errors)
        structural["quote_grounding"] = not grounding_errors
        figure_errors = _figure_selection_errors(draft, response)
        errors.extend(figure_errors)
        structural["figure_selection"] = not figure_errors

        # Quality signal (warning only): a page that gathered broad evidence
        # but cites only a narrow slice of it. Does not flip ``ok``.
        coverage_findings = _evidence_coverage_findings(draft, response)
        warnings.extend(coverage_findings)
        structural["evidence_coverage"] = not coverage_findings

    return {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "ok": len(errors) == 0,
        "page_id": page_id,
        "response_path": response_p,
        "draft_path": draft_p,
        "errors": errors,
        "warnings": warnings,
        "structural_checks": structural,
        "checked_at": _utcnow(),
    }
