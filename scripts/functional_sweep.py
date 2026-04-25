"""Functional sweep: assess bib + graph quality on ald_all_marker.

The fluent-API section was removed in W0 of the skill-centric redesign.
It depended on `wikify.distill.kg_tools`, a module that was never
created — the section had been broken since the script was written.
Add a fresh fluent-API diagnostic against `wikify.corpus.graph` if
that capability is needed again.
"""

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
    print("\n  hygiene:")
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
        touched.add(e["source"])
        touched.add(e["target"])
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


def main() -> None:
    h("1. BIBLIOGRAPHY AUDIT")
    audit_bib("corpus_papers.bib", sample_bib(CORPUS / "corpus_papers.bib"))
    audit_bib("cited_works.bib", sample_bib(CORPUS / "cited_works.bib"))

    h("2. KNOWLEDGE GRAPH AUDIT")
    audit_graph(CORPUS / "knowledge_graph.json")


if __name__ == "__main__":
    main()
