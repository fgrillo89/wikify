"""FastAPI dashboard for monitoring Wikify epoch convergence.

Serves a single-page application backed by SQLite — no LLM calls.
Run with:  uvicorn wikify.wiki.dashboard:app --reload --port 8765
"""

from __future__ import annotations

import logging
from collections import defaultdict

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sqlmodel import func, select

from wikify.store.db import get_session
from wikify.store.models import ConceptRecord, EpochLog, SourceCoverage

logger = logging.getLogger(__name__)

app = FastAPI(title="Wikify Wiki Dashboard")

# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@app.get("/api/epochs")
def api_epochs() -> list[dict]:
    """Return all EpochLog rows ordered by epoch number."""
    with get_session() as session:
        rows = session.exec(select(EpochLog).order_by(EpochLog.epoch)).all()
        return [
            {
                "epoch": r.epoch,
                "triggered_by": r.triggered_by,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "concepts_discovered": r.concepts_discovered,
                "stubs_upgraded": r.stubs_upgraded,
                "articles_written": r.articles_written,
                "contradictions_flagged": r.contradictions_flagged,
                "cross_refs_added": r.cross_refs_added,
                "converged": r.converged,
                "loss_score": r.loss_score,
                "loss_delta": r.loss_delta,
            }
            for r in rows
        ]


@app.get("/api/concepts")
def api_concepts() -> list[dict]:
    """Return all ConceptRecord rows ordered by importance descending."""
    with get_session() as session:
        rows = session.exec(
            select(ConceptRecord).order_by(ConceptRecord.importance.desc())  # type: ignore[attr-defined]
        ).all()
        return [
            {
                "id": r.id,
                "name": r.name,
                "concept_type": r.concept_type,
                "domain": r.domain,
                "importance": r.importance,
                "article_status": r.article_status,
                "epoch_discovered": r.epoch_discovered,
                "epoch_last_updated": r.epoch_last_updated,
            }
            for r in rows
        ]


@app.get("/api/coverage")
def api_coverage() -> list[dict]:
    """Return SourceCoverage aggregated as source -> domain -> count matrix."""
    with get_session() as session:
        rows = session.exec(select(SourceCoverage)).all()

    # Aggregate: {source_id: {domain: count}}
    matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        domain = row.domain or "unspecified"
        matrix[row.source_id][domain] += 1

    return [
        {"source_id": source_id, "domains": dict(domain_counts)}
        for source_id, domain_counts in sorted(matrix.items())
    ]


@app.get("/api/gradient")
def api_gradient() -> list[dict]:
    """Return top-20 concepts by information gradient proxy.

    Gradient = coverage_count / max_coverage_count across all concepts.
    """
    with get_session() as session:
        # Count SourceCoverage rows per article_slug
        coverage_counts = session.exec(
            select(SourceCoverage.article_slug, func.count(SourceCoverage.id).label("cnt"))
            .group_by(SourceCoverage.article_slug)
            .order_by(func.count(SourceCoverage.id).desc())
            .limit(20)
        ).all()

        if not coverage_counts:
            return []

        max_count = coverage_counts[0][1] if coverage_counts[0][1] > 0 else 1

        result = []
        for slug, cnt in coverage_counts:
            concept = session.get(ConceptRecord, slug)
            name = concept.name if concept else slug
            result.append(
                {
                    "concept_id": slug,
                    "name": name,
                    "gradient": round(cnt / max_count, 4),
                    "coverage_count": cnt,
                }
            )
        return result


# ---------------------------------------------------------------------------
# Single-page application
# ---------------------------------------------------------------------------

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Wikify - Wiki Dashboard</title>
  <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
  <script src="https://d3js.org/d3.v7.min.js"></script>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --bg:       #0d1117;
      --surface:  #161b22;
      --border:   #30363d;
      --text:     #c9d1d9;
      --muted:    #8b949e;
      --accent:   #58a6ff;
      --green:    #3fb950;
      --yellow:   #d29922;
      --red:      #f85149;
      --orange:   #e3723a;
      --font:     "JetBrains Mono", "Fira Mono", "Consolas", monospace;
    }

    body {
      background: var(--bg);
      color: var(--text);
      font-family: var(--font);
      font-size: 13px;
      line-height: 1.6;
      padding: 24px;
    }

    h1 {
      font-size: 1.4rem;
      color: var(--accent);
      margin-bottom: 4px;
      letter-spacing: 0.04em;
    }

    .subtitle {
      color: var(--muted);
      font-size: 0.8rem;
      margin-bottom: 28px;
    }

    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
      margin-bottom: 20px;
    }

    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 16px;
    }

    .card h2 {
      font-size: 0.85rem;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 12px;
    }

    .card-full {
      grid-column: 1 / -1;
    }

    .plot-container {
      width: 100%;
      min-height: 260px;
    }

    /* Epoch log table */
    .table-wrap {
      overflow-x: auto;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.78rem;
    }

    thead th {
      text-align: left;
      color: var(--muted);
      font-weight: normal;
      padding: 6px 10px;
      border-bottom: 1px solid var(--border);
      white-space: nowrap;
    }

    tbody tr:hover { background: rgba(88,166,255,0.04); }

    tbody td {
      padding: 6px 10px;
      border-bottom: 1px solid rgba(48,54,61,0.5);
      white-space: nowrap;
    }

    .badge {
      display: inline-block;
      padding: 1px 6px;
      border-radius: 3px;
      font-size: 0.72rem;
    }

    .badge-none    { background: #21262d; color: var(--muted); }
    .badge-stub    { background: #2d2a1e; color: var(--yellow); }
    .badge-draft   { background: #1e2d3a; color: var(--accent); }
    .badge-full    { background: #1e2d23; color: var(--green); }
    .badge-true    { background: #1e2d23; color: var(--green); }
    .badge-false   { background: #21262d; color: var(--muted); }

    .empty-msg {
      color: var(--muted);
      text-align: center;
      padding: 40px 0;
      font-size: 0.85rem;
    }

    /* D3 graph */
    #graph-svg {
      width: 100%;
      height: 380px;
      display: block;
    }

    .node circle { stroke-width: 1.5px; cursor: pointer; }
    .node text {
      font-size: 10px;
      fill: var(--text);
      pointer-events: none;
    }

    .link { stroke: var(--border); stroke-opacity: 0.6; }

    .legend {
      display: flex;
      gap: 16px;
      flex-wrap: wrap;
      margin-top: 10px;
    }

    .legend-item {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 0.75rem;
      color: var(--muted);
    }

    .legend-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      flex-shrink: 0;
    }

    #refresh-btn {
      background: none;
      border: 1px solid var(--border);
      color: var(--accent);
      font-family: var(--font);
      font-size: 0.78rem;
      padding: 4px 12px;
      border-radius: 4px;
      cursor: pointer;
      float: right;
      margin-top: -4px;
    }

    #refresh-btn:hover { background: rgba(88,166,255,0.1); }

    #last-updated {
      color: var(--muted);
      font-size: 0.72rem;
      float: right;
      margin-top: 2px;
      clear: right;
    }
  </style>
</head>
<body>
  <h1>Wikify &mdash; Wiki Dashboard</h1>
  <p class="subtitle">Epoch convergence monitor &middot; read-only &middot; SQLite</p>

  <button id="refresh-btn" onclick="loadAll()">Refresh</button>
  <div id="last-updated"></div>

  <div class="grid">

    <!-- Convergence curve -->
    <div class="card">
      <h2>Convergence Curve</h2>
      <div id="convergence-chart" class="plot-container"></div>
    </div>

    <!-- Concept status -->
    <div class="card">
      <h2>Concept Status Distribution</h2>
      <div id="status-chart" class="plot-container"></div>
    </div>

    <!-- Epoch log -->
    <div class="card card-full">
      <h2>Epoch Log</h2>
      <div class="table-wrap">
        <table id="epoch-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Triggered by</th>
              <th>Started</th>
              <th>Duration</th>
              <th>Concepts</th>
              <th>Stubs upgraded</th>
              <th>Articles</th>
              <th>Contradictions</th>
              <th>Cross-refs</th>
              <th>Loss</th>
              <th>Delta</th>
              <th>Converged</th>
            </tr>
          </thead>
          <tbody id="epoch-tbody">
            <tr><td colspan="12" class="empty-msg">Loading...</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Concept graph -->
    <div class="card card-full">
      <h2>Concept Graph
        <span style="color:var(--muted);font-weight:normal;font-size:0.75rem">
          &mdash; node size = importance &middot; color = article status
        </span></h2>
      <svg id="graph-svg"></svg>
      <div class="legend">
        <div class="legend-item"><div class="legend-dot" style="background:#4a4f56"></div>none</div>
        <div class="legend-item"><div class="legend-dot" style="background:#d29922"></div>stub</div>
        <div class="legend-item">
          <div class="legend-dot" style="background:#58a6ff"></div>draft</div>
        <div class="legend-item"><div class="legend-dot" style="background:#3fb950"></div>full</div>
      </div>
    </div>

  </div>

  <script>
  // ---------------------------------------------------------------------------
  // Plotly theme helpers
  // ---------------------------------------------------------------------------
  const DARK = {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor:  "rgba(0,0,0,0)",
    font:          { color: "#c9d1d9", family: "'JetBrains Mono', monospace", size: 11 },
    xaxis:         { gridcolor: "#21262d", linecolor: "#30363d", zerolinecolor: "#30363d" },
    yaxis:         { gridcolor: "#21262d", linecolor: "#30363d", zerolinecolor: "#30363d" },
    margin:        { t: 20, r: 20, b: 40, l: 50 },
  };

  const STATUS_COLOR = {
    none:  "#4a4f56",
    stub:  "#d29922",
    draft: "#58a6ff",
    full:  "#3fb950",
  };

  // ---------------------------------------------------------------------------
  // Fetch helpers
  // ---------------------------------------------------------------------------
  async function fetchJSON(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${url} -> ${res.status}`);
    return res.json();
  }

  // ---------------------------------------------------------------------------
  // Convergence curve
  // ---------------------------------------------------------------------------
  async function renderConvergence(epochs) {
    const el = document.getElementById("convergence-chart");
    if (!epochs.length) {
      el.innerHTML = '<p class="empty-msg">No epoch data yet.</p>';
      return;
    }
    const xs = epochs.map(e => e.epoch);
    const ys = epochs.map(e => e.loss_score);
    const deltas = epochs.map(e => e.loss_delta);

    const traces = [
      {
        x: xs, y: ys,
        mode: "lines+markers",
        name: "Loss L",
        line: { color: "#58a6ff", width: 2 },
        marker: { size: 6, color: ys.map(v => v < 0.3 ? "#3fb950" : "#58a6ff") },
        hovertemplate: "Epoch %{x}<br>L = %{y:.4f}<extra></extra>",
      },
      {
        x: xs, y: deltas,
        mode: "lines+markers",
        name: "Loss delta",
        line: { color: "#d29922", width: 1.5, dash: "dot" },
        marker: { size: 4 },
        hovertemplate: "Epoch %{x}<br>dL = %{y:.4f}<extra></extra>",
      },
    ];

    // Convergence threshold line at L = 0.3
    const layout = {
      ...DARK,
      shapes: [{
        type: "line", xref: "paper", x0: 0, x1: 1,
        yref: "y", y0: 0.3, y1: 0.3,
        line: { color: "#3fb950", width: 1, dash: "dash" },
      }],
      annotations: [{
        xref: "paper", x: 0.01, yref: "y", y: 0.3,
        text: "L=0.3 (haiku→sonnet)", showarrow: false,
        font: { color: "#3fb950", size: 9 },
        xanchor: "left", yanchor: "bottom",
      }],
      legend: { x: 0.7, y: 0.95, bgcolor: "rgba(0,0,0,0)", font: { size: 10 } },
    };

    Plotly.newPlot(el, traces, layout, { displayModeBar: false, responsive: true });
  }

  // ---------------------------------------------------------------------------
  // Concept status bar chart
  // ---------------------------------------------------------------------------
  async function renderStatusChart(concepts) {
    const el = document.getElementById("status-chart");
    if (!concepts.length) {
      el.innerHTML = '<p class="empty-msg">No concepts yet.</p>';
      return;
    }

    const counts = { none: 0, stub: 0, draft: 0, full: 0 };
    for (const c of concepts) {
      const s = c.article_status in counts ? c.article_status : "none";
      counts[s]++;
    }

    const labels = Object.keys(counts);
    const values = Object.values(counts);
    const colors = labels.map(l => STATUS_COLOR[l]);

    const trace = {
      x: labels, y: values,
      type: "bar",
      marker: { color: colors },
      hovertemplate: "%{x}: %{y}<extra></extra>",
    };

    const layout = {
      ...DARK,
      showlegend: false,
      bargap: 0.3,
    };

    Plotly.newPlot(el, [trace], layout, { displayModeBar: false, responsive: true });
  }

  // ---------------------------------------------------------------------------
  // Epoch log table
  // ---------------------------------------------------------------------------
  function renderEpochTable(epochs) {
    const tbody = document.getElementById("epoch-tbody");
    if (!epochs.length) {
      tbody.innerHTML = '<tr><td colspan="12" class="empty-msg">No epochs recorded yet.</td></tr>';
      return;
    }

    function fmt(iso) {
      if (!iso) return "&mdash;";
      const d = new Date(iso);
      return d.toLocaleString(undefined, {
        month: "short", day: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit",
      });
    }

    function duration(start, end) {
      if (!start || !end) return "&mdash;";
      const s = (new Date(end) - new Date(start)) / 1000;
      if (s < 60) return s.toFixed(1) + "s";
      return (s / 60).toFixed(1) + "m";
    }

    tbody.innerHTML = [...epochs].reverse().map(e => `
      <tr>
        <td>${e.epoch}</td>
        <td>${e.triggered_by || "&mdash;"}</td>
        <td>${fmt(e.started_at)}</td>
        <td>${duration(e.started_at, e.completed_at)}</td>
        <td>${e.concepts_discovered}</td>
        <td>${e.stubs_upgraded}</td>
        <td>${e.articles_written}</td>
        <td>${e.contradictions_flagged}</td>
        <td>${e.cross_refs_added}</td>
        <td>${e.loss_score.toFixed(4)}</td>
        <td>${e.loss_delta.toFixed(4)}</td>
        <td><span class="badge badge-${e.converged}">${e.converged}</span></td>
      </tr>
    `).join("");
  }

  // ---------------------------------------------------------------------------
  // D3 force-directed concept graph
  // ---------------------------------------------------------------------------
  function renderConceptGraph(concepts) {
    const svgEl = document.getElementById("graph-svg");
    // Clear previous render
    d3.select(svgEl).selectAll("*").remove();

    if (!concepts.length) {
      d3.select(svgEl)
        .append("text")
        .attr("x", "50%").attr("y", "50%")
        .attr("text-anchor", "middle")
        .attr("fill", "#8b949e")
        .attr("font-family", "monospace")
        .attr("font-size", 12)
        .text("No concepts to display.");
      return;
    }

    const W = svgEl.clientWidth  || 800;
    const H = svgEl.clientHeight || 380;

    const svg = d3.select(svgEl)
      .attr("viewBox", `0 0 ${W} ${H}`);

    // Build nodes — limit to top 80 by importance to avoid clutter
    const nodes = concepts
      .slice(0, 80)
      .map(c => ({
        id: c.id,
        name: c.name,
        importance: c.importance,
        status: c.article_status,
        domain: c.domain,
      }));

    // Build edges: connect nodes sharing the same domain
    const byDomain = {};
    for (const n of nodes) {
      if (n.domain) {
        (byDomain[n.domain] = byDomain[n.domain] || []).push(n.id);
      }
    }
    const links = [];
    const seen = new Set();
    for (const ids of Object.values(byDomain)) {
      for (let i = 0; i < ids.length; i++) {
        for (let j = i + 1; j < ids.length; j++) {
          const key = ids[i] < ids[j] ? `${ids[i]}|${ids[j]}` : `${ids[j]}|${ids[i]}`;
          if (!seen.has(key)) {
            seen.add(key);
            links.push({ source: ids[i], target: ids[j] });
          }
        }
      }
    }

    const simulation = d3.forceSimulation(nodes)
      .force("link",   d3.forceLink(links).id(d => d.id).distance(60))
      .force("charge", d3.forceManyBody().strength(-80))
      .force("center", d3.forceCenter(W / 2, H / 2))
      .force("collide", d3.forceCollide(d => nodeRadius(d) + 4));

    const link = svg.append("g")
      .selectAll("line")
      .data(links)
      .join("line")
      .attr("class", "link");

    const node = svg.append("g")
      .selectAll("g")
      .data(nodes)
      .join("g")
      .attr("class", "node")
      .call(
        d3.drag()
          .on("start", (event, d) => {
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x; d.fy = d.y;
          })
          .on("drag", (event, d) => { d.fx = event.x; d.fy = event.y; })
          .on("end",  (event, d) => {
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null; d.fy = null;
          })
      );

    node.append("circle")
      .attr("r", nodeRadius)
      .attr("fill", d => STATUS_COLOR[d.status] || "#4a4f56")
      .attr("stroke", d => d3.color(STATUS_COLOR[d.status] || "#4a4f56").brighter(0.8));

    node.append("text")
      .attr("dy", d => nodeRadius(d) + 10)
      .attr("text-anchor", "middle")
      .text(d => d.name.length > 18 ? d.name.slice(0, 16) + "..." : d.name);

    node.append("title").text(d =>
      `${d.name}\nDomain: ${d.domain || "unknown"}\nStatus: ${d.status}\n`
      + `Importance: ${d.importance.toFixed(3)}`
    );

    simulation.on("tick", () => {
      link
        .attr("x1", d => d.source.x).attr("y1", d => d.source.y)
        .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
      node.attr("transform", d => `translate(${d.x},${d.y})`);
    });
  }

  function nodeRadius(d) {
    // 4px minimum, scale with importance up to 16px
    return 4 + d.importance * 12;
  }

  // ---------------------------------------------------------------------------
  // Main loader
  // ---------------------------------------------------------------------------
  async function loadAll() {
    try {
      const [epochs, concepts] = await Promise.all([
        fetchJSON("/api/epochs"),
        fetchJSON("/api/concepts"),
      ]);
      renderConvergence(epochs);
      renderStatusChart(concepts);
      renderEpochTable(epochs);
      renderConceptGraph(concepts);
      document.getElementById("last-updated").textContent =
        "Updated " + new Date().toLocaleTimeString();
    } catch (err) {
      console.error("Dashboard load error:", err);
      document.getElementById("last-updated").textContent =
        "Error: " + err.message;
    }
  }

  // Initial load
  loadAll();
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """Serve the single-page dashboard application."""
    return _HTML
