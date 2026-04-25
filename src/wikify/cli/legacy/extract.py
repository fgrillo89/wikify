"""wikify extract ... — turn raw extract subagent outputs into session pages.

The extract phase of the baseline workflow runs N tier-S subagents over
the seed chunks. Each subagent emits an `ExtractResponse` JSON to scratch
(`<bundle>/_scratch/extract-<chunk_id>.json`). This family converts those
raw responses into deduped, canonicalized `session.pages` entries the
later draft/write loop iterates over.

`wikify extract canonicalize` is the wrapper around
`distill.dossier.canonicalize`. It loads the corpus to resolve doc_ids
for each chunk_id, hands the deduped candidate list to canonicalize,
and patches the session under the lock.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ...bundle.concepts.dossier import Candidate, canonicalize
from ...distill.preload import preload_corpus
from ...paths import CorpusPaths
from ...schema import ExtractResponse
from ...session import (
    apply_merge_patch,
    load_session,
    save_session,
    session_lock,
    touch,
)
from .._helpers import cli_owner, handle_lock_held, strip_envelope

app = typer.Typer(add_completion=False, help="Convert extract subagent outputs to session pages.")


@app.command("canonicalize")
def cmd_canonicalize(
    session_path: Path = typer.Option(..., "--session"),
    responses: str = typer.Option(
        ...,
        "--responses",
        help="JSON array of paths to extract-<chunk_id>.json files.",
    ),
    owner: str | None = typer.Option(None, "--owner"),
) -> None:
    """Dedup and canonicalize extracted concepts; append session.pages entries.

    Reads each response, resolves doc_id via the corpus chunks index,
    runs `distill.dossier.canonicalize`, and patches `session.pages`
    with one `status=planned` entry per canonical page (carrying kind +
    aliases). Idempotent: existing entries with the same `page_id` are
    not duplicated; new aliases merge into the existing entry.
    """
    paths_list = json.loads(responses)
    if not isinstance(paths_list, list) or not paths_list:
        raise typer.BadParameter("--responses must be a non-empty JSON array of paths")

    # Parse each response. Tolerate `schema_version` envelope; refuse on
    # any other validation failure so silent corruption can't slip
    # through into session.pages.
    parsed_responses: list[ExtractResponse] = []
    for raw in paths_list:
        path = Path(raw)
        if not path.is_file():
            raise typer.BadParameter(f"response file not found: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        parsed_responses.append(ExtractResponse.model_validate(strip_envelope(data)))

    session = load_session(session_path)
    preloaded = preload_corpus(CorpusPaths(Path(session.corpus_root)))
    chunks_by_id = preloaded.chunks_by_id

    # Map each (response, concept) -> Candidate.
    candidates: list[Candidate] = []
    for resp in parsed_responses:
        chunk = chunks_by_id.get(resp.chunk_id)
        if chunk is None:
            raise typer.BadParameter(
                f"unknown chunk_id {resp.chunk_id!r} (not in ingested corpus)"
            )
        for concept in resp.concepts:
            candidates.append(
                Candidate(concept=concept, chunk_id=resp.chunk_id, doc_id=chunk.doc_id)
            )

    pages = canonicalize(candidates, existing=[])

    # Build the merge patch. Existing session.pages entries are
    # preserved; new pages append; same page_id collisions update
    # aliases without touching status.
    with handle_lock_held():
        with session_lock(session_path, owner=cli_owner(owner)):
            fresh = load_session(session_path)
            existing_by_id = {p.page_id: p.model_dump(mode="json") for p in fresh.pages}
            for page in pages:
                if page.id in existing_by_id:
                    entry = existing_by_id[page.id]
                    merged_aliases = list(
                        dict.fromkeys([*entry.get("aliases", []), *page.aliases])
                    )
                    entry["aliases"] = merged_aliases
                    if not entry.get("kind"):
                        entry["kind"] = page.kind
                else:
                    existing_by_id[page.id] = {
                        "page_id": page.id,
                        "status": "planned",
                        "draft_path": None,
                        "validation_path": None,
                        "kind": page.kind,
                        "aliases": list(page.aliases),
                    }
            new_pages = list(existing_by_id.values())
            updated = apply_merge_patch(fresh, {"pages": new_pages})
            save_session(session_path, touch(updated))

    typer.echo(
        json.dumps(
            {
                "ok": True,
                "n_canonical_pages": len(pages),
                "n_session_pages": len(new_pages),
                "kinds": {
                    "article": sum(1 for p in pages if p.kind == "article"),
                    "person": sum(1 for p in pages if p.kind == "person"),
                },
            }
        )
    )


__all__ = ["app"]
