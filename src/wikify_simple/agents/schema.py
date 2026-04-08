"""Typed request/response shapes for Extractor / Writer / Orchestrator / Querier.

These are the only structures the bindings ever see. They are Pydantic v2
``BaseModel``s with ``frozen=True`` and ``extra="forbid"``, so a missing
or extra field aborts the call after one retry.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_EVIDENCE_HEADING = "## Evidence"
_MARKER_RE = re.compile(r"\[\^e\d+\]")
_EVIDENCE_DEF_RE = re.compile(r"^\[\^e\d+\]:")
_FIGURE_EMBED_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_FIGURE_NUM_IN_ALT_RE = re.compile(r"[Ff]igure\s+(\d+)")
_FIGURE_MENTION_TEMPLATE = r"(?:figure|fig\.?)\s*{n}\b"
_SENTENCE_END_RE = re.compile(r"[.!?](?:\s|$)")
_BULLET_RE = re.compile(r"^\s*[-*]\s+\S")
_REQUIRED_SECTIONS: tuple[str, ...] = (
    "## Definition",
    "## Mechanism",  # accept "## Mechanism" or "## Mechanism / Process"
    "## Key Facts",
    "## In This Corpus",
    "## Relationships",
    "## Open Questions",
    "## Evidence",
)
_MIN_BODY_CHARS = 800

_STRICT = ConfigDict(frozen=True, extra="forbid")


# --- exceptions ----------------------------------------------------------


class QuoteNotInChunkError(ValueError):
    """Raised by a binding when an extracted quote is not a verbatim
    substring of the ``ExtractRequest.chunk_text`` it came from.

    This is a *binding-level* check, not a schema-level check, because
    ``ExtractedConcept`` never sees the source chunk. Bindings run it
    after ``ExtractResponse.model_validate`` as a structural barrier
    against hallucinated paraphrases.
    """

    def __init__(self, *, title: str, quote_prefix: str) -> None:
        self.title = title
        self.quote_prefix = quote_prefix
        super().__init__(
            f"extracted quote for concept {title!r} is not a substring of chunk_text "
            f"(quote starts with: {quote_prefix!r})"
        )


_TITLE_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "and",
        "or",
        "to",
        "is",
        "this",
        "that",
        "these",
        "those",
    }
)
_TITLE_PUNCT = ".,;:!?\"'()[]{}<>-_/\\"

# --- extractor -----------------------------------------------------------


class ImageRef(BaseModel):
    """Lightweight image reference passed to extractors and writers.

    A flat projection of ``store.images_index.ImageRecord`` so the agents
    layer doesn't take a store dependency. The fully-qualified ``id`` is
    ``"<doc_id>/<stem>"`` and is the canonical handle for citation.
    """

    model_config = _STRICT

    id: str
    label: str | None = None
    caption: str = ""
    page: int | None = None
    path: str = ""


class ExtractRequest(BaseModel):
    model_config = _STRICT

    chunk_id: str
    chunk_text: str
    canonical_titles: list[str]  # known wiki page titles to dedup against
    prompt_template: str  # used by the cache key
    model_id: str
    tier: str  # "S" | "M" | "L"
    images_for_doc: list[ImageRef] = Field(default_factory=list)


ConfidenceLabel = Literal["extracted", "inferred", "ambiguous"]


ConceptCategory = Literal[
    "phenomenon",
    "method",
    "material",
    "device",
    "theory",
    "metric",
    "organization",
    "other",
]


class ExtractedConcept(BaseModel):
    """One concept (or person) surfaced from a single chunk.

    ``kind`` is the **page-type discriminator**: it drives directory
    routing (``concepts/<id>.md`` vs ``people/<id>.md``) and the wiki
    index. The wiki has two page kinds, period. Do not widen this.

    ``category`` is a **facet tag**, not a type. Downstream tools
    (graphify audit, M3 modularity colouring) can slice the wiki by
    category, but category never changes page routing. ``category`` is
    always ``None`` for ``kind="person"`` and optional for
    ``kind="concept"`` -- ``None`` simply means "not classified".
    """

    model_config = _STRICT

    title: str
    aliases: list[str]
    kind: Literal["concept", "person"]
    quote: str
    category: ConceptCategory | None = None
    evidence_figures: list[str] = Field(default_factory=list)
    confidence: ConfidenceLabel = "extracted"
    score: float = 1.0

    @field_validator("score")
    @classmethod
    def _score_in_unit_interval(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"ExtractedConcept.score must be in [0, 1], got {v}")
        return v

    @field_validator("title")
    @classmethod
    def _title_hygiene(cls, v: str) -> str:
        t = v.strip()
        if not (2 <= len(t) <= 120):
            raise ValueError(f"ExtractedConcept.title length {len(t)} outside [2, 120]: {t!r}")
        if t.lower() in _TITLE_STOPWORDS:
            raise ValueError(f"ExtractedConcept.title is a stopword: {t!r}")
        if t[0] in _TITLE_PUNCT or t[-1] in _TITLE_PUNCT:
            raise ValueError(f"ExtractedConcept.title has leading/trailing punctuation: {t!r}")
        return t

    @field_validator("quote")
    @classmethod
    def _quote_hygiene(cls, v: str) -> str:
        q = v.strip()
        if not (5 <= len(q) <= 400):
            raise ValueError(f"ExtractedConcept.quote length {len(q)} outside [5, 400]")
        return q

    @model_validator(mode="before")
    @classmethod
    def _normalize_aliases_and_check_person_category(cls, data):
        if not isinstance(data, dict):
            return data
        title = data.get("title", "")
        title_norm = title.strip().lower() if isinstance(title, str) else ""
        raw_aliases = data.get("aliases") or []
        out: list[str] = []
        seen: set[str] = set()
        for raw in raw_aliases:
            if not isinstance(raw, str):
                raise ValueError("ExtractedConcept.aliases entries must be str")
            a = raw.strip()
            if not a:
                continue
            key = a.lower()
            if key == title_norm or key in seen:
                continue
            seen.add(key)
            out.append(a)
            if len(out) >= 8:
                break
        data["aliases"] = out
        if data.get("kind") == "person" and data.get("category") is not None:
            raise ValueError(
                f"ExtractedConcept with kind='person' must not set category "
                f"(got {data.get('category')!r})"
            )
        return data


class ExtractResponse(BaseModel):
    model_config = _STRICT

    chunk_id: str
    concepts: list[ExtractedConcept]
    tokens_in: int
    tokens_out: int


# --- writer --------------------------------------------------------------


def _check_figure_mentions(body: str) -> None:
    """Enforce: every embedded ``![Figure N](path)`` must be textually
    referenced on the previous non-blank line.

    The writer is allowed to skip embedding figures, but if it embeds
    one, the preceding prose must mention ``Figure N`` / ``Fig N`` /
    ``fig. N`` (case-insensitive). The check looks at the nearest
    non-blank line above the embed on the same or prior line.
    """
    lines = body.splitlines()
    for idx, line in enumerate(lines):
        for m in _FIGURE_EMBED_RE.finditer(line):
            alt = line[m.start() : m.end()]
            num_match = _FIGURE_NUM_IN_ALT_RE.search(alt)
            if not num_match:
                # Not a "Figure N"-style embed; skip (e.g. generic image).
                continue
            n = num_match.group(1)
            mention_re = re.compile(_FIGURE_MENTION_TEMPLATE.format(n=n), re.IGNORECASE)
            # Candidate mention lines: the portion of the current line
            # BEFORE the embed, plus the previous non-blank line.
            before_on_line = line[: m.start()].strip()
            candidates: list[str] = []
            if before_on_line:
                candidates.append(before_on_line)
            for prev in range(idx - 1, -1, -1):
                if lines[prev].strip():
                    candidates.append(lines[prev])
                    break
            if not any(mention_re.search(c) for c in candidates):
                raise ValueError(
                    f"WriteResponse.body_markdown embeds Figure {n} without a "
                    f"textual 'Figure {n}' / 'Fig. {n}' mention on the "
                    f"preceding non-blank line"
                )


def _split_sections(body: str) -> dict[str, str]:
    """Return ``{normalized_heading: section_body}`` for every ``## Heading``.

    The normalized key is the lowercase heading text. Section body is
    the text between this heading and the next ``## ``-level heading
    (or end of file). Anything before the first ``## `` heading is
    keyed under ``""``.
    """
    out: dict[str, str] = {}
    current_key = ""
    current_buf: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            out[current_key] = "\n".join(current_buf).strip()
            current_key = line[3:].strip().lower()
            current_buf = []
        else:
            current_buf.append(line)
    out[current_key] = "\n".join(current_buf).strip()
    return out


def _count_sentences(text: str) -> int:
    return len([m for m in _SENTENCE_END_RE.finditer(text) if text[: m.end()].strip()])


def _has_section(sections: dict[str, str], prefix: str) -> tuple[str, str] | None:
    """Find a section whose lowercase heading starts with ``prefix.lower()``.

    Returns ``(matched_key, section_text)`` or ``None``. This is how we
    accept both ``## Mechanism`` and ``## Mechanism / Process``.
    """
    needle = prefix.lower()
    for key, value in sections.items():
        if key.startswith(needle):
            return key, value
    return None


def _check_wikipedia_structure(body: str) -> None:
    """Enforce the six-section Wikipedia layout from prompts/write_v1.yaml.

    Each failure raises ``ValueError`` with a message naming exactly
    which section or minimum tripped.
    """
    if len(body) < _MIN_BODY_CHARS:
        raise ValueError(
            f"WriteResponse.body_markdown is {len(body)} chars; "
            f"minimum is {_MIN_BODY_CHARS} (writer produced a stub)"
        )
    sections = _split_sections(body)
    for required in _REQUIRED_SECTIONS:
        prefix = required[3:]  # strip "## "
        if _has_section(sections, prefix) is None:
            raise ValueError(f"WriteResponse.body_markdown missing required section `{required}`")

    # Definition: at least one non-blank prose line.
    _, definition = _has_section(sections, "Definition")  # type: ignore[misc]
    if not [ln for ln in definition.splitlines() if ln.strip()]:
        raise ValueError("WriteResponse.body_markdown `## Definition` section is empty")

    # Mechanism: >=3 sentences AND >=1 [^eN] marker.
    _, mech = _has_section(sections, "Mechanism")  # type: ignore[misc]
    if _count_sentences(mech) < 3:
        raise ValueError(
            "WriteResponse.body_markdown `## Mechanism / Process` section needs >=3 sentences"
        )
    if not _MARKER_RE.search(mech):
        raise ValueError(
            "WriteResponse.body_markdown `## Mechanism / Process` section "
            "needs >=1 `[^eN]` evidence marker"
        )

    # Key Facts: >=3 bullet lines.
    _, facts = _has_section(sections, "Key Facts")  # type: ignore[misc]
    bullets = [ln for ln in facts.splitlines() if _BULLET_RE.match(ln)]
    if len(bullets) < 3:
        raise ValueError(
            f"WriteResponse.body_markdown `## Key Facts` section needs >=3 "
            f"bullet lines (got {len(bullets)})"
        )

    # In This Corpus: >=1 non-blank prose line.
    _, corpus = _has_section(sections, "In This Corpus")  # type: ignore[misc]
    if not [ln for ln in corpus.splitlines() if ln.strip()]:
        raise ValueError("WriteResponse.body_markdown `## In This Corpus` section is empty")

    # Open Questions: >=1 non-blank prose line.
    _, oq = _has_section(sections, "Open Questions")  # type: ignore[misc]
    if not [ln for ln in oq.splitlines() if ln.strip()]:
        raise ValueError("WriteResponse.body_markdown `## Open Questions` section is empty")


class WriteEvidenceRef(BaseModel):
    model_config = _STRICT

    chunk_id: str
    doc_id: str
    quote: str
    locator: str = ""


class WriteRequest(BaseModel):
    model_config = _STRICT

    page_id: str
    page_kind: str  # "concept" | "person"
    title: str
    aliases: list[str]
    skeleton: str
    evidence: list[WriteEvidenceRef]
    neighbor_titles: list[str]
    prompt_template: str
    model_id: str
    tier: str
    figures: list[ImageRef] = Field(default_factory=list)


class WriteResponse(BaseModel):
    model_config = _STRICT

    page_id: str
    body_markdown: str
    used_markers: list[str]
    tokens_in: int
    tokens_out: int

    @field_validator("body_markdown")
    @classmethod
    def _body_has_prose_and_evidence(cls, v: str) -> str:
        """Reject empty / stub / structurally-invalid writer output.

        Enforces both the original prose-and-evidence floor (the
        ``## Evidence`` block must be present and well-formed, every
        ``[^eN]`` marker in the prose must have a matching definition,
        and the figure-mention rule still fires) AND the full
        Wikipedia-style six-section layout produced by
        prompts/write_v1.yaml.
        """
        if _EVIDENCE_HEADING not in v:
            raise ValueError("WriteResponse.body_markdown missing `## Evidence` heading")
        prose_part, _, evidence_part = v.partition(_EVIDENCE_HEADING)
        prose_markers = set(_MARKER_RE.findall(prose_part))
        if not prose_markers:
            raise ValueError("WriteResponse.body_markdown prose has no `[^eN]` evidence markers")
        ev_lines = [ln.strip() for ln in evidence_part.splitlines() if ln.strip()]
        ev_defs = [ln for ln in ev_lines if _EVIDENCE_DEF_RE.match(ln)]
        if not ev_defs:
            raise ValueError(
                "WriteResponse.body_markdown `## Evidence` block has no `[^eN]:` definitions"
            )
        defined = {ln.split("]:", 1)[0] + "]" for ln in ev_defs}
        unmatched = sorted(prose_markers - defined)
        if unmatched:
            raise ValueError(
                f"WriteResponse.body_markdown has prose markers with no matching "
                f"`[^eN]:` definitions: {unmatched}"
            )
        _check_wikipedia_structure(v)
        _check_figure_mentions(v)
        return v


# --- orchestrator --------------------------------------------------------


class OrchState(BaseModel):
    """Snapshot of run state for one orchestrator step."""

    model_config = _STRICT

    run_id: str
    n_pages: int
    n_candidates: int
    n_concepts: int = 0
    n_people: int = 0
    docs_covered: int = 0
    docs_total: int = 0
    index_path: str = ""
    last_actions: list[str] = Field(default_factory=list)


class OrchAction(BaseModel):
    model_config = _STRICT

    name: str  # walk_local | jump_uniform | ... | done
    args: dict = Field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0


# --- querier -------------------------------------------------------------


class QueryEvidence(BaseModel):
    model_config = _STRICT

    page_id: str
    page_title: str
    body_excerpt: str
    citations: list[str]

    @field_validator("page_id")
    @classmethod
    def _page_id_nonempty(cls, v: str) -> str:
        if not v:
            raise ValueError("QueryEvidence.page_id must be non-empty str")
        return v


class QueryRequest(BaseModel):
    model_config = _STRICT

    question: str
    evidence: list[QueryEvidence]
    prompt_template: str
    model_id: str
    tier: str

    @field_validator("question")
    @classmethod
    def _question_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("QueryRequest.question must be non-empty str")
        return v


class QueryAnswer(BaseModel):
    model_config = _STRICT

    text: str
    citations: list[str]
    chunks: list[str]
    follow_ups: list[str] = Field(default_factory=list)


class QueryResponse(BaseModel):
    model_config = _STRICT

    answer: QueryAnswer
    tokens_in: int = 0
    tokens_out: int = 0
