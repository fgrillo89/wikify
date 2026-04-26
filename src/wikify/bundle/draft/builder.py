"""DraftBuilder — assemble ``draft.json`` from ``work.md`` + ``evidence.jsonl``.

Strategy stays in skills. ``model_id`` and ``tier`` are required
parameters of :func:`build_draft`; the CLI exposes ``--model-id`` and
``--tier`` flags so the skill or the agent must supply them
explicitly. Python never picks a default model.

What this builder DOES populate:
- page_id / page_kind / title / aliases (from work.md frontmatter)
- evidence + evidence_v2 (from evidence.jsonl + corpus chunk text)
- model_id / tier (caller-supplied)

What is left empty (set by the writer skill before invocation):
- style_guide / field_guide / artifact_template / corpus_persona
  and their hashes
- author_context (person pages)
- dossier_context_yaml / related_pages / equations_context
- prompt_template / skeleton
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


def build_draft(
    bundle: Bundle,
    *,
    slug: str,
    corpus: Corpus,
    model_id: str,
    tier: ModelTier | str,
    task: Literal["create", "refine"] = "create",
) -> WriteRequest:
    """Assemble a ``WriteRequest`` for *slug* and write it to draft.json.

    Strategy knobs (``model_id``, ``tier``, ``task``) are required;
    this function never picks them.
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
        section_type = chunk.section_type if chunk is not None else ""
        evidence_v2.append(
            WriteEvidenceRefV2(
                chunk_id=rec.chunk_id,
                doc_id=rec.doc_id,
                quote=rec.quote,
                chunk_text=chunk_text,
                section_type=section_type,
            )
        )

    tier_value = tier if isinstance(tier, ModelTier) else ModelTier(tier)
    request = WriteRequest(
        page_id=card.page_id,
        page_kind=card.kind,
        title=card.page_id,
        aliases=card.aliases,
        skeleton="",
        evidence=legacy_evidence,
        prompt_template="",
        model_id=model_id,
        tier=tier_value,
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
