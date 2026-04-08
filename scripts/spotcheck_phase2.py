"""Phase-2 spot check: citations + library.bib + author pages on mvp20_v4."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from wikify_simple.distill.author_pages import build_author_pages
from wikify_simple.ingest.bibtex import write_corpus_bibtex
from wikify_simple.ingest.citations import extract_citations
from wikify_simple.models import Document, DocSection
from wikify_simple.paths import CorpusPaths

V4 = Path("data/wikify_simple/corpora/mvp20_v4")
V5 = Path("data/wikify_simple/corpora/mvp20_v5")


def _load_v4_doc(p: Path) -> Document:
    d = json.loads(p.read_text(encoding="utf-8"))
    return Document(
        id=d["id"],
        source_path=d["source_path"],
        kind=d["kind"],
        title=d["title"],
        metadata=d.get("metadata", {}),
        markdown_path=d["markdown_path"],
        image_dir=d["image_dir"],
        sections=[
            DocSection(path=s["path"], chunk_ids=s["chunk_ids"], summary=s.get("summary", ""))
            for s in d.get("sections", [])
        ],
        n_chunks=d.get("n_chunks", 0),
        n_tokens=d.get("n_tokens", 0),
        citations=[],
    )


def main() -> None:
    docs: list[Document] = []
    total_cits = 0
    for f in sorted((V4 / "docs").glob("*.json")):
        doc = _load_v4_doc(f)
        md_path = V4 / "markdown" / f"{doc.id}.md"
        if md_path.exists():
            md = md_path.read_text(encoding="utf-8", errors="replace")
            doc.citations = extract_citations(md, doc.id)
        docs.append(doc)
        total_cits += len(doc.citations)

    V5.mkdir(parents=True, exist_ok=True)
    corpus_paths = CorpusPaths(root=V5)
    bib_path = write_corpus_bibtex(corpus_paths, docs)
    bib_text = bib_path.read_text(encoding="utf-8")
    n_entries = bib_text.count("@article")

    pages = build_author_pages(docs)
    primary_authors: Counter[str] = Counter()
    cited_authors: Counter[str] = Counter()
    for doc in docs:
        for a in (doc.metadata or {}).get("authors") or []:
            primary_authors[a.strip()] += 1
        for cit in doc.citations:
            for a in cit.get("authors") or []:
                cited_authors[a.strip()] += 1

    print(f"Total docs:                {len(docs)}")
    print(f"Total citations parsed:    {total_cits}")
    print(f"BibTeX entries written:    {n_entries}  ({bib_path})")
    print(f"Unique author pages built: {len(pages)}")
    print(
        f"  primary-only authors:    {sum(1 for p in pages if p.provenance['primary_count'] > 0)}"
    )
    print(
        f"  citation-mined authors:  {sum(1 for p in pages if p.provenance['from_citation_count'] > 0)}"
    )
    print()
    print("Top 5 most-cited researchers (from parsed bibliographies):")
    for name, n in cited_authors.most_common(5):
        print(f"  {n:4d}  {name}")


if __name__ == "__main__":
    main()
