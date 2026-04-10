"""Inline heuristic binding: no file dispatch, no polling, no model calls.

Uses regex-based concept extraction (domain-aware patterns) and
deterministic article assembly. Runs entirely in-process with zero
network or filesystem dispatch overhead.

Quality sits between the fake binding (random tokens) and a real model:
extraction catches known domain terms, writing assembles evidence into
structured prose. Good enough for pipeline validation and fast iteration;
swap to a model-backed binding for publication-quality output.
"""

from __future__ import annotations

import re
import time

from ..agents.protocols import Compactor, Editor, Extractor, Writer
from ..agents.schema import (
    BriefSection,
    EditorBrief,
    ExtractedConcept,
    ExtractRequest,
    ExtractResponse,
    WriteRequest,
    WriteResponse,
)
from ..infra.cache import CachedExtract, ExtractCache, ExtractCacheKey, prompt_hash
from ..infra.cost_meter import CostMeter
from ..infra.role import Role, response_reserve, total_context

# ---------------------------------------------------------------------------
# Concept extraction patterns
# ---------------------------------------------------------------------------

# Generic academic patterns that work across domains.
# Each tuple: (regex, canonical_title, category)
_GENERIC_PATTERNS: list[tuple[str, str, str]] = [
    (r"machine\s+learning", "Machine Learning", "method"),
    (r"deep\s+learning", "Deep Learning", "method"),
    (r"neural\s+network", "Neural Network", "method"),
    (r"transfer\s+learning", "Transfer Learning", "method"),
    (r"reinforcement\s+learning", "Reinforcement Learning", "method"),
    (r"support\s+vector\s+machine|SVM", "Support Vector Machine", "method"),
    (r"principal\s+component\s+analysis|PCA", "Principal Component Analysis", "method"),
    (r"Monte\s+Carlo", "Monte Carlo Method", "method"),
    (r"finite\s+element", "Finite Element Method", "method"),
    (r"density\s+functional\s+theory|DFT", "Density Functional Theory", "method"),
    (r"molecular\s+dynamics", "Molecular Dynamics", "method"),
    (r"X-ray\s+diffraction|XRD", "X-ray Diffraction", "method"),
    (r"scanning\s+electron\s+microscop|SEM", "Scanning Electron Microscopy", "method"),
    (r"transmission\s+electron\s+microscop|TEM", "Transmission Electron Microscopy", "method"),
    (r"atomic\s+force\s+microscop|AFM", "Atomic Force Microscopy", "method"),
    (r"photoluminescence|PL\s+spectr", "Photoluminescence", "method"),
    (r"Raman\s+spectroscop", "Raman Spectroscopy", "method"),
]

# Domain-specific patterns (memristor/ALD corpus from the original drain)
_DOMAIN_PATTERNS: list[tuple[str, str, str]] = [
    (r"memristor(?:s|ive)?", "Memristor", "device"),
    (r"resistive\s+switching", "Resistive Switching", "phenomenon"),
    (
        r"resistive\s+random\s+access\s+memory|RRAM|ReRAM",
        "Resistive Random Access Memory",
        "device",
    ),
    (r"atomic\s+layer\s+deposition|ALD", "Atomic Layer Deposition", "method"),
    (r"neuromorphic\s+computing", "Neuromorphic Computing", "method"),
    (
        r"spike[- ]timing[- ]dependent\s+plasticity|STDP",
        "Spike-Timing-Dependent Plasticity",
        "phenomenon",
    ),
    (r"paired[- ]pulse\s+facilitation|PPF", "Paired-Pulse Facilitation", "phenomenon"),
    (r"long[- ]term\s+potentiation|LTP", "Long-Term Potentiation", "phenomenon"),
    (r"long[- ]term\s+depression|LTD", "Long-Term Depression", "phenomenon"),
    (r"conductive\s+filament", "Conductive Filament", "phenomenon"),
    (r"oxygen\s+vacanc(?:y|ies)", "Oxygen Vacancy", "phenomenon"),
    (r"crossbar\s+array", "Crossbar Array", "device"),
    (r"synaptic\s+plasticity", "Synaptic Plasticity", "phenomenon"),
    (r"artificial\s+synapse", "Artificial Synapse", "device"),
    (r"HfO[2x]|hafnium\s+oxide", "Hafnium Oxide", "material"),
    (r"TiO[2x]|titanium\s+oxide", "Titanium Oxide", "material"),
    (r"ZnO|zinc\s+oxide", "Zinc Oxide", "material"),
    (r"bipolar\s+(?:resistive\s+)?switching", "Bipolar Resistive Switching", "phenomenon"),
    (r"unipolar\s+(?:resistive\s+)?switching", "Unipolar Resistive Switching", "phenomenon"),
    (r"electroforming|forming\s+(?:process|voltage)", "Electroforming", "phenomenon"),
    (r"multilevel\s+(?:switching|storage|states?)", "Multilevel Switching", "phenomenon"),
    (r"Hebbian\s+learning|Hebb.s\s+rule", "Hebbian Learning", "theory"),
    (r"von\s+Neumann\s+bottleneck", "Von Neumann Bottleneck", "theory"),
    (r"in[- ]memory\s+computing", "In-Memory Computing", "method"),
    (r"vector[- ]matrix\s+multipl", "Vector-Matrix Multiplication", "method"),
    (r"electrochemical\s+metallization", "Electrochemical Metallization", "phenomenon"),
    (r"valence\s+change\s+mechanism|VCM", "Valence Change Mechanism", "phenomenon"),
    (r"Schottky\s+(?:barrier|emission)", "Schottky Barrier", "phenomenon"),
    (r"Poole[- ]Frenkel", "Poole-Frenkel Effect", "phenomenon"),
    (r"metal[- ]insulator[- ]metal|MIM", "Metal-Insulator-Metal Structure", "device"),
]

_ALL_PATTERNS = _GENERIC_PATTERNS + _DOMAIN_PATTERNS

_NAME_RE = re.compile(r"\b([A-Z][a-z]{2,})\s+([A-Z][a-z]{2,})\b")

# Words that look like names but aren't
_NON_NAMES = {
    "the",
    "this",
    "that",
    "these",
    "those",
    "here",
    "there",
    "where",
    "when",
    "how",
    "what",
    "which",
    "who",
    "whom",
    "whose",
    "high",
    "low",
    "new",
    "old",
    "long",
    "short",
    "large",
    "small",
    "first",
    "second",
    "third",
    "last",
    "next",
    "previous",
    "based",
    "related",
    "compared",
    "induced",
    "applied",
    "observed",
    "resistive",
    "switching",
    "conductive",
    "filament",
    "oxygen",
    "atomic",
    "layer",
    "deposition",
    "neural",
    "network",
    "machine",
    "scanning",
    "electron",
    "transmission",
    "density",
    "functional",
}


def _extract_quote(text: str, match: re.Match) -> str | None:
    """Extract a verbatim quote around a regex match."""
    start = max(0, match.start() - 20)
    end = min(len(text), match.end() + 80)
    # Expand to word boundaries
    while start > 0 and text[start] not in " \n":
        start -= 1
    if start > 0:
        start += 1
    while end < len(text) and text[end] not in " \n.":
        end += 1
    quote = text[start:end].strip()
    if len(quote) > 300:
        dot = quote.find(".", 50)
        if dot > 0:
            quote = quote[: dot + 1]
    if len(quote) < 5:
        quote = match.group(0)
    if quote not in text:
        quote = match.group(0)
    return quote if quote in text else None


def _extract_concepts(chunk_text: str, canonical_titles: list[str]) -> list[dict]:
    """Extract concepts from chunk text using pattern matching."""
    seen_titles: set[str] = {t.lower() for t in canonical_titles}
    found: list[dict] = []

    for pattern, title, category in _ALL_PATTERNS:
        if title.lower() in seen_titles:
            continue
        match = re.search(pattern, chunk_text, re.IGNORECASE)
        if not match:
            continue
        quote = _extract_quote(chunk_text, match)
        if not quote:
            continue

        seen_titles.add(title.lower())
        aliases: list[str] = []
        # Detect abbreviations near the match
        abbr = re.search(
            r"\(([A-Z]{2,6})\)",
            chunk_text[max(0, match.start() - 5) : match.end() + 20],
        )
        if abbr and abbr.group(1) != title:
            aliases.append(abbr.group(1))

        found.append(
            {
                "title": title,
                "aliases": aliases,
                "kind": "concept",
                "category": category,
                "quote": quote,
            }
        )

    # Also detect person names
    for m in _NAME_RE.finditer(chunk_text):
        if len(found) >= 8:
            break
        first, last = m.group(1), m.group(2)
        if first.lower() in _NON_NAMES or last.lower() in _NON_NAMES:
            continue
        name = f"{first} {last}"
        if name.lower() in seen_titles:
            continue
        seen_titles.add(name.lower())
        quote = _extract_quote(chunk_text, m)
        if quote:
            found.append(
                {
                    "title": name,
                    "aliases": [],
                    "kind": "person",
                    "category": None,
                    "quote": quote,
                }
            )

    return found[:8]


# ---------------------------------------------------------------------------
# Article assembly
# ---------------------------------------------------------------------------


def _build_article(req: WriteRequest) -> tuple[str, list[str]]:
    """Build a structured article from evidence. Returns (body, used_markers)."""
    title = req.title
    evidence = req.evidence

    if req.page_kind == "person" and req.skeleton:
        return _build_person_article(title, req.skeleton, evidence)
    return _build_concept_article(title, evidence)


def _build_concept_article(
    title: str,
    evidence: list,
) -> tuple[str, list[str]]:
    ev_by_doc: dict[str, list[tuple[int, str]]] = {}
    for i, ev in enumerate(evidence):
        doc = ev.doc_id
        if ev.quote:
            ev_by_doc.setdefault(doc, []).append((i + 1, ev.quote))

    all_markers: list[str] = []
    lines: list[str] = []

    # Lead paragraph
    first_quotes = [(i, q) for vals in ev_by_doc.values() for i, q in vals][:3]
    lead_parts = []
    for idx, q in first_quotes:
        marker = f"e{idx}"
        all_markers.append(marker)
        q_clean = q.strip().rstrip(".")
        if q_clean and q_clean[0].islower():
            q_clean = q_clean[0].upper() + q_clean[1:]
        lead_parts.append(f"{q_clean} [^{marker}].")

    lines.append(
        f"**{title}** is a topic in the scientific literature. " + " ".join(lead_parts[:2])
    )
    lines.append("")

    # Research findings grouped by source
    doc_sections = list(ev_by_doc.items())
    if doc_sections:
        lines.append("## Research findings")
        lines.append("")
        for _doc_id, doc_evs in doc_sections[:4]:
            para_parts = []
            for idx, q in doc_evs[:3]:
                marker = f"e{idx}"
                if marker not in all_markers:
                    all_markers.append(marker)
                q_clean = q.strip().rstrip(".")
                if q_clean and q_clean[0].islower():
                    q_clean = q_clean[0].upper() + q_clean[1:]
                para_parts.append(f"{q_clean} [^{marker}].")
            lines.append(" ".join(para_parts))
            lines.append("")

    # References
    lines.append("## References")
    lines.append("")
    for i, ev in enumerate(evidence):
        marker = f"e{i + 1}"
        if marker in all_markers:
            quote = (ev.quote or "").replace('"', "'")
            lines.append(f'[^{marker}]: {ev.doc_id} > "{quote}"')

    return "\n".join(lines), all_markers


def _build_person_article(
    title: str,
    skeleton: str,
    evidence: list,
) -> tuple[str, list[str]]:
    used = [f"e{i + 1}" for i in range(len(evidence))]
    if skeleton.strip():
        body = skeleton
    else:
        body = f"# {title}\n\n{title} is a researcher referenced in this corpus."
    if evidence:
        body += "\n\n## References\n\n"
        for i, ev in enumerate(evidence):
            marker = f"e{i + 1}"
            quote = (ev.quote or "").replace('"', "'")
            body += f'[^{marker}]: {ev.doc_id} > "{quote}"\n'
    return body, used


# ---------------------------------------------------------------------------
# Protocol implementations
# ---------------------------------------------------------------------------


class HeuristicExtractor(Extractor):
    BINDING_NAME = "heuristic"

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

        def compute() -> CachedExtract:
            concepts = _extract_concepts(
                request.chunk_text,
                request.canonical_titles,
            )
            return CachedExtract(
                payload={
                    "chunk_id": request.chunk_id,
                    "concepts": concepts,
                },
                tokens_in=0,
                tokens_out=0,
            )

        t0 = time.monotonic()
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
                aliases=c.get("aliases", []),
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


class HeuristicWriter(Writer):
    def __init__(self, meter: CostMeter) -> None:
        self._meter = meter

    def write(self, request: WriteRequest) -> WriteResponse:
        t0 = time.monotonic()
        body, used = _build_article(request)
        wall = time.monotonic() - t0
        self._meter.record(
            role=Role.WRITER,
            tier=request.tier,
            input_tokens=0,
            output_tokens=0,
            context_cap=total_context() - response_reserve(),
            wall_seconds=wall,
            cache_hit=False,
            prompt_hash=prompt_hash(request.prompt_template),
        )
        return WriteResponse(
            page_id=request.page_id,
            body_markdown=body,
            used_markers=used,
            tokens_in=0,
            tokens_out=0,
        )


class HeuristicCompactor(Compactor):
    """Deterministic compaction: pick best definition, dedup, truncate."""

    def compact(self, page_id: str, title: str, entries: list[dict]) -> dict:
        definitions = [e.get("definition", "") for e in entries if e.get("definition")]
        best_def = max(definitions, key=len) if definitions else f"{title} is a concept."

        summaries = [e.get("summary", "") for e in entries if e.get("summary")]
        best_summary = max(summaries, key=len) if summaries else ""

        seen_params: dict[str, dict] = {}
        for e in entries:
            for p in e.get("parameters", []):
                k = p.get("name", "")
                if k and k not in seen_params:
                    seen_params[k] = p

        mechs = list(dict.fromkeys(m for e in entries for m in e.get("mechanisms", [])))[:6]

        seen_rels: dict[str, dict] = {}
        for e in entries:
            for r in e.get("relationships", []):
                k = r.get("target", "")
                if k and k not in seen_rels:
                    seen_rels[k] = r

        seen_docs: set[str] = set()
        top: list[dict] = []
        for e in entries:
            doc = e.get("doc_id", "")
            if doc not in seen_docs:
                seen_docs.add(doc)
                top.append(e)
            if len(top) >= 8:
                break

        return {
            "page_id": page_id,
            "definition": best_def,
            "summary": best_summary,
            "parameters": list(seen_params.values())[:10],
            "mechanisms": mechs,
            "relationships": list(seen_rels.values())[:8],
            "top_evidence": top,
            "tokens_in": 0,
            "tokens_out": 0,
        }


class HeuristicEditor(Editor):
    """Rule-based editor: greenlight all concepts with substance."""

    def edit(
        self,
        page_id: str,
        title: str,
        dossier: list[dict],
        neighbors: list[dict],
    ) -> EditorBrief:
        d = dossier[0] if dossier else {}
        sections = [
            BriefSection(
                heading="## Definition",
                instruction=f"Define {title} in one or two sentences.",
                zone="established",
            ),
            BriefSection(
                heading="## Background",
                instruction="Provide historical context and motivation.",
                zone="established",
            ),
            BriefSection(
                heading="## Mechanism",
                instruction="Explain how it works, citing evidence.",
                evidence_markers=[
                    f"e{i}" for i in range(1, min(len(d.get("evidence", [])), 5) + 1)
                ],
                zone="established",
                parameters_to_include=[p.get("name", "") for p in d.get("parameters", [])[:3]],
            ),
            BriefSection(
                heading="## Applications",
                instruction="Describe practical applications.",
                zone="established",
            ),
            BriefSection(
                heading="## Open Questions",
                instruction="Note unresolved issues.",
                zone="frontier",
            ),
        ]
        return EditorBrief(
            page_id=page_id,
            title=title,
            article_register="academic",
            tone_guidance="Neutral encyclopedic tone.",
            lead_paragraph_instruction=d.get("definition", f"Define {title}."),
            sections=sections,
            comparative_notes="",
            max_length_chars=4000,
        )
