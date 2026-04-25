"""Functional sweep: assess bib / graph / fluent-API quality on ald_all_marker."""

from __future__ import annotations

import io
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

CORPUS = Path("data/corpora/ald_all_marker")


def h(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def sub(title: str) -> None:
    print(f"\n--- {title} ---")


def sample_bib(path: Path, n: int = 5) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    entries: list[dict[str, str]] = []
    cur: dict[str, str] | None = None
    buf = ""
    for line in text.splitlines():
        if line.startswith("@"):
            if cur:
                cur["_raw"] = buf
                entries.append(cur)
            m = re.match(r"@(\w+)\{([^,]+),", line)
            cur = {"kind": m.group(1) if m else "?", "key": m.group(2) if m else "?"}
            buf = line + "\n"
        elif cur is not None:
            buf += line + "\n"
            m = re.match(r"\s*(\w+)\s*=\s*\{(.*?)\},?\s*$", line)
            if m:
                cur[m.group(1)] = m.group(2)
    if cur:
        cur["_raw"] = buf
        entries.append(cur)
    return entries


def audit_bib(name: str, entries: list[dict[str, str]]) -> None:
    sub(f"{name}: {len(entries)} entries")
    rng = random.Random(42)
    sample = rng.sample(entries, min(5, len(entries)))
    for e in sample:
        print(f"\n  @{e['kind']}{{{e['key']}")
        for f in ("author", "title", "year", "journal", "doi"):
            if f in e:
                print(f"    {f:8s} = {e[f][:100]}")
    # hygiene checks
    missing_author = sum(1 for e in entries if not e.get("author"))
    missing_title = sum(1 for e in entries if not e.get("title"))
    missing_year = sum(1 for e in entries if not e.get("year"))
    missing_doi = sum(1 for e in entries if not e.get("doi"))
    weird_author = sum(
        1 for e in entries
        if e.get("author") and re.search(r"[\u2020\u2021\u2022\u00a7\uf020-\uf8ff]", e["author"])
    )
    all_caps_author = sum(
        1 for e in entries
        if e.get("author") and e["author"].isupper() and len(e["author"]) > 3
    )
    print(f"\n  hygiene:")
    print(f"    missing author: {missing_author}/{len(entries)}")
    print(f"    missing title:  {missing_title}/{len(entries)}")
    print(f"    missing year:   {missing_year}/{len(entries)}")
    print(f"    missing DOI:    {missing_doi}/{len(entries)}")
    print(f"    author has affiliation glyph: {weird_author}/{len(entries)}")
    print(f"    author is ALL-CAPS: {all_caps_author}/{len(entries)}")


def audit_graph(kg_path: Path) -> None:
    kg = json.loads(kg_path.read_text())
    nodes = kg["nodes"]
    edges = kg["edges"]
    node_by_id = {n["id"]: n for n in nodes}

    types = Counter(n.get("type", "?") for n in nodes)
    kinds = Counter(n.get("kind", "?") for n in nodes if n.get("type") == "source")
    ekinds = Counter(e.get("kind", "?") for e in edges)

    sub(f"nodes: {len(nodes):,}  edges: {len(edges):,}")
    print(f"  node types: {dict(types.most_common())}")
    print(f"  source kinds: {dict(kinds.most_common())}")
    print(f"  edge kinds: {dict(ekinds.most_common())}")

    # Dangling edges
    dangling = 0
    for e in edges:
        if e["source"] not in node_by_id or e["target"] not in node_by_id:
            dangling += 1
    print(f"  dangling edges (endpoint missing): {dangling}/{len(edges)}")

    # Isolated nodes
    touched = set()
    for e in edges:
        touched.add(e["source"]); touched.add(e["target"])
    isolated = [n for n in nodes if n["id"] not in touched]
    iso_types = Counter(n.get("type", "?") for n in isolated)
    print(f"  isolated nodes: {len(isolated)}  by type: {dict(iso_types)}")

    # CITES integrity
    cites = [e for e in edges if e.get("kind") == "CITES"]
    cites_to_corpus = sum(
        1 for e in cites
        if node_by_id.get(e["target"], {}).get("kind") == "corpus"
    )
    cites_to_cited = sum(
        1 for e in cites
        if node_by_id.get(e["target"], {}).get("kind") == "cited"
    )
    print(f"  CITES: {len(cites)} total  ->corpus: {cites_to_corpus}  ->cited: {cites_to_cited}")

    # PageRank distribution
    corpus = [n for n in nodes if n.get("kind") == "corpus"]
    prs = sorted([n.get("pagerank", 0) for n in corpus], reverse=True)
    if prs:
        print(
            f"  pagerank (corpus): max={prs[0]:.4f}  "
            f"median={prs[len(prs)//2]:.4f}  min={prs[-1]:.4f}  "
            f"zeros={sum(1 for v in prs if v == 0)}"
        )

    # Top papers
    sub("top-10 corpus papers by PageRank")
    top = sorted(corpus, key=lambda n: -(n.get("pagerank") or 0))[:10]
    for n in top:
        print(
            f"  PR={n.get('pagerank', 0):.4f}  cites={n.get('citation_count', 0):3d}  "
            f"{(n.get('title') or n['id'])[:68]}"
        )

    # Author graph
    auths = [n for n in nodes if n.get("type") == "author"]
    h_idx = sorted([(n.get("h_index", 0), n["id"]) for n in auths], reverse=True)[:10]
    sub("top-10 authors by h-index")
    for hi, aid in h_idx:
        print(f"  h={hi:3d}  {aid[:60]}")

    # Chunk -> source coverage
    chunks = [n for n in nodes if n.get("type") == "chunk"]
    chunk_to_src = {}
    for e in edges:
        if e.get("kind") == "CONTAINS_CHUNK":
            chunk_to_src[e["target"]] = e["source"]
    orphan_chunks = [c for c in chunks if c["id"] not in chunk_to_src]
    print(f"\n  chunks without a CONTAINS_CHUNK edge: {len(orphan_chunks)}/{len(chunks)}")


def run_fluent(corpus_root: Path) -> None:
    from wikify.corpus.graph_build import load_knowledge_graph
    from wikify.distill.kg_tools import get_citations, get_source_info, search_chunks
    from wikify.embedding import embedder_for
    from wikify.paths import CorpusPaths
    from wikify.corpus.chunks import all_chunks
    from wikify.corpus.vectors import load_vectors
    from wikify.corpus.vectors_meta import read_meta

    paths = CorpusPaths(root=corpus_root)
    vs = load_vectors(paths.vectors_path)
    meta = read_meta(paths.vectors_path)
    # Use query mode for user-search fidelity.
    embed = embedder_for(meta.backend, meta.model, mode="query")
    kg = load_knowledge_graph(paths.knowledge_graph_path, vectors=vs, embed_fn=embed)
    chunk_text = {c.id: c.text for c in all_chunks(paths)}

    n_src = kg.sources().count()
    n_auth = kg.authors().count()
    n_ck = kg.chunks().count()
    print(f"KG loaded: {n_src} sources, {n_auth} authors, {n_ck} chunks")
    print(f"Embedder: {meta.backend} / {meta.model} dim={meta.dim}")

    queries = [
        "thermal atomic layer deposition growth per cycle",
        "plasma-enhanced ALD of aluminum oxide",
        "TMA water precursor self-limiting surface reaction",
        "HfO2 high-k gate dielectric thin film",
        "memristor resistive switching filament formation",
        "area-selective ALD inhibitor molecule",
        "oxygen vacancy neuromorphic synapse",
    ]

    sub("search_chunks on realistic ALD queries")
    for q in queries:
        hits = search_chunks(kg, query=q, top_k=3)
        print(f"\nQ: {q}")
        for r in hits:
            t = chunk_text.get(r["id"], "")
            preview = re.sub(r"\s+", " ", t)[:180]
            src = r.get("source_id") or "?"
            print(f"  [{r['score']:.3f}] {src[:50]} :: {preview}")

    # Compose: author -> papers -> top chunks
    sub("compose: top-h author -> papers -> top chunk on 'ALD precursor'")
    auth = kg.authors().top(1, by="h_index").first()
    if auth:
        aid = auth["id"]
        papers = kg.author(aid).sources().collect()
        print(f"author: {aid}  ({len(papers)} papers)")
        for p in papers[:3]:
            print(f"  [{p.get('year')}] {(p.get('title') or p['id'])[:68]}")
            hits = kg.source(p["id"]).chunks().search("ALD precursor", top_k=1)
            for h2 in hits:
                t = chunk_text.get(h2["id"], "")[:160].replace("\n", " ")
                print(f"    [{h2['score']:.3f}] {t}")

    # Compose: top-PR paper -> references
    sub("compose: top-PR paper -> outgoing references")
    top_src = kg.sources(kind="corpus").top(1, by="pagerank").first()
    if top_src:
        sid = top_src["id"]
        print(f"source: {top_src.get('title', sid)[:68]}")
        refs = get_citations(kg, source_id=sid, direction="references")
        print(f"  references: {len(refs)}")
        for r in refs[:5]:
            print(f"    -> {(r.get('title') or r.get('id'))[:68]}")
        cited_by = get_citations(kg, source_id=sid, direction="cited_by")
        print(f"  cited_by: {len(cited_by)}")
        for r in cited_by[:5]:
            print(f"    <- {(r.get('title') or r.get('id'))[:68]}")

    # similar_to on one chunk
    sub("similar_to: seed chunk from a top hit")
    seed_hits = search_chunks(kg, query="resistive switching mechanism", top_k=1)
    if seed_hits:
        seed = seed_hits[0]["id"]
        print(f"seed: {chunk_text.get(seed, '')[:160]}")
        sims = kg.chunks().similar_to(seed, top_k=5)
        for s in sims:
            t = chunk_text.get(s["id"], "")[:140].replace("\n", " ")
            print(f"  [{s['score']:.3f}] {t}")


def main() -> None:
    h("1. BIBLIOGRAPHY AUDIT")
    audit_bib("corpus_papers.bib", sample_bib(CORPUS / "corpus_papers.bib"))
    audit_bib("cited_works.bib", sample_bib(CORPUS / "cited_works.bib"))

    h("2. KNOWLEDGE GRAPH AUDIT")
    audit_graph(CORPUS / "knowledge_graph.json")

    h("3. FLUENT API / DISTILL QUERIES")
    run_fluent(CORPUS)


if __name__ == "__main__":
    main()
