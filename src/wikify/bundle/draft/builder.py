"""DraftBuilder — assemble ``draft.json`` from ``work.md`` + ``evidence.jsonl``.

W5 MVP scope: minimal valid ``WriteRequest`` populated from the
concept folder + corpus chunk lookup. The full prompt layer
(``writer_persona``, ``style_guide``, ``artifact_template``,
``corpus_persona``, ``author_context`` for person pages, dossier
context) lives in the legacy ``cli/legacy/draft.py`` until that
file is retired in Phase C; the skill drives the writer subagent
through the assembled draft.

What we DO populate today:
- page_id / page_kind / title / aliases (from work.md frontmatter)
- evidence + evidence_v2 (from evidence.jsonl + corpus chunk text)
- model_id / tier / prompt_template / skeleton (deterministic placeholders)

What is left empty for now (set by the skill or the legacy
draft.py until Phase C absorbs it):
- style_guide / field_guide / artifact_template / corpus_persona
  and their hashes
- author_context (person pages)
- dossier_context_yaml / related_pages / equations_context
"""

from __future__ import annotations

from typing import Literal

from ...api import Bundle, Corpus
from ...corpus import queries as corpus_queries
from ...schema import WriteEvidenceRef, WriteEvidenceRefV2, WriteRequest
from ...types import ModelTier
from ..work.card import load_card
from ..work.evidence import read_evidence
from .artifact import draft_path, read_json, write_json

_DEFAULT_MODEL_ID = "claude-sonnet-4-6"
_DEFAULT_TIER = ModelTier.MEDIUM


def build_draft(
    bundle: Bundle,
    *,
    slug: str,
    corpus: Corpus,
    task: Literal["create", "refine"] = "create",
) -> WriteRequest:
    """Assemble a ``WriteRequest`` for *slug* and write it to draft.json.

    The minimum-viable flow:
    1. Load the concept's ``work.md`` (page_id / kind / aliases).
    2. Load active evidence records from ``evidence.jsonl``.
    3. For each evidence record, fetch the corpus chunk text via
       :func:`corpus.queries.get_chunk` so the writer subagent has
       full chunk context to ground its citations.
    4. Construct ``WriteRequest`` and write to ``draft.json``.
    """
    card = load_card(bundle, slug)
    if not card.front:
        raise FileNotFoundError(
            f"work/concepts/{slug}/work.md not found; create the concept first"
        )

    evidence_records = read_evidence(bundle, slug)
    active = [r for r in evidence_records if r.status == "active"]

    legacy_evidence: list[WriteEvidenceRef] = []
    evidence_v2: list[WriteEvidenceRefV2] = []
    for rec in active:
        legacy_evidence.append(
            WriteEvidenceRef(
                chunk_id=rec.chunk_id,
                doc_id=rec.doc_id,
                quote=rec.quote,
            )
        )
        chunk = corpus_queries.get_chunk(corpus, rec.chunk_id)
        chunk_text = chunk.text if chunk is not None else ""
        section_type = (
            chunk.section_type if chunk is not None else ""
        )
        evidence_v2.append(
            WriteEvidenceRefV2(
                chunk_id=rec.chunk_id,
                doc_id=rec.doc_id,
                quote=rec.quote,
                chunk_text=chunk_text,
                section_type=section_type,
            )
        )

    request = WriteRequest(
        page_id=card.page_id,
        page_kind=card.kind,
        title=card.page_id,
        aliases=card.aliases,
        skeleton="",
        evidence=legacy_evidence,
        prompt_template="",
        model_id=_DEFAULT_MODEL_ID,
        tier=_DEFAULT_TIER,
        evidence_v2=evidence_v2,
    )

    payload = request.model_dump(mode="json")
    payload["schema_version"] = 1
    payload["task"] = task
    write_json(draft_path(bundle, slug), payload)
    return request


def load_draft(bundle: Bundle, slug: str) -> WriteRequest:
    """Read ``draft.json`` and return the parsed model."""
    payload = read_json(draft_path(bundle, slug))
    payload.pop("schema_version", None)
    payload.pop("task", None)
    return WriteRequest.model_validate(payload)
