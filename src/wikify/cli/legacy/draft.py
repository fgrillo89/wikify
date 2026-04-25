"""wikify draft ... — build request artifacts the write subagent will consume."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ...api import Corpus, LegacyBundle
from ...bundle.draft.preload import preload_corpus
from ...schema import (
    WriteEvidenceRef,
    WriteEvidenceRefV2,
    WriteRequest,
)
from ...session import (
    PageEntry,
    apply_merge_patch,
    load_session,
    save_session,
    session_lock,
    touch,
)
from ...types import ModelTier
from .._helpers import cli_owner, handle_lock_held

app = typer.Typer(add_completion=False, help="Build request artifacts for the write subagent.")

# Template name string consumed by the write subagent.
WRITE_PROMPT = "wikify/write"

DRAFT_SCHEMA_VERSION = 1


@app.command("write-request")
def cmd_write_request(
    session_path: Path = typer.Option(..., "--session"),
    page_id: str = typer.Option(..., "--page-id"),
    chunk_ids: str = typer.Option(
        ...,
        "--chunk-ids",
        help="JSON array of evidence chunk ids. Typically from `wikify kg evidence`.",
    ),
    page_kind: str = typer.Option("article", "--page-kind"),
    title: str | None = typer.Option(None, "--title"),
    aliases: str = typer.Option("[]", "--aliases", help="JSON array of aliases."),
    owner: str | None = typer.Option(None, "--owner"),
) -> None:
    """Build a WriteRequest scratch artifact for one page.

    Reads the corpus via preload_corpus, assembles a token-light
    WriteRequest, writes it to <bundle>/_scratch/draft-<page_id>.json, and
    records the draft path on the session page entry.
    """
    chunk_ids_list = json.loads(chunk_ids)
    if not isinstance(chunk_ids_list, list) or not chunk_ids_list:
        raise typer.BadParameter("--chunk-ids must be a non-empty JSON array")
    aliases_list = json.loads(aliases)
    if not isinstance(aliases_list, list):
        raise typer.BadParameter("--aliases must be a JSON array")

    session = load_session(session_path)
    bundle_paths = LegacyBundle(Path(session.bundle_root))
    bundle_paths.scratch_dir.mkdir(parents=True, exist_ok=True)

    preloaded = preload_corpus(Corpus(Path(session.corpus_root)))
    missing = [cid for cid in chunk_ids_list if cid not in preloaded.chunks_by_id]
    if missing:
        raise typer.BadParameter(f"unknown chunk_ids: {missing[:5]}")

    # Evidence refs carry empty quote by convention — the canonical
    # quote lands in the subagent's response body as the quoted tail of a
    # `[^eN]: <chunk_id> (<doc_id>) > "<quote>"` line (see
    # reference/citation-format.md). `wikify validate write` parses those
    # body-defined quotes and cross-checks them against chunk_text here;
    # the request-side quote field is not consulted. The field is retained
    # to satisfy the frozen WriteRequest schema.
    evidence_refs: list[WriteEvidenceRef] = []
    evidence_v2: list[WriteEvidenceRefV2] = []
    for cid in chunk_ids_list:
        chunk = preloaded.chunks_by_id[cid]
        evidence_refs.append(
            WriteEvidenceRef(chunk_id=cid, doc_id=chunk.doc_id, quote="", locator="")
        )
        evidence_v2.append(
            WriteEvidenceRefV2(
                chunk_id=cid,
                doc_id=chunk.doc_id,
                quote="",
                chunk_text=chunk.text,
                section_type=chunk.section_type,
            )
        )

    write_tier_str = session.config.default_tiers.get("write", "M")
    try:
        write_tier = ModelTier(write_tier_str)
    except ValueError as exc:
        raise typer.BadParameter(
            f"session.config.default_tiers.write={write_tier_str!r} is not a valid ModelTier"
        ) from exc

    request = WriteRequest(
        page_id=page_id,
        page_kind=page_kind,
        title=title or page_id,
        aliases=aliases_list,
        skeleton="",
        evidence=evidence_refs,
        evidence_v2=evidence_v2,
        prompt_template=WRITE_PROMPT,
        model_id=write_tier_str,
        tier=write_tier,
    )

    draft_path = bundle_paths.scratch_dir / f"draft-{page_id}.json"
    payload = request.model_dump(mode="json")
    payload["schema_version"] = DRAFT_SCHEMA_VERSION
    draft_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    # Record the draft path on the session page entry. If the page is not
    # yet in session.pages, append it with status=drafted.
    patch = _session_patch_after_draft(session.pages, page_id, draft_path)
    with handle_lock_held():
        with session_lock(session_path, owner=cli_owner(owner)):
            fresh = load_session(session_path)
            updated = apply_merge_patch(fresh, patch)
            save_session(session_path, touch(updated))

    typer.echo(
        json.dumps(
            {
                "ok": True,
                "draft_path": str(draft_path),
                "n_evidence": len(evidence_v2),
                "schema_version": DRAFT_SCHEMA_VERSION,
            }
        )
    )


def _session_patch_after_draft(
    pages: list[PageEntry], page_id: str, draft_path: Path
) -> dict:
    """Return a JSON Merge Patch that records the draft path on the page entry."""
    new_pages = [p.model_dump(mode="json") for p in pages]
    for entry in new_pages:
        if entry["page_id"] == page_id:
            entry["status"] = "drafted"
            entry["draft_path"] = str(draft_path)
            break
    else:
        new_pages.append(
            {
                "page_id": page_id,
                "status": "drafted",
                "draft_path": str(draft_path),
                "validation_path": None,
            }
        )
    return {"pages": new_pages}


__all__ = ["app"]
