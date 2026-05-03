"""``wikify eval`` — metrics over a bundle's committed wiki.

Single command::

    wikify eval --bundle <bundle> [--corpus <corpus>] [--report <path>]

Always emits the corpus-free subset: page counts, M3 graph
crystallinity (g_evidence + g_links), figure-reference rates, M5
hit-rate computed from ``run/events.jsonl`` (``chunk_read`` events
intersected with the chunk ids that became page evidence), and a
telemetry rollup (call cost + per-type/actor event counts).

When ``--corpus`` is supplied, the corpus-dependent metrics M1
(coverage residual) and M6 (grounding) are also computed using the
embedding backend recorded in ``corpus/vectors.meta.json``. Without
``--corpus`` those fields are emitted as ``null`` and the
``corpus_dependent_unavailable`` list names which metrics were skipped
— callers see the gap rather than fabricated zeros.

Output schema (``schema_version: 1``)::

    {
      "schema_version": 1,
      "n_articles": int,
      "n_people": int,
      "g_evidence": {modularity, spectral_gap, n_nodes, n_edges},
      "g_links":    {modularity, spectral_gap, n_nodes, n_edges},
      "figures":    {n_figures_referenced_in_bodies, n_total_captions,
                     figure_reference_rate},
      "M5_hit_rate": {value, n_chunks_read, n_chunks_used,
                      n_chunks_read_and_used},
      "telemetry":   {events_by_type, events_by_actor, calls,
                      concepts, run_closed},
      "M1_coverage_residual": float | null,
      "M6_grounding": {g1_anchoring, g2_evidence_ok, n_sentences,
                       n_markers, passes} | null,
      "corpus_dependent_unavailable": [str],   # empty when --corpus given
    }
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ..api import Bundle, Corpus
from ..bundle.wiki.page import load_bundle as load_page_bundle
from ..eval.metrics import (
    GroundingResult,
    coverage_residual,
    figure_reference_counts,
    g_links_modularity,
    grounding,
    spectral_gap_modularity,
)
from ..eval.trace_replay import load_trace, replay_stats
from ._helpers import EXIT_VALIDATION, cli_error

app = typer.Typer(add_completion=False, help="Eval metrics over a bundle.")

_REPORT_SCHEMA_VERSION = 1


def _resolve_bundle(bundle_flag: Path | None) -> Bundle:
    if bundle_flag is not None:
        try:
            return Bundle.open(bundle_flag)
        except FileNotFoundError as exc:
            cli_error(EXIT_VALIDATION, error="bad_bundle", message=str(exc))
    cwd = Path.cwd()
    try:
        return Bundle.open(cwd)
    except FileNotFoundError as exc:
        cli_error(
            EXIT_VALIDATION,
            error="no_bundle_context",
            message=f"no bundle resolved (cwd={cwd}); pass --bundle <bundle>. cause: {exc}",
        )


def _to_jsonable(value):
    """Convert NaN/inf to None so the JSON report stays valid JSON."""
    import math

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    return value


def _grounding_payload(result: GroundingResult) -> dict:
    return {
        "g1_anchoring": result.g1_anchoring,
        "g2_evidence_ok": result.g2_evidence_ok,
        "n_sentences": result.n_sentences,
        "n_markers": result.n_markers,
        "passes": result.passes,
    }


def _m5_hit_rate(bundle: Bundle, page_bundle) -> dict:
    """M5. Compute hit-rate from ``run/events.jsonl``.

    chunks_read = the union of ``chunk_id`` envelope fields across every
    ``chunk_read`` event. chunks_used = the union of ``ev.chunk_id``
    across every page's evidence. Hit rate = |used ∩ read| / |read|;
    ``value`` is None when no ``chunk_read`` events were recorded so
    callers see "no signal" rather than a fabricated zero.
    """
    trace = load_trace(bundle)
    chunks_read: set[str] = set()
    for entry in trace:
        if entry.method != "chunk_read":
            continue
        cid = entry.chunk_id or entry.data.get("chunk_id")
        if cid:
            chunks_read.add(cid)
    chunks_used = {ev.chunk_id for p in page_bundle.pages for ev in p.evidence}
    overlap = chunks_used & chunks_read
    value = (len(overlap) / len(chunks_read)) if chunks_read else None
    return {
        "value": value,
        "n_chunks_read": len(chunks_read),
        "n_chunks_used": len(chunks_used),
        "n_chunks_read_and_used": len(overlap),
    }


def _compute_corpus_metrics(page_bundle, corpus: Corpus) -> dict:
    """Compute M1 + M6 against ``corpus``.

    Returns the metric dict; raises ``cli_error`` if the corpus handle
    or its vector backend is unusable.
    """
    from ..corpus.chunks import all_chunks, read_chunks_by_id, read_vector_store
    from ..corpus.vectors_meta import meta_path_for, read_meta
    from ..embedding import embedder_for

    # Embeddings live in `wikify.db` for fresh builds; the legacy
    # `vectors.npz` is only present in older corpora. Either is fine —
    # `read_vector_store` picks whichever the corpus has.
    if not corpus.sqlite_path.exists() and not corpus.vectors_path.is_file():
        cli_error(
            EXIT_VALIDATION,
            error="corpus_missing_vectors",
            message=(
                f"corpus at {corpus.root} has no embeddings (looked for "
                f"{corpus.sqlite_path.name} and {corpus.vectors_path.name}); "
                f"M1/M6 require an embedded corpus"
            ),
        )

    meta_path = meta_path_for(corpus.vectors_path)
    if not meta_path.exists():
        cli_error(
            EXIT_VALIDATION,
            error="corpus_missing_vectors_meta",
            message=(
                f"no {meta_path.name} next to {corpus.vectors_path}; cannot reconstruct embedder"
            ),
        )
    meta = read_meta(corpus.vectors_path)

    vectors = read_vector_store(corpus)
    if vectors.matrix.shape[0] == 0:
        cli_error(
            EXIT_VALIDATION,
            error="corpus_missing_vectors",
            message=(
                f"corpus at {corpus.root} has no embedded chunks; "
                f"M1/M6 require an embedded corpus"
            ),
        )
    chunk_embeds = vectors.matrix
    embed = embedder_for(meta.backend, meta.model)

    m1 = coverage_residual(
        page_bundle, chunk_embeds, embed=embed, corpus=corpus
    )

    # Build a chunk_id -> text lookup that pulls only the chunks each
    # page actually cites; keeps the whole-corpus scan off the hot path.
    needed = {ev.chunk_id for p in page_bundle.pages for ev in p.evidence}
    if needed:
        found = {c.id: c.text for c in read_chunks_by_id(corpus, list(needed))}
    else:
        found = {}
    # Fall back to a full-corpus pass only for ids that the indexed lookup
    # missed (defensive — lets M6 exercise edge cases like manually edited
    # evidence on test fixtures without rebuilding the chunk store).
    missing = needed - found.keys()
    if missing:
        for c in all_chunks(corpus):
            if c.id in missing:
                found[c.id] = c.text
                missing.discard(c.id)
                if not missing:
                    break

    def chunk_text(chunk_id: str) -> str | None:
        return found.get(chunk_id)

    m6 = grounding(page_bundle, chunk_text)
    return {"M1_coverage_residual": float(m1), "M6_grounding": _grounding_payload(m6)}


@app.callback(invoke_without_command=True)
def cmd_eval(
    ctx: typer.Context,
    bundle: Path | None = typer.Option(None, "--bundle"),
    corpus_dir: Path | None = typer.Option(None, "--corpus"),
    report: Path | None = typer.Option(None, "--report"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Compute metrics over the bundle's committed wiki.

    Without ``--corpus`` the corpus-dependent metrics (M1, M6) are
    emitted as ``null`` and listed in ``corpus_dependent_unavailable``.
    With ``--corpus`` they are computed from the corpus's embedded
    chunks.
    """
    if ctx.invoked_subcommand is not None:
        return
    resolved = _resolve_bundle(bundle)
    page_bundle = load_page_bundle(resolved.wiki_dir)

    payload: dict = {
        "schema_version": _REPORT_SCHEMA_VERSION,
        "n_articles": len(page_bundle.concepts),
        "n_people": len(page_bundle.people),
        "g_evidence": spectral_gap_modularity(page_bundle),
        "g_links": g_links_modularity(page_bundle),
        "figures": figure_reference_counts(page_bundle),
        "M5_hit_rate": _m5_hit_rate(resolved, page_bundle),
        "telemetry": replay_stats(load_trace(resolved)),
        "M1_coverage_residual": None,
        "M6_grounding": None,
        "corpus_dependent_unavailable": ["M1", "M6"],
    }

    if corpus_dir is not None:
        if not corpus_dir.is_dir():
            cli_error(
                EXIT_VALIDATION,
                error="corpus_not_found",
                message=f"--corpus {corpus_dir} is not a directory",
            )
        corpus = Corpus(root=corpus_dir)
        extra = _compute_corpus_metrics(page_bundle, corpus)
        payload.update(extra)
        payload["corpus_dependent_unavailable"] = []

    payload = _to_jsonable(payload)

    report_path = report if report is not None else resolved.derived_dir / "eval.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "report": str(report_path), **payload}))
        return
    typer.echo(f"articles:       {payload['n_articles']}")
    typer.echo(f"people:         {payload['n_people']}")
    typer.echo(f"G_evidence Q:   {payload['g_evidence']['modularity']:.4f}")
    typer.echo(f"G_links Q:      {payload['g_links']['modularity']:.4f}")
    m5 = payload["M5_hit_rate"]
    if m5["value"] is not None:
        typer.echo(
            f"M5 hit-rate:    {m5['value']:.3f} "
            f"({m5['n_chunks_read_and_used']}/{m5['n_chunks_read']} read)"
        )
    else:
        typer.echo("M5 hit-rate:    n/a (no chunk_read events)")
    calls = payload["telemetry"]["calls"]
    typer.echo(
        f"telemetry:      {calls['n_calls']} calls, "
        f"{calls.get('total_cost_haiku_eq', 0):.1f} haiku-eq"
    )
    if payload["M1_coverage_residual"] is not None:
        typer.echo(f"M1 residual:    {payload['M1_coverage_residual']:.4f}")
    if payload["M6_grounding"] is not None:
        m6 = payload["M6_grounding"]
        typer.echo(
            f"M6 grounding:   G1={m6['g1_anchoring']:.3f} "
            f"G2={m6['g2_evidence_ok']:.3f} passes={m6['passes']}"
        )
    if payload["corpus_dependent_unavailable"]:
        typer.echo(
            "corpus-dependent unavailable: "
            + ", ".join(payload["corpus_dependent_unavailable"])
        )
    typer.echo(f"report:         {report_path}")


__all__ = ["app"]
