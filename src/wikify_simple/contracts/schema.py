"""Typed request/response shapes for Extractor / Writer / Orchestrator / Querier.

These are the only structures the bindings ever see. They are Pydantic v2
``BaseModel``s with ``frozen=True`` and ``extra="forbid"``, so a missing
or extra field aborts the call after one retry.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_REFERENCES_HEADING = "## References"
_MARKER_RE = re.compile(r"\[\^e\d+\]")
_EVIDENCE_DEF_RE = re.compile(r"^\[\^e\d+\]:")
_WIKILINK_RE = re.compile(r"\[\[[^\]]+\]\]")
_FIGURE_EMBED_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_FIGURE_NUM_IN_ALT_RE = re.compile(r"[Ff]igure\s+(\d+)")
_FIGURE_MENTION_TEMPLATE = r"(?:figure|fig\.?)\s*{n}\b"
_MIN_BODY_CHARS = 1200

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


class Parameter(BaseModel):
    """A quantitative value extracted from the corpus."""

    model_config = _STRICT

    name: str  # e.g. "growth rate", "switching speed"
    value: str  # e.g. "0.1", "<100 ns", "12.1%"
    unit: str = ""  # e.g. "A/cycle", "ns", "%"
    conditions: str = ""  # e.g. "at 200C", "H2 plasma 20s"


class Equation(BaseModel):
    """An equation or chemical formula extracted from the corpus."""

    model_config = _STRICT

    latex: str  # e.g. "R = V/I", "HfO_2", "TiO_{2-x}"
    label: str = ""  # e.g. "(1)", "Eq. 2", "Fick's second law"
    kind: Literal["mathematical", "chemical"] = "mathematical"
    context: str = ""  # one-sentence description of what it represents


class Relationship(BaseModel):
    """A directed relationship between two concepts."""

    model_config = _STRICT

    target: str  # target concept title
    relation: str  # e.g. "enables", "part-of", "contrasts-with", "used-in"
    evidence: str = ""  # brief supporting statement


class ExtractedConcept(BaseModel):
    """One concept (or person) surfaced from a single chunk.

    ``kind`` is the **page-type discriminator**: it drives directory
    routing (``articles/<id>.md`` vs ``people/<id>.md``) and the wiki
    index. The wiki has two page kinds, period. Do not widen this.

    ``category`` is a **facet tag**, not a type. Downstream tools
    (graphify audit, M3 modularity colouring) can slice the wiki by
    category, but category never changes page routing. ``category`` is
    always ``None`` for ``kind="person"`` and optional for
    ``kind="article"`` -- ``None`` simply means "not classified".
    """

    model_config = _STRICT

    title: str
    aliases: list[str]
    kind: Literal["article", "person"]
    quote: str
    category: ConceptCategory | None = None
    evidence_figures: list[str] = Field(default_factory=list)
    confidence: ConfidenceLabel = "extracted"
    score: float = 1.0
    # Rich dossier fields (v2): optional for backwards compatibility
    definition: str = ""  # one-line definition of the concept
    summary: str = ""  # 2-3 sentence summary of what this chunk says about it
    parameters: list[Parameter] = Field(default_factory=list)
    mechanisms: list[str] = Field(default_factory=list)  # how it works
    relationships: list[Relationship] = Field(default_factory=list)
    equations: list[Equation] = Field(default_factory=list)

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


def _has_section(sections: dict[str, str], prefix: str) -> tuple[str, str] | None:
    """Find a section whose lowercase heading starts with ``prefix.lower()``.

    Returns ``(matched_key, section_text)`` or ``None``.
    """
    needle = prefix.lower()
    for key, value in sections.items():
        if key.startswith(needle):
            return key, value
    return None


_APPENDIX_LABELS: frozenset[str] = frozenset(
    {
        "references",
        "notes and references",
        "see also",
        "further reading",
        "external links",
    }
)


def _check_wikipedia_structure(body: str, page_kind: str = "") -> None:
    """Soft shape validator: sections are guidance, not strict requirements.

    Required shape:
      - >= 1200 chars
      - no `[[wikilinks]]` in prose
      - at least one H2 heading
      - for article/concept pages: at least 2 non-appendix H2 headings
      - at least 3 paragraphs of prose and at least one `[^eN]` marker
        somewhere in the body
      - a final `## References` (or equivalently named) section with at
        least one `[^eN]:` evidence definition
      - every `[^eN]` marker resolves to a definition
      - figure-mention rule (enforced separately via _check_figure_mentions)
    """
    if len(body) < _MIN_BODY_CHARS:
        raise ValueError(
            f"WriteResponse.body_markdown is {len(body)} chars; "
            f"minimum is {_MIN_BODY_CHARS} (writer produced a stub)"
        )
    if _WIKILINK_RE.search(body):
        raise ValueError(
            "WriteResponse.body_markdown contains `[[wikilink]]` markup; "
            "the body must stay clean (crosslinks live in frontmatter)"
        )
    sections = _split_sections(body)
    h2_keys = [k for k in sections if k]
    if not h2_keys:
        raise ValueError("WriteResponse.body_markdown needs at least one `## H2` heading")

    # For article pages: require at least 2 non-appendix H2 headings.
    if page_kind == "article":
        non_appendix = [k for k in h2_keys if k.strip() not in _APPENDIX_LABELS]
        if len(non_appendix) < 2:
            raise ValueError(
                "WriteResponse.body_markdown must contain at least 2 `## H2` sections "
                "before the appendix group (References / See also / etc.); "
                f"found {len(non_appendix)} non-appendix H2 heading(s). "
                "Add sections such as `## Background`, `## Mechanism`, `## Applications`."
            )

    # Locate the References section (or a case-insensitive variant).
    refs_entry = _has_section(sections, "References")
    if refs_entry is None:
        raise ValueError(
            "WriteResponse.body_markdown must end with a `## References` section "
            "containing the `[^eN]:` evidence definitions"
        )
    _, refs_body = refs_entry
    if not any(_EVIDENCE_DEF_RE.match(ln.strip()) for ln in refs_body.splitlines()):
        raise ValueError(
            "WriteResponse.body_markdown `## References` section must contain "
            "at least one `[^eN]:` evidence definition"
        )

    # Prose-body content (everything except References) needs substance:
    # >=3 non-blank paragraphs and >=1 `[^eN]` marker.
    prose_body = "\n\n".join(
        text for key, text in sections.items() if key != refs_entry[0] and text
    )
    paragraphs = [p for p in re.split(r"\n\s*\n", prose_body) if p.strip()]
    if len(paragraphs) < 3:
        raise ValueError(
            "WriteResponse.body_markdown needs at least 3 paragraphs of prose "
            "outside the References section"
        )
    if not _MARKER_RE.search(prose_body):
        raise ValueError(
            "WriteResponse.body_markdown needs at least one `[^eN]` evidence marker in the prose"
        )


class WriteEvidenceRef(BaseModel):
    model_config = _STRICT

    chunk_id: str
    doc_id: str
    quote: str
    locator: str = ""


class WriteEvidenceRefV2(BaseModel):
    """Extended evidence reference carrying full chunk context."""

    model_config = _STRICT

    chunk_id: str
    doc_id: str
    quote: str
    locator: str = ""
    chunk_text: str = ""  # full chunk text for synthesis context
    section_type: str = ""  # abstract/methods/results/conclusion
    definition: str = ""  # concept definition from dossier
    summary: str = ""  # dossier summary of this chunk's contribution
    evidence_figures: list[str] = Field(default_factory=list)  # image IDs flagged by extractor


class WriteRequest(BaseModel):
    model_config = _STRICT

    page_id: str
    page_kind: str  # "article" | "person"
    title: str
    aliases: list[str]
    skeleton: str
    evidence: list[WriteEvidenceRef]
    neighbor_titles: list[str]
    prompt_template: str
    model_id: str
    tier: str
    figures: list[ImageRef] = Field(default_factory=list)
    # Layered writer-prompt context. These are loaded once per run by
    # ``distill.pipeline.run`` and round-tripped through every dispatch.
    # They are large strings (style guide ~5k chars, field guide ~1.5k,
    # artifact template ~2k, persona ~1.5k) but the pipeline reuses the
    # same instances across all WriteRequests in a run, so the cost is
    # paid in bytes-on-the-wire, not in repeated loads.
    style_guide: str = ""
    field_guide: str = ""
    artifact_template: str = ""
    corpus_persona: str = ""
    # Editor-writer v2 fields (optional for backwards compatibility)
    brief: "EditorBrief | None" = None
    evidence_v2: list[WriteEvidenceRefV2] = Field(default_factory=list)
    neighbor_summaries: list[dict] = Field(default_factory=list)


class WriteResponse(BaseModel):
    model_config = _STRICT

    page_id: str
    page_kind: str = ""  # "concept" | "article" | "person" -- empty means unknown
    body_markdown: str
    used_markers: list[str]
    tokens_in: int
    tokens_out: int

    @model_validator(mode="after")
    def _body_has_prose_and_evidence(self) -> "WriteResponse":
        """Reject empty / stub / structurally-invalid writer output.

        Enforces the prose-and-evidence floor (the ``## References``
        block must be present and well-formed, every ``[^eN]`` marker in
        the prose must have a matching definition, and the figure-mention
        rule still fires) plus the Wikipedia-style structure produced by
        ``prompts/write.yaml``.
        """
        v = self.body_markdown
        if _REFERENCES_HEADING not in v:
            raise ValueError("WriteResponse.body_markdown missing `## References` heading")
        prose_part, _, evidence_part = v.partition(_REFERENCES_HEADING)
        prose_markers = set(_MARKER_RE.findall(prose_part))
        if not prose_markers:
            raise ValueError("WriteResponse.body_markdown prose has no `[^eN]` evidence markers")
        ev_lines = [ln.strip() for ln in evidence_part.splitlines() if ln.strip()]
        ev_defs = [ln for ln in ev_lines if _EVIDENCE_DEF_RE.match(ln)]
        if not ev_defs:
            raise ValueError(
                "WriteResponse.body_markdown `## References` block has no `[^eN]:` definitions"
            )
        defined = {ln.split("]:", 1)[0] + "]" for ln in ev_defs}
        unmatched = sorted(prose_markers - defined)
        if unmatched:
            raise ValueError(
                f"WriteResponse.body_markdown has prose markers with no matching "
                f"`[^eN]:` definitions: {unmatched}"
            )
        _check_wikipedia_structure(v, self.page_kind)
        _check_figure_mentions(v)
        return self


# --- editor (brief) ------------------------------------------------------


class BriefSection(BaseModel):
    """One section in the editor's brief to the writer."""

    model_config = _STRICT

    heading: str  # e.g. "## Mechanism" or "## Device Performance"
    instruction: str  # what to write, what to compare
    evidence_markers: list[str] = Field(default_factory=list)  # e.g. ["e1", "e3"]
    zone: Literal["established", "contested", "frontier", ""] = ""
    parameters_to_include: list[str] = Field(default_factory=list)


class EditorBrief(BaseModel):
    """The editor's structured instructions for the writer.

    The editor reads all accumulated dossier material for a concept
    and produces a brief that tells the writer exactly what to write,
    what tone to use, which evidence to cite where, and which figures
    to embed.
    """

    model_config = _STRICT

    page_id: str
    title: str
    article_register: Literal["academic", "applied", "tutorial", "general"] = "academic"
    tone_guidance: str = ""  # specific tone instructions
    lead_paragraph_instruction: str = ""  # what the opening should say
    sections: list[BriefSection] = Field(default_factory=list)
    comparative_notes: str = ""  # how this differs from related concepts
    figures_to_embed: list[str] = Field(default_factory=list)  # figure IDs
    max_length_chars: int = 5000
    tokens_in: int = 0
    tokens_out: int = 0


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
    # Sampler snapshot for Phase 3 LLM-as-sampler.
    # Contains top_gap_chunks, doc_coverage, page_index, content_stats.
    # ~2-4 kB of JSON; built in LlmPolicy.next_extract before each orch call.
    sampler_snapshot: dict = Field(default_factory=dict)


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
