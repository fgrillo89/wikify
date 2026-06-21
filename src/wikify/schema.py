"""Typed request/response shapes the skill-driven CLI consumes.

These are the canonical Pydantic v2 ``BaseModel``s for the extract and
write subagent contracts. All are ``frozen=True`` and
``extra="forbid"``, so a missing or extra field aborts the call.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .types import ModelTier

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

    ``near_chunk_ids`` lists the body chunks that mention this figure via
    inline ``Fig. N`` / ``Table N`` / ``Scheme N`` references. Extract
    handlers use it to figure out which figures the current chunk is
    discussing; writers use it to prefer figure evidence whose body
    discussion overlaps the cited evidence chunks.
    """

    model_config = _STRICT

    id: str
    label: str | None = None
    caption: str = ""
    page: int | None = None
    path: str = ""
    near_chunk_ids: list[str] = Field(default_factory=list)


class SelectedFigure(BaseModel):
    """Figure the writer elected to place in the article body.

    ``placement_anchor`` is the token used by ``{{figure:<anchor>}}`` in
    ``body_markdown``. ``figure_id`` and ``path`` must come from
    ``WriteRequest.figures``.
    """

    model_config = _STRICT

    figure_id: str
    path: str
    caption: str
    placement_anchor: str
    source_marker: str = ""


class EquationRef(BaseModel):
    """Equation occurring inside the chunk currently being extracted.

    A flat projection of one entry from ``Document.equations`` filtered
    to those whose ``char_offset`` falls inside the chunk's char_span.
    The handler uses this to (a) cite equations on extracted concepts
    via the ``equations`` field of ``ExtractedConcept`` and (b) ground
    quantitative parameter extraction in the equation's ``context``.
    """

    model_config = _STRICT

    id: str
    latex: str
    type: Literal["display", "inline", "chemical", "named", "unicode", "image"]
    label: str | None = None
    context: str = ""


class FigureCaption(BaseModel):
    """Figure / table / scheme caption near the chunk currently being extracted.

    Surfaced to the extract handler so the model can decide whether the
    figure is worth attaching as evidence (``evidence_figures`` on the
    extracted concept) without dispatching a separate vision call. We
    include captions of figures whose ``near_chunk_ids`` already point
    to the current chunk — i.e. the body explicitly mentions the figure.
    """

    model_config = _STRICT

    key: str  # e.g. "Fig. 1", "Table 2", "Scheme 3a"
    kind: Literal["figure", "table", "scheme"]
    num: int
    sub: str = ""  # "" or "a" / "b" / ...
    caption: str
    image_id: str | None = None  # populated if a binary image matched this label


class ExtractRequest(BaseModel):
    model_config = _STRICT

    chunk_id: str
    chunk_text: str
    canonical_titles: list[str]  # known wiki page titles to dedup against
    prompt_template: str  # used by the cache key
    model_id: str
    tier: ModelTier
    images_for_doc: list[ImageRef] = Field(default_factory=list)
    # Equations whose source offset falls inside this chunk's char_span.
    # Computed at ingest time via Document.equations + Chunk.equation_ids
    # and surfaced here so the model has equation context when extracting
    # parameters and concepts from a chunk.
    equations: list[EquationRef] = Field(default_factory=list)
    # Figure / table / scheme captions for figures the body discusses
    # near this chunk (i.e. the figure's near_chunk_ids contains this
    # chunk_id). Lets the handler decide which figures to attach as
    # evidence without a separate vision pass.
    figure_captions: list[FigureCaption] = Field(default_factory=list)
    # When true, the subagent must include a 1-3 sentence `reasoning`
    # field in its response explaining what it kept, skipped, and why.
    verbalize: bool = False
    # Resolved citation markers from the chunk text.
    # Each dict: {ord, title, authors, year, doi, in_corpus, corpus_doc_id}.
    citation_refs: list[dict] = Field(default_factory=list)


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
    # Rich dossier fields: optional, kept default-empty so older payloads still parse
    definition: str = ""  # one-line definition of the concept
    summary: str = ""  # 2-3 sentence summary of what this chunk says about it
    parameters: list[Parameter] = Field(default_factory=list)
    mechanisms: list[str] = Field(default_factory=list)  # how it works
    relationships: list[Relationship] = Field(default_factory=list)
    equations: list[Equation] = Field(default_factory=list)
    # Citation references relevant to this concept (bibkeys or ordinals).
    cited_refs: list[str] = Field(default_factory=list)
    # Corpus doc handles the extractor saw and judged relevant (e.g.
    # "doc:abc12345"). Used as a high-precision prior for evidence
    # gathering. Must be drawn from handles supplied in the sampled
    # bodies; do not invent handles.
    seed_doc_handles: list[str] = Field(default_factory=list)

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
    # Populated only when ExtractRequest.verbalize is true. Empty otherwise.
    reasoning: str = ""


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

    # For article and person pages: require at least 2 non-appendix H2 headings.
    if page_kind in ("article", "person"):
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
    """Evidence reference carrying full chunk context for the writer."""

    model_config = _STRICT

    chunk_id: str
    doc_id: str
    quote: str
    locator: str = ""
    chunk_text: str = ""  # full chunk text for synthesis context
    section_type: str = ""  # abstract/methods/results/conclusion
    # Retrieval score from the evidence record (higher = more relevant).
    # Used by the dossier renderer to order source papers by per-doc
    # max-score so the strongest paper leads. Zero when the workflow
    # did not record a score.
    score: float = 0.0
    # Source-doc-relative chunk ordinal (matches Chunk.ord on the
    # corpus row). Used by the dossier renderer to present chunks
    # from a single paper in narrative order (intro before methods).
    # -1 when the corpus could not be queried (lets the renderer
    # fall back to insertion order without raising).
    chunk_ord: int = -1
    definition: str = ""  # concept definition from dossier
    summary: str = ""  # dossier summary of this chunk's contribution
    evidence_figures: list[str] = Field(default_factory=list)  # image IDs flagged by extractor
    # Optional flanking chunks (ord-1 and ord+1 of the same doc), concatenated
    # for the writer's synthesis context. Empty string when the workflow did
    # not request adjacent context. Citations must still target chunk_id —
    # the validator only checks ``quote`` against ``chunk_text``, not against
    # ``context_window``.
    context_window: str = ""
    # Free-form label naming the sub-query / topic that retrieved this
    # chunk. Empty for single-concept baseline; populated by refinement
    # and exploration strategies that gather evidence via multiple
    # sub-queries on one page. The dossier renderer groups by this when
    # multiple distinct values exist.
    source: str = ""
    # LaTeX or text bodies of equations bound to this chunk via
    # ``chunk_assets``. The dossier renderer surfaces these inline so
    # the writer can wrap them in ``$...$`` regions. Empty when the
    # corpus has no equation assets for the chunk.
    chunk_equations: list[str] = Field(default_factory=list)
    # Tables referenced by the chunk's text (e.g. "Table 2") and
    # resolved against assets of asset_type='table' in the same
    # document. Each entry is the asset caption + rendered content.
    chunk_tables: list[str] = Field(default_factory=list)
    # Figure captions referenced by the chunk's text (e.g. "Figure 3")
    # and resolved against assets of asset_type='figure' in the same
    # document. Caption text only; binary image content is not
    # surfaced to the writer.
    chunk_figures: list[str] = Field(default_factory=list)


class WriteRequest(BaseModel):
    model_config = _STRICT

    page_id: str
    page_kind: str  # "article" | "person"
    title: str
    aliases: list[str]
    skeleton: str
    prompt_template: str
    model_id: str
    tier: ModelTier
    figures: list[ImageRef] = Field(default_factory=list)
    # Layered writer-prompt context. Hash fields let the runtime cache each
    # stable layer instead of resending the full text on every write call.
    style_guide: str = ""
    field_guide: str = ""
    artifact_template: str = ""
    corpus_persona: str = ""
    style_guide_hash: str | None = None
    field_guide_hash: str | None = None
    artifact_template_hash: str | None = None
    corpus_persona_hash: str | None = None
    evidence: list[WriteEvidenceRef] = Field(default_factory=list)
    neighbor_summaries: list[dict] = Field(default_factory=list)
    # Person-page grounding context. Present only when page_kind="person" and
    # the author appears in the corpus as a primary author. Context-only: never
    # emitted to disk as a standalone artifact. The writer uses it as grounded
    # facts; it is NOT directly citable (cite via evidence[i] instead).
    author_context: dict | None = None
    # Structured citation context from corpus/citation_index.json. Includes
    # source-paper BibTeX keys and a capped list of references cited by the
    # evidence sources, so writers can cite consistently without parsing BibTeX.
    citation_context: dict = Field(default_factory=dict)
    # Chunks from in-corpus cited works, pre-retrieved for deeper synthesis.
    # {corpus_doc_id: [{chunk_id, text}]}
    cited_corpus_chunks: dict = Field(default_factory=dict)
    # YAML-serialised dossier context for the writer. Compact alternative to
    # repeating the same definition/summary across each evidence entry.
    # Empty string when no dossier exists for this page.
    dossier_context_yaml: str = ""
    # Related wiki pages (top-5 by token overlap + Jaccard over evidence doc
    # ids). Each entry: {id, title, topic_overlap, body_excerpt, see_also,
    # evidence_doc_ids}. Capped at 500 chars per excerpt.
    related_pages: list[dict] = Field(default_factory=list)
    # Structured equations from the dossier, deduplicated by normalized
    # LaTeX. Each: {latex, label, kind, context, source_doc_ids}.
    # The writer should use $$ / $ delimiters and return equations it used.
    equations_context: list[dict] = Field(default_factory=list)
    # When true, the subagent must include a 1-3 sentence `reasoning`
    # field in its response explaining its editorial choices.
    verbalize: bool = False
    # Verified factual data points drawn from this page's own evidence
    # chunks, so the writer can cite specific numbers/tables via the
    # existing ``[^eN]`` marker for that chunk. Each entry:
    # {subject, property, value, unit, chunk_id}. Empty when the claim
    # store has no verified points for the gathered chunks.
    data_points: list[dict] = Field(default_factory=list)


class WriteResponse(BaseModel):
    model_config = _STRICT

    page_id: str
    page_kind: str = ""  # "article" | "person" -- empty means unknown
    body_markdown: str
    used_markers: list[str]
    tokens_in: int
    tokens_out: int
    # Non-null when the writer extended an existing article rather than
    # creating a fresh one. The value is the page_id that was extended.
    extends_page_id: str | None = None
    # Equations the writer used in the article body. Each: {latex, label,
    # kind, context}. Populated from the writer's structured output.
    equations: list[Equation] = Field(default_factory=list)
    # Figures the writer selected from WriteRequest.figures. Each one
    # must have a matching ``{{figure:<placement_anchor>}}`` placeholder
    # in body_markdown; render replaces those placeholders with figure
    # blocks and staged image assets.
    figures: list[SelectedFigure] = Field(default_factory=list)
    # Populated only when WriteRequest.verbalize is true. Empty otherwise.
    reasoning: str = ""

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


