"""``wikify run ...`` — execution control for wiki bundles.

Subcommands::

    run init   --bundle <b> --corpus <c> [--strategy <s>] [--target-haiku-eq <n>]
    run show   [--run <b>] [--detail|--full] [--format text|json]
    run list events [--run <b>] [--tail <n>] [--type <t>] [--format text|json]
    run lock   --run <b> [--owner <id>]
    run unlock --run <b>
    run close  [--run <b>] [--status completed|failed|abandoned]
    run metrics --run <b> --round N [--corpus <c>]
    run stats  --run <b> [--format json|csv] [--plot <out.svg>]
    run record-call [--run <b>] --role <r> --model-id <m> --tier S|M|L
                    --tokens-in N --tokens-out N [--stage <s>]
    run record-calls --run <b> --from-stdin [--fail-fast] [--format json|compact]
    run record-event [--run <b>] --type <t> [--stage <s>] [--concept-id <c>]
                     [--page-id <p>] [--chunk-id <c>] [--doc-id <d>]
                     [--actor <a>] [--data <json>]

``--run <bundle>`` overrides; otherwise the current working directory
must be a bundle root (``run/state.json`` present).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from ..api import Bundle, Corpus
from ..bundle.run.events import Event, append_event, iter_events
from ..bundle.run.lifecycle import close_run, init_run
from ..bundle.run.lock import LockHeldError, acquire_lock, read_lock, release_lock
from ..bundle.run.state import load_state, save_state, touch
from ._helpers import EXIT_LOCK_HELD, EXIT_VALIDATION, cli_error, cli_owner

app = typer.Typer(add_completion=False, help="Run-level execution control.")


def _resolve_bundle(run_flag: Path | None) -> Bundle:
    """Resolve ``--run <bundle>`` or fall back to CWD; error on missing marker."""
    if run_flag is not None:
        try:
            return Bundle.open(run_flag)
        except FileNotFoundError as exc:
            cli_error(EXIT_VALIDATION, error="bad_bundle", message=str(exc))
    cwd = Path.cwd()
    try:
        return Bundle.open(cwd)
    except FileNotFoundError as exc:
        cli_error(
            EXIT_VALIDATION,
            error="no_bundle_context",
            message=(
                f"no bundle resolved (cwd={cwd}); pass --run <bundle> "
                f"or cd into a bundle root with run/state.json. cause: {exc}"
            ),
        )


@app.command("init")
def cmd_init(
    bundle_dir: Path = typer.Option(..., "--bundle", help="Bundle directory."),
    corpus_dir: Path = typer.Option(..., "--corpus", help="Corpus directory."),
    strategy: str = typer.Option(
        "",
        "--strategy",
        help=(
            "Free-form workflow label (e.g. baseline | guided | free | query). "
            "Passive run metadata; the agent picks. No Python branch reads this."
        ),
    ),
    target_haiku_eq: int = typer.Option(0, "--target-haiku-eq"),
    fmt: str = typer.Option("text", "--format", help="text | json"),
) -> None:
    """Create ``run/state.json`` and ``run/events.jsonl`` for a fresh bundle."""
    bundle_dir.mkdir(parents=True, exist_ok=True)
    if (bundle_dir / "run" / "state.json").is_file():
        cli_error(
            EXIT_VALIDATION,
            error="bundle_already_initialised",
            message=f"{bundle_dir} already has run/state.json; refusing to re-init",
        )
    corpus_fingerprint = Corpus(root=corpus_dir).manifest_fingerprint()
    # ``init_run`` writes ``run/state.json``; until that happens
    # ``Bundle.open`` would refuse this directory. Construct the Bundle
    # dataclass directly — ``run init`` is the privileged bootstrap path.
    bundle = Bundle(root=bundle_dir)
    state = init_run(
        bundle,
        corpus_path=corpus_dir,
        strategy=strategy,
        target_haiku_eq=target_haiku_eq,
        corpus_fingerprint=corpus_fingerprint,
    )
    # The cli_invoked event for `run init` is emitted by
    # ``_io.run_with_io_logging``: it detects ``run init --bundle <b>`` at
    # pre-flight and tees stdin/stdout/stderr into ``<b>/run/io/`` even
    # though the bundle does not yet exist. The event lands after init
    # has materialised state.json and events.jsonl.
    if fmt == "json":
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "run_id": state.run_id,
                    "bundle": str(bundle.root),
                    "state_path": str(bundle.state_path),
                    "events_path": str(bundle.events_path),
                    "corpus_fingerprint": state.corpus_fingerprint,
                }
            )
        )
    else:
        typer.echo(f"run_id:           {state.run_id}")
        typer.echo(f"bundle:           {bundle.root}")
        typer.echo(f"state:            {bundle.state_path}")
        typer.echo(f"events:           {bundle.events_path}")
        if state.corpus_fingerprint:
            typer.echo(f"corpus_fingerprint: {state.corpus_fingerprint}")


@app.command("show")
def cmd_show(
    run: Path | None = typer.Option(None, "--run"),
    detail: bool = typer.Option(False, "--detail"),
    full: bool = typer.Option(False, "--full"),
    fmt: str = typer.Option("text", "--format", help="text | json"),
) -> None:
    """Print the current run state. ``--full`` includes computed cost."""
    from ..bundle.run.cost import spent_haiku_eq

    bundle = _resolve_bundle(run)
    state = load_state(bundle)
    spent = spent_haiku_eq(bundle)
    if fmt == "json":
        out: dict = state.model_dump()
        out["budget"]["spent_haiku_eq"] = spent
        if full:
            from ..bundle.run.cost import cost_summary
            out["cost"] = cost_summary(bundle)
        typer.echo(json.dumps(out))
        return
    typer.echo(f"run_id:    {state.run_id}")
    typer.echo(f"status:    {state.status}")
    typer.echo(f"strategy:  {state.strategy}")
    typer.echo(f"corpus:    {state.corpus_path}")
    typer.echo(f"updated:   {state.updated_at}")
    if detail or full:
        typer.echo(
            f"budget:    {spent}/{state.budget.target_haiku_eq} haiku-eq"
        )
        if state.stages:
            typer.echo("stages:")
            for stage, status in state.stages.items():
                typer.echo(f"  {stage:<16} {status}")
    if full:
        from ..bundle.run.cost import cost_summary
        cost = cost_summary(bundle)
        totals = cost["totals"]
        typer.echo(
            f"cost:      {totals['calls']} calls, "
            f"{totals['haiku_eq']:.1f} haiku-eq, "
            f"{totals['input_tokens']}+{totals['output_tokens']} tokens"
        )


def _sizing_knobs(n_docs: int, n_chunks: int, n_notable_authors: int = 0) -> dict:
    """Corpus-scaled round knobs, the single source mirrored from
    ``references/sizing.md``. Surfaced in ``run sense`` so the SEED floor
    (``target_min``) and PERSON gate (``target_min/2``) are deterministic
    signals, not values the editor must re-derive from prose each round.

    ``expected_people`` scales with the corpus's notable-author pool
    (``n_notable_authors``, authors above a solid h-index) when that is
    known, falling back to the ``log(D)`` fit -- a log curve tuned for
    concept coverage badly under-counts people on authorship-rich research
    corpora. The strict person maturity gate remains the quality filter, so
    a generous target only widens who is reviewed, not who commits.
    """
    import math

    def clamp(x: float, lo: int, hi: int) -> int:
        return int(max(lo, min(hi, x)))

    d = max(int(n_docs), 1)
    kc = max(int(n_chunks), 1)
    log_d = math.log10(d)
    wave_size = clamp(math.ceil(d / 80), 2, 12)
    people_log = 4 * log_d - 3
    expected_people = clamp(round(max(people_log, 0.5 * n_notable_authors)), 0, 60)
    return {
        "n_docs": int(n_docs),
        "n_chunks": int(n_chunks),
        "n_notable_authors": int(n_notable_authors),
        "wave_size": wave_size,
        "target_min": clamp(round(42 * log_d - 27), 10, 200),
        "expected_pages": clamp(round(38 * log_d - 37), 5, 250),
        "expected_people": expected_people,
        "max_rounds": clamp(round(kc / (wave_size * 25)) + 12, 12, 250),
        "person_quota_multiplier": 2.0,
    }


@app.command("sense")
def cmd_sense(
    corpus_dir: Path = typer.Option(..., "--corpus", help="Corpus root."),
    run: Path | None = typer.Option(None, "--run"),
    current_round: int = typer.Option(0, "--round"),
    fmt: str = typer.Option("json", "--format"),
) -> None:
    """One-shot editor SENSE snapshot: budget + roster/bands + coverage + data.

    Collapses the five reads the investigate editor makes at the top of every
    round (``run show``, ``work maturity --all``, ``work coverage``,
    ``data coverage``, committed-page lookup) into a single call. Each concept
    carries a ``committed`` flag and committed pages list their slug, so the
    editor does not re-derive either from separate commands.
    """
    from collections import Counter

    from ..api import Corpus
    from ..bundle.run.cost import spent_haiku_eq
    from ..bundle.wiki.derived import list_committed_pages
    from ..bundle.work.card import list_concept_slugs
    from ..bundle.work.coverage import compute_coverage
    from ..bundle.work.maturity import compute_maturity
    from ..data.store import DataStore

    bundle = _resolve_bundle(run)
    state = load_state(bundle)
    spent = spent_haiku_eq(bundle)
    target = state.budget.target_haiku_eq

    committed_pages = list_committed_pages(bundle)
    committed_slugs = {p["slug"] for p in committed_pages}
    reports = [
        compute_maturity(bundle, s, current_round=current_round)
        for s in list_concept_slugs(bundle)
    ]
    concepts = []
    bands: Counter[str] = Counter()
    for r in reports:
        band = "committed" if r.slug in committed_slugs else r.band
        bands[band] += 1
        concepts.append({
            "slug": r.slug,
            "band": band,
            "score": round(r.score, 3),
            "gates_passed": r.gates_passed,
            "committed": r.slug in committed_slugs,
        })

    corpus = Corpus.open(corpus_dir)
    cov = compute_coverage(bundle, corpus).to_dict()
    store = DataStore.open(bundle.root)
    try:
        data_cov = store.coverage()
    finally:
        store.close()

    # Deterministic roster targets so a resuming editor is TOLD when the roster
    # is still starved -- SEED/PERSON eligibility must not depend on the editor
    # re-deriving sizing from prose each round (see references/sizing.md).
    n_docs = len(cov.get("per_doc") or {})
    n_chunks = cov.get("n_total") or 0
    # Notable-author pool: authors above a solid h-index, so the person target
    # scales with how authorship-rich the corpus is rather than a flat log(D).
    n_notable_authors = 0
    if corpus.sqlite_path.exists():
        import sqlite3 as _sqlite3
        _con = _sqlite3.connect(str(corpus.sqlite_path))
        try:
            row = _con.execute(
                "SELECT COUNT(*) FROM node_metrics WHERE node_type='author' "
                "AND metric='h_index' AND value >= 4"
            ).fetchone()
            n_notable_authors = int(row[0]) if row else 0
        except _sqlite3.Error:
            n_notable_authors = 0
        finally:
            _con.close()
    sizing = _sizing_knobs(n_docs, n_chunks, n_notable_authors)
    active_concepts = sum(
        v for k, v in bands.items() if k not in ("dropped", "parked")
    )
    n_people = sum(1 for p in committed_pages if p.get("kind") == "person")
    target_min = sizing["target_min"]
    expected_people = sizing["expected_people"]
    seed_should_fire = active_concepts < target_min
    person_gate_open = active_concepts >= target_min / 2
    waves = {
        "seed_should_fire": seed_should_fire,
        "seed_deficit": max(0, target_min - active_concepts),
        "person_gate_open": person_gate_open,
        "person_should_fire": person_gate_open
        and n_people < expected_people * sizing["person_quota_multiplier"],
        "person_deficit": max(0, expected_people - n_people),
        "roster_saturated": not seed_should_fire,
    }

    snapshot = {
        "ok": True,
        "round": current_round,
        "budget": {
            "target_haiku_eq": target,
            "spent_haiku_eq": spent,
            "remaining_haiku_eq": max(0, target - spent) if target else None,
        },
        "bands": dict(bands),
        "concepts": concepts,
        "coverage": {
            "chunk_coverage_ratio": cov.get("chunk_coverage_ratio"),
            "n_covered": cov.get("n_covered"),
            "n_total": cov.get("n_total"),
            "addressable_coverage_ratio": cov.get("addressable_coverage_ratio"),
            "n_addressable_covered": cov.get("n_addressable_covered"),
            "n_addressable": cov.get("n_addressable"),
        },
        "data": {
            k: data_cov.get(k)
            for k in ("n_points", "verified_ratio", "n_subjects",
                      "n_properties", "n_artifacts")
        },
        "sizing": sizing,
        "roster": {
            "active_concepts": active_concepts,
            "n_committed_articles": sum(
                1 for p in committed_pages if p.get("kind") == "article"
            ),
            "n_people": n_people,
        },
        "waves": waves,
        "committed_pages": committed_pages,
    }
    typer.echo(json.dumps(snapshot, ensure_ascii=False))


def _eval_m1_m3(
    bundle: Bundle, corpus_dir: Path | None
) -> tuple[float | None, float | None]:
    """M1 (coverage residual) and M3 (G_evidence modularity) by reusing
    ``wikify.eval.metrics``.

    M3 is corpus-free (evidence doc-overlap graph on the committed pages).
    M1 needs the corpus's embedded chunk vectors; it is ``None`` when no
    corpus is given or the corpus vectors are missing/unusable. Neither
    metric is reimplemented here -- both are the ``eval`` functions.
    """
    from ..bundle.wiki.page import load_bundle as load_page_bundle
    from ..eval.metrics import spectral_gap_modularity

    page_bundle = load_page_bundle(bundle.wiki_dir)
    try:
        m3: float | None = float(spectral_gap_modularity(page_bundle)["modularity"])
    except Exception:
        m3 = None

    m1: float | None = None
    if corpus_dir is not None and corpus_dir.is_dir():
        from ..corpus.chunks import read_vector_store
        from ..corpus.vectors_meta import read_meta
        from ..embedding import embedder_for
        from ..eval.metrics import EmbedderMismatch, coverage_residual

        try:
            corpus = Corpus(root=corpus_dir)
            meta = read_meta(corpus.sqlite_path) if corpus.sqlite_path.exists() else None
            vectors = read_vector_store(corpus) if meta is not None else None
            if meta is not None and vectors is not None and vectors.matrix.shape[0] > 0:
                embed = embedder_for(meta.backend, meta.model)
                m1 = float(
                    coverage_residual(
                        page_bundle, vectors.matrix, embed=embed, corpus=corpus
                    )
                )
        except (FileNotFoundError, EmbedderMismatch):
            # Corpus vectors absent / an embedder that can't match them ->
            # M1 is simply unavailable. A genuine computation error is a real
            # bug and must not be masked, so only these narrow cases -> null.
            m1 = None
    return m1, m3


def _eval_graph_structure(bundle: Bundle) -> dict[str, float]:
    """Structural metrics of the evidence-overlap graph over committed pages.

    Nodes are committed pages (the same ``bundle.pages`` set M3's
    ``G_evidence`` is built on); an edge joins two pages that share at least
    one evidence ``doc_id``. Returns ``graph_edges``, ``graph_density``
    (2E/(N(N-1))), ``graph_avg_degree`` (2E/N) and ``graph_largest_cc_frac``
    (largest connected component size / N). A bundle with 0/1 committed
    pages yields zeros, matching the M1/M3 ``n<2`` guard.
    """
    import networkx as nx

    from ..bundle.wiki.page import load_bundle as load_page_bundle

    pages = load_page_bundle(bundle.wiki_dir).pages
    n = len(pages)
    if n < 2:
        return {
            "graph_edges": 0,
            "graph_density": 0.0,
            "graph_avg_degree": 0.0,
            "graph_largest_cc_frac": 0.0,
        }
    docs = {p.id: {ev.doc_id for ev in p.evidence if ev.doc_id} for p in pages}
    ids = list(docs)
    graph = nx.Graph()
    graph.add_nodes_from(ids)
    for a in range(n):
        for b in range(a + 1, n):
            if docs[ids[a]] & docs[ids[b]]:
                graph.add_edge(ids[a], ids[b])
    edges = graph.number_of_edges()
    largest_cc = max((len(c) for c in nx.connected_components(graph)), default=0)
    return {
        "graph_edges": edges,
        "graph_density": 2 * edges / (n * (n - 1)),
        "graph_avg_degree": 2 * edges / n,
        "graph_largest_cc_frac": largest_cc / n,
    }


@app.command("metrics")
def cmd_metrics(
    round_num: int = typer.Option(..., "--round"),
    run: Path | None = typer.Option(None, "--run"),
    corpus_dir: Path | None = typer.Option(None, "--corpus"),
) -> None:
    """Compute the full metric snapshot for ``--round N`` and append it to
    ``<bundle>/derived/stats.jsonl`` (one JSON line per call).

    The snapshot bundles the cheap counts (committed pages / articles /
    people, maturity band histogram, chunk + addressable coverage, data
    points + artifacts, budget spent) with M1 (coverage residual), M3
    (G_evidence modularity) reused from ``wikify.eval.metrics``, and the
    evidence-overlap graph structure (edges, density, average degree,
    largest connected-component fraction). Coverage
    and M1 need ``--corpus``; without it those fields are ``null`` rather
    than fabricated. Recording the same round twice appends a second line;
    ``run stats`` keeps the latest per round.
    """
    from collections import Counter

    from ..bundle.run.cost import spent_haiku_eq
    from ..bundle.wiki.derived import list_committed_pages
    from ..bundle.work.card import list_concept_slugs
    from ..bundle.work.maturity import compute_maturity
    from ..data.store import DataStore

    bundle = _resolve_bundle(run)

    committed = list_committed_pages(bundle)
    committed_slugs = {p["slug"] for p in committed}
    n_articles = sum(1 for p in committed if p["kind"] == "article")
    n_people = sum(1 for p in committed if p["kind"] == "person")

    bands: Counter[str] = Counter()
    for s in list_concept_slugs(bundle):
        report = compute_maturity(bundle, s, current_round=round_num)
        band = "committed" if s in committed_slugs else report.band
        bands[band] += 1

    chunk_cov: float | None = None
    addr_cov: float | None = None
    if corpus_dir is not None:
        from ..bundle.work.coverage import compute_coverage

        cov = compute_coverage(bundle, Corpus(root=corpus_dir)).to_dict()
        chunk_cov = cov.get("chunk_coverage_ratio")
        addr_cov = cov.get("addressable_coverage_ratio")

    # Guard on the claim-store file: opening a DataStore schema-initializes
    # ``claims.db`` on disk, so a metrics call must not conjure one where the
    # DATA wave never ran. Absent store -> zero data counts.
    if bundle.claims_db_path.exists():
        store = DataStore.open(bundle.root)
        try:
            data_cov = store.coverage()
        finally:
            store.close()
    else:
        data_cov = {}

    m1, m3 = _eval_m1_m3(bundle, corpus_dir)
    graph = _eval_graph_structure(bundle)

    record = {
        "round": round_num,
        "n_committed_pages": n_articles + n_people,
        "n_articles": n_articles,
        "n_people": n_people,
        "band_counts": dict(bands),
        "chunk_coverage_ratio": chunk_cov,
        "addressable_coverage_ratio": addr_cov,
        "n_data_points": data_cov.get("n_points", 0),
        "n_data_artifacts": data_cov.get("n_artifacts", 0),
        "budget_spent_haiku_eq": spent_haiku_eq(bundle),
        "M1": m1,
        "M3": m3,
        "graph_edges": graph["graph_edges"],
        "graph_density": graph["graph_density"],
        "graph_avg_degree": graph["graph_avg_degree"],
        "graph_largest_cc_frac": graph["graph_largest_cc_frac"],
    }
    stats_path = bundle.derived_dir / "stats.jsonl"
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    with stats_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    typer.echo(json.dumps(record))


_STATS_CSV_FIELDS = (
    ("round", "round"),
    ("pages", "n_committed_pages"),
    ("chunk_cov", "chunk_coverage_ratio"),
    ("addr_cov", "addressable_coverage_ratio"),
    ("budget", "budget_spent_haiku_eq"),
    ("M1", "M1"),
    ("M3", "M3"),
    ("n_artifacts", "n_data_artifacts"),
    ("graph_edges", "graph_edges"),
    ("graph_density", "graph_density"),
    ("graph_avg_degree", "graph_avg_degree"),
    ("graph_largest_cc_frac", "graph_largest_cc_frac"),
)


def _load_stats_series(bundle: Bundle) -> list[dict]:
    """Read ``derived/stats.jsonl`` (falling back to ``round_completed``
    events), dedupe to the latest record per round, and sort by round."""
    stats_path = bundle.derived_dir / "stats.jsonl"
    records: list[dict] = []
    if stats_path.is_file():
        for line in stats_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and isinstance(rec.get("round"), int):
                records.append(rec)
    if not records:
        records = _reconstruct_stats_from_events(bundle)
    latest: dict[int, dict] = {}
    for rec in records:
        latest[rec["round"]] = rec  # later lines win
    return [latest[k] for k in sorted(latest)]


def _reconstruct_stats_from_events(bundle: Bundle) -> list[dict]:
    """Minimal per-round series rebuilt from ``round_completed`` events:
    the round counter, any budget field, and any metric fields present."""
    out: list[dict] = []
    metric_keys = (
        "n_committed_pages", "chunk_coverage_ratio",
        "addressable_coverage_ratio", "n_data_artifacts", "M1", "M3",
    )
    for ev in iter_events(bundle):
        if ev.type != "round_completed":
            continue
        data = ev.data or {}
        rnd = data.get("round")
        if not isinstance(rnd, int):
            continue
        rec: dict = {"round": rnd}
        for key in ("budget_spent_haiku_eq", "budget_used", "budget"):
            if key in data:
                rec["budget_spent_haiku_eq"] = data[key]
                break
        for key in metric_keys:
            if key in data:
                rec[key] = data[key]
        out.append(rec)
    return out


def _svg_polyline(
    series: list[tuple[int, float]],
    *,
    x0: int, y0: int, pw: int, ph: int,
    xmin: int, xmax: int, ymin: float, ymax: float,
    color: str,
) -> str:
    """One panel polyline (+ endpoint dots) mapping (round, value) into the
    box at (x0, y0) of size (pw, ph). Empty string when no points."""
    if not series:
        return ""
    xspan = (xmax - xmin) or 1
    yspan = (ymax - ymin) or 1.0
    pts = []
    for rnd, val in series:
        px = x0 + (rnd - xmin) / xspan * pw
        py = y0 + ph - (val - ymin) / yspan * ph
        pts.append((px, py))
    poly = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
    dots = "".join(
        f'<circle cx="{px:.1f}" cy="{py:.1f}" r="2.5" fill="{color}"/>'
        for px, py in pts
    )
    return (
        f'<polyline fill="none" stroke="{color}" stroke-width="2" '
        f'points="{poly}"/>{dots}'
    )


def _write_stats_svg(records: list[dict], out_path: Path) -> None:
    """Hand-rolled dependency-free SVG: two stacked panels vs round --
    addressable coverage (top) and cumulative committed pages (bottom)."""
    def series(key: str) -> list[tuple[int, float]]:
        return [
            (r["round"], float(r[key]))
            for r in records
            if isinstance(r.get(key), (int, float))
        ]

    cov = series("addressable_coverage_ratio")
    pages = series("n_committed_pages")
    rounds = [r["round"] for r in records] or [0]
    xmin, xmax = min(rounds), max(rounds)

    w, h = 480, 340
    x0, pw = 60, w - 90
    p1y, p2y, ph = 40, 200, 100
    pages_max = max((v for _, v in pages), default=1.0) or 1.0

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="monospace" font-size="11">',
        f'<rect width="{w}" height="{h}" fill="white"/>',
        f'<text x="{x0}" y="24">addressable_coverage_ratio vs round</text>',
        f'<rect x="{x0}" y="{p1y}" width="{pw}" height="{ph}" '
        f'fill="none" stroke="#ccc"/>',
        f'<text x="{x0 - 34}" y="{p1y + 6}">1.0</text>',
        f'<text x="{x0 - 34}" y="{p1y + ph}">0.0</text>',
        _svg_polyline(
            cov, x0=x0, y0=p1y, pw=pw, ph=ph,
            xmin=xmin, xmax=xmax, ymin=0.0, ymax=1.0, color="#1f77b4",
        ),
        f'<text x="{x0}" y="{p2y - 12}">committed_pages vs round</text>',
        f'<rect x="{x0}" y="{p2y}" width="{pw}" height="{ph}" '
        f'fill="none" stroke="#ccc"/>',
        f'<text x="{x0 - 34}" y="{p2y + 6}">{pages_max:.0f}</text>',
        f'<text x="{x0 - 34}" y="{p2y + ph}">0</text>',
        _svg_polyline(
            pages, x0=x0, y0=p2y, pw=pw, ph=ph,
            xmin=xmin, xmax=xmax, ymin=0.0, ymax=pages_max, color="#d62728",
        ),
        f'<text x="{x0}" y="{p2y + ph + 24}">round {xmin} .. {xmax}</text>',
        "</svg>",
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def _stats_series(records: list[dict], key: str) -> list[tuple[float, float]]:
    """(round, value) pairs where ``value`` is a present, finite number."""
    return [
        (float(r["round"]), float(r[key]))
        for r in records
        if isinstance(r.get("round"), (int, float))
        and isinstance(r.get(key), (int, float))
    ]


def _write_stats_matplotlib(records: list[dict], out_path: Path, plt) -> None:
    """Two-panel figure: committed pages + addressable coverage vs round
    (twin axes, top) and committed pages vs budget (bottom). None points are
    dropped per series; an empty series is simply omitted."""
    pages = _stats_series(records, "n_committed_pages")
    cov = _stats_series(records, "addressable_coverage_ratio")
    budget = [
        (float(r["budget_spent_haiku_eq"]), float(r["n_committed_pages"]))
        for r in records
        if isinstance(r.get("budget_spent_haiku_eq"), (int, float))
        and isinstance(r.get("n_committed_pages"), (int, float))
    ]

    fig, (ax_r, ax_b) = plt.subplots(2, 1, figsize=(7, 7))
    fig.suptitle("wikify build metrics")
    any_data = False

    if pages:
        xs, ys = zip(*pages)
        ax_r.plot(xs, ys, "o-", color="#d62728", label="committed pages")
        any_data = True
    ax_r.set_xlabel("round")
    ax_r.set_ylabel("committed pages")
    ax_r.grid(True, alpha=0.3)

    ax_cov = ax_r.twinx()
    if cov:
        xs, ys = zip(*cov)
        ax_cov.plot(
            xs, [v * 100.0 for v in ys], "s--", color="#1f77b4",
            label="addressable coverage (%)",
        )
        any_data = True
    ax_cov.set_ylabel("addressable coverage (%)")
    ax_cov.set_ylim(0, 100)

    handles = ax_r.get_legend_handles_labels()
    twin = ax_cov.get_legend_handles_labels()
    lines = handles[0] + twin[0]
    if lines:
        ax_r.legend(lines, handles[1] + twin[1], loc="upper left", fontsize=8)

    if budget:
        xs, ys = zip(*sorted(budget))
        ax_b.plot(xs, ys, "o-", color="#2ca02c", label="pages vs budget")
        ax_b.legend(loc="upper left", fontsize=8)
        any_data = True
    ax_b.set_xlabel("budget spent (haiku-eq)")
    ax_b.set_ylabel("committed pages")
    ax_b.grid(True, alpha=0.3)

    if not any_data:
        ax_r.text(
            0.5, 0.5, "no data", ha="center", va="center",
            transform=ax_r.transAxes,
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def _write_stats_plot(records: list[dict], out_path: Path) -> str:
    """Render the per-round metric chart to ``out_path``. Uses matplotlib when
    importable -- honoring the .png/.svg/.pdf extension via savefig -- and falls
    back to the hand-rolled SVG writer otherwise. Returns the renderer name."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        _write_stats_svg(records, out_path)
        return "svg"
    _write_stats_matplotlib(records, out_path, plt)
    return "matplotlib"


@app.command("stats")
def cmd_stats(
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("json", "--format", help="json | csv"),
    plot: Path | None = typer.Option(
        None, "--plot", help="Write a chart (png/svg/pdf; matplotlib else SVG)."
    ),
) -> None:
    """Retrieve the per-round metric time series from ``derived/stats.jsonl``.

    Default / ``--format json`` prints the deduped, round-sorted list of
    records. ``--format csv`` emits a header row (round, pages, chunk_cov,
    addr_cov, budget, M1, M3, n_artifacts, graph_edges, graph_density,
    graph_avg_degree, graph_largest_cc_frac) plus one row per round.
    ``--plot <out>`` writes a chart in addition to the series (matplotlib when
    importable, honoring the .png/.svg/.pdf extension; a hand-rolled SVG
    otherwise): the series is still emitted in the requested format and a
    trailing JSON status line reports the plot path and renderer. Falls back to
    reconstructing the series from ``round_completed`` events when
    ``stats.jsonl`` is absent or empty.
    """
    if fmt not in {"json", "csv"}:
        cli_error(
            EXIT_VALIDATION,
            error="bad_format",
            message=f"unknown --format {fmt!r}; expected json | csv",
        )

    bundle = _resolve_bundle(run)
    records = _load_stats_series(bundle)

    if fmt == "csv":
        lines = [",".join(name for name, _ in _STATS_CSV_FIELDS)]
        for rec in records:
            cells = []
            for _, key in _STATS_CSV_FIELDS:
                val = rec.get(key)
                cells.append("" if val is None else str(val))
            lines.append(",".join(cells))
        typer.echo("\n".join(lines))
    else:
        typer.echo(json.dumps(records))

    if plot is not None:
        renderer = _write_stats_plot(records, plot)
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "plot": str(plot),
                    "n_rounds": len(records),
                    "renderer": renderer,
                }
            )
        )


events_app = typer.Typer(add_completion=False, help="Event-ledger queries.")
app.add_typer(events_app, name="list")


@events_app.command("events")
def cmd_list_events(
    run: Path | None = typer.Option(None, "--run"),
    tail: int = typer.Option(20, "--tail"),
    type_filter: str | None = typer.Option(None, "--type"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Print the most recent events from ``run/events.jsonl``."""
    bundle = _resolve_bundle(run)
    events = list(iter_events(bundle))
    if type_filter:
        events = [e for e in events if e.type == type_filter]
    events = events[-tail:] if tail > 0 else events
    if fmt == "json":
        typer.echo(json.dumps([e.model_dump() for e in events]))
        return
    for e in events:
        actor = (e.actor or "?")[:12]
        typer.echo(f"{e.at}  {e.type:<22} {actor:<14} {e.event_id[:8]}")


@app.command("lock")
def cmd_lock(
    run: Path | None = typer.Option(None, "--run"),
    owner: str | None = typer.Option(None, "--owner"),
    ttl_seconds: int = typer.Option(3600, "--ttl-seconds"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Acquire ``run/lock`` for ``--owner`` (default: this CLI process)."""
    bundle = _resolve_bundle(run)
    try:
        acquire_lock(bundle, owner=cli_owner(owner), ttl_seconds=ttl_seconds)
    except LockHeldError as exc:
        if fmt == "json":
            typer.echo(
                json.dumps(
                    {
                        "ok": False,
                        "error": "lock_held",
                        "owner": exc.owner,
                        "acquired_at": exc.acquired_at,
                    }
                )
            )
        else:
            typer.echo(f"lock held by {exc.owner} since {exc.acquired_at}", err=True)
        raise typer.Exit(code=EXIT_LOCK_HELD) from exc
    record = read_lock(bundle) or {}
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, **record}))
    else:
        typer.echo(f"locked by {record.get('owner', '?')}")


@app.command("unlock")
def cmd_unlock(
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Release the bundle lock unconditionally."""
    bundle = _resolve_bundle(run)
    release_lock(bundle)
    if fmt == "json":
        typer.echo(json.dumps({"ok": True}))
    else:
        typer.echo("unlocked")


@app.command("close")
def cmd_close(
    run: Path | None = typer.Option(None, "--run"),
    status: str = typer.Option("completed", "--status"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Mark the run completed/failed/abandoned and emit ``run_closed``."""
    bundle = _resolve_bundle(run)
    if status not in {"completed", "failed", "abandoned"}:
        cli_error(
            EXIT_VALIDATION,
            error="bad_status",
            message="--status must be completed|failed|abandoned",
        )
    has_call_events = any(e.type == "call" for e in iter_events(bundle))
    state = close_run(bundle, status=status)
    if not has_call_events:
        typer.echo(
            "WARNING: no agent call telemetry recorded; eval cost curves will "
            "be empty. Use 'wikify run record-calls' before closing.",
            err=True,
        )
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "run_id": state.run_id, "status": state.status}))
    else:
        typer.echo(f"run {state.run_id} -> {state.status}")


@app.command("record-call")
def cmd_record_call(
    run: Path | None = typer.Option(None, "--run"),
    role: str = typer.Option(..., "--role"),
    model_id: str = typer.Option(..., "--model-id"),
    tier: str = typer.Option(..., "--tier"),
    tokens_in: int = typer.Option(..., "--tokens-in"),
    tokens_out: int = typer.Option(..., "--tokens-out"),
    stage: str = typer.Option("model_call", "--stage"),
    concept_id: str | None = typer.Option(None, "--concept-id"),
    page_id: str | None = typer.Option(None, "--page-id"),
    wall_seconds: float = typer.Option(0.0, "--wall-seconds"),
    actor: str = typer.Option("agent", "--actor"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Append a model-call telemetry event emitted by an agent harness.

    Python does not call the model SDK. This command gives skills a
    deterministic bridge for token accounting after each extractor or
    writer Task returns.
    """
    if tokens_in < 0 or tokens_out < 0:
        cli_error(
            EXIT_VALIDATION,
            error="bad_tokens",
            message="--tokens-in and --tokens-out must be >= 0",
        )
    if wall_seconds < 0:
        cli_error(
            EXIT_VALIDATION,
            error="bad_wall_seconds",
            message="--wall-seconds must be >= 0",
        )
    bundle = _resolve_bundle(run)
    state = load_state(bundle)
    try:
        from ..bundle.run.cost import haiku_eq_for
        cost_haiku_eq = haiku_eq_for(tier, tokens_in, tokens_out)
    except ValueError as exc:
        cli_error(EXIT_VALIDATION, error="bad_tier", message=str(exc))

    payload = {
        "role": role,
        "model_id": model_id,
        "tier": tier,
        "stage": stage,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "haiku_eq": cost_haiku_eq,
        "cost_haiku_eq": cost_haiku_eq,
        "cost_usd": 0.0,
        "wall_seconds": wall_seconds,
    }
    append_event(
        bundle,
        Event(
            run_id=state.run_id,
            type="call",
            actor=actor,
            concept_id=concept_id,
            page_id=page_id,
            stage=stage,
            data=payload,
        ),
    )
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, **payload}))
    else:
        typer.echo(
            f"recorded call role={role} model={model_id} tier={tier} "
            f"tokens={tokens_in}+{tokens_out} haiku_eq={cost_haiku_eq:.1f}"
        )


_ROUND_REQUIRED_TYPES = frozenset(
    {"round_started", "round_completed", "pattern_dispatched"}
)


def _read_event_payload(
    data_flag: str | None, *, from_stdin: bool
) -> tuple[dict, bool]:
    """Return ``(payload_dict, stdin_was_ignored)``.

    Resolution order:
    1. If ``--from-stdin`` is set, read stdin and parse it as the payload.
       If ``--data`` is also supplied, ``--data`` wins and stdin is ignored
       (caller should warn).
    2. If ``data_flag`` is set (and ``--from-stdin`` is not, or was ignored),
       parse and return it.
    3. Else fall back to an empty object.

    ``stdin_was_ignored`` is True only when both ``--from-stdin`` and
    ``--data`` are supplied, so the caller can warn that piped input was
    discarded.
    """
    stdin_content: str = ""
    if from_stdin:
        stdin_content = sys.stdin.read().strip()

    if data_flag is not None:
        stdin_was_ignored = bool(stdin_content)
        try:
            payload = json.loads(data_flag)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--data is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("--data must be a JSON object, not an array or scalar")
        return payload, stdin_was_ignored

    if stdin_content:
        try:
            payload = json.loads(stdin_content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"stdin is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("stdin payload must be a JSON object, not an array or scalar")
        return payload, False

    return {}, False


def _validate_event_payload(type_: str, payload: dict) -> None:
    """Raise ``ValueError`` when a required field is absent or wrong type.

    Events in ``_ROUND_REQUIRED_TYPES`` must have ``round`` as a
    non-negative integer. Booleans (bool is a subclass of int) and negative
    values are rejected.
    """
    if type_ not in _ROUND_REQUIRED_TYPES:
        return
    if "round" not in payload:
        raise ValueError(
            f"event type {type_!r} requires a 'round' int field in the payload"
        )
    val = payload["round"]
    if isinstance(val, bool) or not isinstance(val, int):
        raise ValueError(
            f"event type {type_!r}: 'round' must be an int, got {type(val).__name__}"
        )
    if val < 0:
        raise ValueError(
            f"event type {type_!r}: 'round' must be a non-negative int, got {val}"
        )


@app.command("record-event")
def cmd_record_event(
    type_: str = typer.Option(..., "--type", help="Event type literal."),
    run: Path | None = typer.Option(None, "--run"),
    stage: str | None = typer.Option(None, "--stage"),
    concept_id: str | None = typer.Option(None, "--concept-id"),
    page_id: str | None = typer.Option(None, "--page-id"),
    chunk_id: str | None = typer.Option(None, "--chunk-id"),
    doc_id: str | None = typer.Option(None, "--doc-id"),
    actor: str = typer.Option("agent", "--actor"),
    data: str | None = typer.Option(None, "--data", help="JSON object payload."),
    from_stdin: bool = typer.Option(False, "--from-stdin", help="Read payload from stdin."),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Append a non-call event (round_started, round_completed, etc.).

    Use this for the investigate workflow's round + pattern lifecycle
    events. ``call`` events stay on ``record-call`` / ``record-calls``
    where the cost machinery enforces token validation.

    The ``--type`` value is validated against ``EventType``; unknown
    types are rejected.

    Payload resolution order: ``--data`` > ``--from-stdin`` > empty object.
    When both ``--data`` and ``--from-stdin`` are supplied, ``--data`` wins
    and a warning is printed to stderr.

    Events that carry a round counter (``round_started``,
    ``round_completed``, ``pattern_dispatched``) must include ``round``
    as a non-negative integer field or the command exits non-zero.
    """
    from typing import get_args

    from ..bundle.run.events import EventType
    allowed = set(get_args(EventType))
    if type_ == "call":
        cli_error(
            EXIT_VALIDATION,
            error="use_record_call",
            message="use 'wikify run record-call' for type=call.",
        )
    if type_ not in allowed:
        cli_error(
            EXIT_VALIDATION,
            error="bad_event_type",
            message=f"unknown event type: {type_!r}",
            allowed=sorted(allowed),
        )
    try:
        payload, stdin_ignored = _read_event_payload(data, from_stdin=from_stdin)
    except ValueError as exc:
        cli_error(EXIT_VALIDATION, error="bad_data", message=str(exc))
    if stdin_ignored:
        typer.echo(
            "WARNING: --data was supplied; piped stdin was ignored",
            err=True,
        )
    try:
        _validate_event_payload(type_, payload)
    except ValueError as exc:
        cli_error(EXIT_VALIDATION, error="bad_payload", message=str(exc))
    bundle = _resolve_bundle(run)
    state = load_state(bundle)
    event = Event(
        run_id=state.run_id,
        type=type_,
        actor=actor,
        stage=stage,
        concept_id=concept_id,
        page_id=page_id,
        chunk_id=chunk_id,
        doc_id=doc_id,
        data=payload,
    )
    append_event(bundle, event)
    if fmt == "json":
        typer.echo(
            json.dumps({"ok": True, "event_id": event.event_id, "type": type_})
        )
        return
    typer.echo(f"recorded {type_} event_id={event.event_id}")


_REQUIRED_BATCH_FIELDS: tuple[tuple[str, type], ...] = (
    ("role", str),
    ("model_id", str),
    ("tier", str),
    ("tokens_in", int),
    ("tokens_out", int),
    ("stage", str),
)


def _validate_batch_line(obj: object) -> tuple[dict | None, str | None]:
    """Return ``(payload, None)`` or ``(None, error_message)`` for one line.

    ``payload`` is a normalised dict ready to hand to ``Event``. Errors
    are short human-readable strings ("missing role", "tokens_in must
    be int", etc.); the caller prefixes the line number.
    """
    if not isinstance(obj, dict):
        return None, "line is not a JSON object"
    for name, typ in _REQUIRED_BATCH_FIELDS:
        if name not in obj:
            return None, f"missing {name}"
        val = obj[name]
        # bool is a subclass of int; reject explicitly for the int fields.
        if typ is int and (not isinstance(val, int) or isinstance(val, bool)):
            return None, f"{name} must be int"
        if typ is str and not isinstance(val, str):
            return None, f"{name} must be str"
    if obj["tokens_in"] < 0 or obj["tokens_out"] < 0:
        return None, "tokens_in and tokens_out must be >= 0"
    return obj, None


@app.command("record-calls")
def cmd_record_calls(
    run: Path = typer.Option(..., "--run"),
    from_stdin: bool = typer.Option(False, "--from-stdin"),
    fail_fast: bool = typer.Option(False, "--fail-fast"),
    fmt: str = typer.Option("json", "--format", help="json | compact"),
) -> None:
    """Batched ingest of call telemetry: one JSON object per stdin line.

    Each line must carry ``role``, ``model_id``, ``tier``, ``tokens_in``,
    ``tokens_out``, ``stage``. Optional keys: ``concept_id``, ``page_id``,
    ``chunk_id``, ``doc_id``, ``at``, ``wall_seconds``, ``actor``. Valid
    lines append a ``call`` event preserving input order; malformed lines
    are skipped (or abort the batch under ``--fail-fast``).
    """
    if not from_stdin:
        cli_error(
            EXIT_VALIDATION,
            error="missing_input_mode",
            message="--from-stdin is required",
        )
    bundle = _resolve_bundle(run)
    state = load_state(bundle)
    from ..bundle.run.cost import haiku_eq_for

    appended = 0
    errors: list[str] = []
    for lineno, raw in enumerate(sys.stdin, start=1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            err = f"line {lineno}: invalid json ({exc.msg})"
            errors.append(err)
            if fail_fast:
                break
            continue
        payload, reason = _validate_batch_line(obj)
        if payload is None:
            errors.append(f"line {lineno}: {reason}")
            if fail_fast:
                break
            continue
        try:
            cost_haiku_eq = haiku_eq_for(
                payload["tier"], payload["tokens_in"], payload["tokens_out"]
            )
        except ValueError as exc:
            errors.append(f"line {lineno}: {exc}")
            if fail_fast:
                break
            continue
        wall_seconds = payload.get("wall_seconds", 0.0)
        actor = payload.get("actor", "agent")
        data = {
            "role": payload["role"],
            "model_id": payload["model_id"],
            "tier": payload["tier"],
            "stage": payload["stage"],
            "input_tokens": payload["tokens_in"],
            "output_tokens": payload["tokens_out"],
            "haiku_eq": cost_haiku_eq,
            "cost_haiku_eq": cost_haiku_eq,
            "cost_usd": 0.0,
            "wall_seconds": wall_seconds,
        }
        event_kwargs: dict = {
            "run_id": state.run_id,
            "type": "call",
            "actor": actor,
            "stage": payload["stage"],
            "data": data,
        }
        for opt in ("concept_id", "page_id", "chunk_id", "doc_id"):
            if opt in payload and payload[opt] is not None:
                event_kwargs[opt] = payload[opt]
        if "at" in payload and payload["at"] is not None:
            event_kwargs["at"] = payload["at"]
        try:
            append_event(bundle, Event(**event_kwargs))
        except Exception as exc:
            errors.append(f"line {lineno}: append failed ({exc})")
            if fail_fast:
                break
            continue
        appended += 1

    summary = {
        "ok": True,
        "run": str(bundle.root),
        "appended": appended,
        "rejected": len(errors),
        "errors": errors,
    }
    if fmt == "compact":
        typer.echo(
            f"appended={appended} rejected={len(errors)} run={bundle.root}"
        )
    else:
        typer.echo(json.dumps(summary))


@app.command("set")
def cmd_set(
    run: Path | None = typer.Option(None, "--run"),
    target_haiku_eq: int | None = typer.Option(None, "--target-haiku-eq"),
    strategy_note: str | None = typer.Option(None, "--strategy-note"),
    corpus_fingerprint: str | None = typer.Option(None, "--corpus-fingerprint"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Update small mutable fields. ``--corpus`` is forbidden — open a new bundle.

    ``--corpus-fingerprint`` re-stamps corpus identity after a re-entry has
    absorbed new documents (the value is the live ``health.fingerprint``),
    so drift detection does not re-fire on the next round.
    """
    bundle = _resolve_bundle(run)
    state = load_state(bundle)
    updates: dict = {}
    if target_haiku_eq is not None:
        budget = state.budget.model_copy(update={"target_haiku_eq": target_haiku_eq})
        updates["budget"] = budget
    if corpus_fingerprint is not None:
        updates["corpus_fingerprint"] = corpus_fingerprint
    if strategy_note is not None:
        # Append note as a stage_changed event; state.json itself stays slim.
        append_event(
            bundle,
            Event(
                run_id=state.run_id,
                type="stage_changed",
                actor="cli",
                stage="set",
                data={"strategy_note": strategy_note},
            ),
        )
    if updates:
        new_state = touch(state.model_copy(update=updates))
        save_state(bundle, new_state)
    if fmt == "json":
        typer.echo(json.dumps({"ok": True}))
    else:
        typer.echo("ok")


__all__ = ["app"]
