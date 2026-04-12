"""Deterministic fakes for tests + dry runs.

The fake extractor returns one canned concept per chunk built from the
chunk's leading words; the fake writer echoes the skeleton (or a stub
prose body) plus the supplied evidence; the fake orchestrator picks
``done`` after a small fixed number of steps.

All three respect the ExtractCache + CostMeter contract: every call goes
through ``meter.record`` so the run accounting is honest, and extract
calls go through ``cache.get_or_extract`` so cache semantics are
exercised.
"""

import re
import time

from wikify.types import Extractor, Orchestrator, Querier, Writer
from wikify.types import Role
from wikify.context import response_reserve, total_context
from wikify.schema import (
    ExtractedConcept,
    ExtractRequest,
    ExtractResponse,
    OrchAction,
    OrchState,
    QueryAnswer,
    QueryRequest,
    QueryResponse,
    WriteRequest,
    WriteResponse,
)
from wikify.cache import CachedExtract, ExtractCache, ExtractCacheKey, prompt_hash
from wikify.meter import CostMeter

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]+")


class FakeExtractor(Extractor):
    BINDING_NAME = "fake"

    def __init__(self, cache: ExtractCache, meter: CostMeter) -> None:
        self._cache = cache
        self._meter = meter

    def extract(self, request: ExtractRequest) -> ExtractResponse:
        key = ExtractCacheKey(
            binding_name=self.BINDING_NAME,
            model_id=request.model_id,
            prompt_hash=prompt_hash(request.prompt_template),
            chunk_id=request.chunk_id,
        )
        t0 = time.monotonic()

        # Figures/images add meaningful prompt bulk: account for that
        # here so cache cost and meter cost both reflect payload size.
        image_tokens = 40 * len(request.images_for_doc)
        tokens_in_est = 200 + image_tokens

        def compute() -> CachedExtract:
            payload = _fake_extract_payload(request)
            return CachedExtract(payload=payload, tokens_in=tokens_in_est, tokens_out=80)

        entry, was_hit = self._cache.get_or_extract(key, compute)
        wall = time.monotonic() - t0
        self._meter.record(
            role=Role.EXTRACTOR,
            tier=request.tier,
            input_tokens=entry.tokens_in,
            output_tokens=entry.tokens_out,
            context_cap=total_context() - response_reserve(),
            wall_seconds=wall,
            cache_hit=was_hit,
            prompt_hash=key.prompt_hash,
        )
        payload = entry.payload
        concepts = [
            ExtractedConcept(
                title=c["title"],
                aliases=c["aliases"],
                kind=c["kind"],
                quote=c["quote"],
                category=c.get("category"),
            )
            for c in payload["concepts"]
        ]
        return ExtractResponse(
            chunk_id=payload["chunk_id"],
            concepts=concepts,
            tokens_in=entry.tokens_in,
            tokens_out=entry.tokens_out,
        )


def _fake_extract_payload(request: ExtractRequest) -> dict:
    """Pull two longest tokens from the chunk; promote them as concept candidates.

    The first token is a concept; if a capitalised bigram looks like a name in
    the original text we also emit a person.
    """
    text = request.chunk_text
    tokens = [t.lower() for t in _TOKEN_RE.findall(text) if len(t) >= 5]
    seen: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.append(t)
        if len(seen) >= 2:
            break
    concepts: list[dict] = []
    for idx, tok in enumerate(seen):
        # quote: first sentence containing the token, else first 80 chars
        quote = _first_sentence_with(text, tok) or text[:80]
        concepts.append(
            {
                "title": tok,
                "aliases": [],
                "kind": "article",
                "quote": quote,
                # Tag the first fake concept with a facet so tests can
                # exercise the ``category`` round-trip without forcing
                # every caller to provide one.
                "category": "method" if idx == 0 else None,
            }
        )
    person = _detect_person(text)
    if person:
        concepts.append(
            {
                "title": person,
                "aliases": [],
                "kind": "person",
                "quote": _first_sentence_with(text, person.split()[-1]) or text[:80],
            }
        )
    return {
        "chunk_id": request.chunk_id,
        "concepts": concepts,
        "tokens_in": 200,
        "tokens_out": 80,
    }


def _first_sentence_with(text: str, needle: str) -> str:
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if needle.lower() in sent.lower():
            return sent.strip()[:200]
    return ""


_NAME_RE = re.compile(r"\b([A-Z][a-z]+)\s+([A-Z][a-z]+)\b")


def _detect_person(text: str) -> str | None:
    m = _NAME_RE.search(text)
    return f"{m.group(1)} {m.group(2)}" if m else None


# --- writer --------------------------------------------------------------


class FakeWriter(Writer):
    def __init__(self, meter: CostMeter) -> None:
        self._meter = meter

    def write(self, request: WriteRequest) -> WriteResponse:
        t0 = time.monotonic()
        if not request.evidence:
            raise ValueError(
                f"FakeWriter.write: {request.page_id} has no evidence; "
                "canonicalize is expected to filter unsupported pages."
            )
        used: list[str] = [f"e{i}" for i in range(1, len(request.evidence) + 1)]
        title = request.title

        if request.page_kind == "person" and request.skeleton:
            body = _fake_person_body(title, used, request.skeleton, request.evidence)
        else:
            body = _fake_concept_body(title, used, request.evidence, request.figures)
        wall = time.monotonic() - t0
        # Writer tokens scale with figures payload: each figure adds ~50
        # tokens of caption + id + path to the prompt.
        figures_tokens = 50 * len(request.figures)
        tokens_in = 300 + figures_tokens
        tokens_out = 120
        self._meter.record(
            role=Role.WRITER,
            tier=request.tier,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            context_cap=total_context() - response_reserve(),
            wall_seconds=wall,
            cache_hit=False,
            prompt_hash=prompt_hash(request.prompt_template),
        )
        return WriteResponse(
            page_id=request.page_id,
            page_kind=request.page_kind,
            body_markdown=body,
            used_markers=used,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )


def _fake_concept_body(
    title: str,
    used: list[str],
    evidence: list,
    figures: list,
) -> str:
    m1 = used[0]
    m_last = used[-1]
    background = (
        f"{title} has been studied across the supplied corpus over multiple "
        f"sources[^{m1}]. Early reports framed it as a topic of interest for "
        f"the broader community covered by the cited evidence[^{m_last}]. "
        f"Subsequent work expanded the scope to additional contexts that the "
        f"fake writer does not interpret in detail[^{m1}]. The motivation "
        f"behind the topic is captured by the supplied evidence list."
    )
    mech_lines: list[str] = [
        f"{title} operates through a sequence of well-defined steps grounded "
        f"in the supplied evidence[^{m1}].",
        f"Each step is documented in the cited corpus chunks listed below[^{m_last}].",
        f"The mechanism is reproducible across the sources reviewed for this article[^{m1}].",
        f"The fake writer renders these sentences without invoking a model[^{m_last}].",
    ]
    if figures:
        fig_path = figures[0].path or "images/fig1.png"
        mech_lines.append(
            f"As shown in Figure 1, the mechanism is visible in the corpus evidence[^{m1}]."
        )
        mech_lines.append(f"![Figure 1]({fig_path})")
    mechanism = "\n\n".join(mech_lines)
    applications = (
        f"{title} is applied across the cases described in the cited "
        f"chunks[^{m1}]. Practitioners reference it in the contexts "
        f"surfaced by the supplied evidence list[^{m_last}]. The fake "
        f"writer does not enumerate specific deployments beyond the "
        f"structural placeholder text."
    )
    evidence_block_lines = [
        f"[^{marker}]: {ev.quote or 'supporting quote'} ({ev.doc_id})"
        for marker, ev in zip(used, evidence, strict=False)
    ]
    evidence_block = "\n".join(evidence_block_lines)
    return (
        f"# {title}\n\n"
        f"## Definition\n\n"
        f"{title} is a placeholder concept rendered by the fake writer "
        f"for structural validation. It is not real prose.\n\n"
        f"## Background\n\n"
        f"{background}\n\n"
        f"## Mechanism / Process\n\n"
        f"{mechanism}\n\n"
        f"## Applications\n\n"
        f"{applications}\n\n"
        f"## Open Questions\n\n"
        f"The fake writer does not assess open questions; this stub exists "
        f"only to satisfy the structural validator.\n\n"
        f"## References\n\n"
        f"{evidence_block}\n"
    )


def _fake_person_body(
    title: str,
    used: list[str],
    skeleton: str,
    evidence: list,
) -> str:
    """Two-tier person page: model-enriched lead + skeleton Tier 1 + refs."""
    m1, m_last = used[0], used[-1]
    lead = (
        f"**{title}** is a notable figure discussed across multiple sources "
        f"in this corpus[^{m1}]. The supplied evidence describes their "
        f"contributions and role within the domain covered by the corpus "
        f"documents[^{m_last}]. Their work is referenced in the context of "
        f"broader developments documented across the cited sources and "
        f"related materials in the corpus collection[^{m1}]."
    )
    research_focus = (
        f"{title} is primarily associated with research and practice "
        f"described in the supplied evidence[^{m1}]. The corpus documents "
        f"their involvement across multiple topics and contexts that span "
        f"the breadth of the collection[^{m_last}]. Their contributions "
        f"cover the areas represented by the cited corpus chunks and "
        f"related source materials[^{m1}]. The scope of their work as "
        f"documented in the evidence is broad and covers several "
        f"interconnected themes[^{m_last}]."
    )
    significance = (
        f"{title} is notable in this corpus for the breadth of references "
        f"to their work across the collected sources[^{m1}]. Multiple "
        f"documents cite or discuss their contributions in substantive "
        f"detail that spans the major themes of the corpus[^{m_last}]. "
        f"The fake writer does not interpret the evidence further but "
        f"notes that the coverage is consistent across sources[^{m1}]."
    )
    tier1 = _extract_skeleton_sections(skeleton)
    ev_block = "\n".join(
        f"[^{marker}]: {ev.quote or 'supporting quote'} ({ev.doc_id})"
        for marker, ev in zip(used, evidence, strict=False)
    )
    parts = [
        f"# {title}\n",
        lead,
        "",
        "## Research focus\n",
        research_focus,
        "",
        "## Significance\n",
        significance,
    ]
    if tier1:
        parts += ["", tier1]
    parts += ["", f"## References\n\n{ev_block}\n"]
    return "\n".join(parts)


_SKELETON_KEEP = (
    "## Notable contributions",
    "## Publications in this corpus",
    "## Cited works in this corpus",
    "## Collaborators",
)


_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def _extract_skeleton_sections(skeleton: str) -> str:
    """Pull Tier 1 sections from the skeleton, stripping wikilinks.

    The writer body must stay free of ``[[wikilinks]]`` (the crosslink
    pass adds them via frontmatter). We convert ``[[Title]]`` to plain
    ``Title`` so the validator accepts the output.
    """
    lines = skeleton.splitlines()
    keep: list[str] = []
    in_section = False
    for line in lines:
        if line.startswith("## "):
            in_section = any(line.startswith(h) for h in _SKELETON_KEEP)
        if in_section:
            keep.append(_WIKILINK_RE.sub(r"\1", line))
    return "\n".join(keep).strip()


# --- orchestrator --------------------------------------------------------


class FakeOrchestrator(Orchestrator):
    def __init__(self, meter: CostMeter, max_steps: int = 4) -> None:
        self._meter = meter
        self._max_steps = max_steps
        self._steps = 0

    def step(self, state: OrchState) -> OrchAction:
        self._steps += 1
        t0 = time.monotonic()
        name = "done" if self._steps >= self._max_steps else "jump_uniform"
        self._meter.record(
            role=Role.ORCHESTRATOR,
            tier="L",
            input_tokens=400,
            output_tokens=40,
            context_cap=total_context() - response_reserve(),
            wall_seconds=time.monotonic() - t0,
            cache_hit=False,
            prompt_hash="fake",
        )
        return OrchAction(name=name, args={"n_docs": 2}, tokens_in=400, tokens_out=40)


# --- querier -------------------------------------------------------------


class FakeQuerier(Querier):
    """Deterministic stub. No randomness, no I/O, no model calls."""

    def answer(self, request: QueryRequest) -> QueryResponse:
        n = len(request.evidence)
        text = f"[fake] question='{request.question}' supported by {n} pages"
        citations = [ev.page_id for ev in request.evidence]
        chunks: list[str] = []
        for ev in request.evidence:
            for c in ev.citations:
                if c not in chunks:
                    chunks.append(c)
        return QueryResponse(
            answer=QueryAnswer(
                text=text,
                citations=citations,
                chunks=chunks,
                follow_ups=[],
            )
        )
