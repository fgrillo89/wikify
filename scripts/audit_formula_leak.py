"""Audit Granite-Docling formula contamination across a corpus build.

Walks every markdown sidecar and (when present) every cached
``derived/doclingdoc/*.json`` and counts:

* files with leaked ``<formula>`` / ``<loc_`` tokens,
* total leaked-tag count across the corpus,
* total formula blocks scanned,
* contaminated formula blocks,
* contaminated-block rate = contaminated formula blocks / total
  formula blocks,
* contaminated-token share = tokens inside contaminated blocks / all
  formula-block tokens,
* longest repeated 3-gram run inside any single block.

Reports the top-10 worst offenders by per-paper contamination rate.
The audit is read-only and never mutates the corpus.

Usage:
    uv run python scripts/audit_formula_leak.py build/ald_docling_2026_05_06/
    uv run python scripts/audit_formula_leak.py --top 20 <corpus>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from wikify.ingest.parsers.docling import (
    _find_leak_sentinels,
    _longest_repeated_ngram_run,
)

# Markdown formula block patterns — both clean ``$$ ... $$`` blocks and
# leaked ``<formula> ... </formula>`` blocks. We intentionally include
# the leaked variant so a paper whose markdown is ENTIRELY inside the
# wrapper still gets a contamination signal.
_DISPLAY_MATH_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_FORMULA_TAG_RE = re.compile(r"<formula[^>]*>(.+?)</formula>", re.DOTALL)


@dataclass
class FormulaBlockStats:
    """Aggregate counts for one source (markdown sidecar OR docling JSON)."""

    source: str
    blocks: int = 0
    contaminated_blocks: int = 0
    leaked_tokens: int = 0
    block_tokens: int = 0
    contaminated_tokens: int = 0
    longest_run: int = 0
    examples: list[str] = field(default_factory=list)

    @property
    def rate(self) -> float:
        return self.contaminated_blocks / self.blocks if self.blocks else 0.0

    @property
    def token_share(self) -> float:
        if not self.block_tokens:
            return 0.0
        return self.contaminated_tokens / self.block_tokens


def _scan_block(stats: FormulaBlockStats, text: str) -> None:
    """Update ``stats`` with one formula block's measurements."""
    stats.blocks += 1
    n_tokens = len(text.split())
    stats.block_tokens += n_tokens
    leaks = _find_leak_sentinels(text)
    run = _longest_repeated_ngram_run(text)
    if run > stats.longest_run:
        stats.longest_run = run
    is_contaminated = bool(leaks) or run > 10
    if is_contaminated:
        stats.contaminated_blocks += 1
        stats.contaminated_tokens += n_tokens
        # Each leak SUBSTRING that occurs is counted once (cheap proxy
        # for "leaked tag count" — the paper-level metric we want is
        # "is this paper polluted", not "how many tags exactly").
        for s in leaks:
            stats.leaked_tokens += text.count(s)
        if len(stats.examples) < 3:
            stats.examples.append(text[:140].replace("\n", " "))


def _scan_markdown_sidecar(path: Path) -> FormulaBlockStats:
    """Scan a markdown sidecar's ``$$...$$`` and ``<formula>...</formula>``."""
    stats = FormulaBlockStats(source=path.name)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return stats
    for m in _DISPLAY_MATH_RE.finditer(text):
        _scan_block(stats, m.group(1))
    for m in _FORMULA_TAG_RE.finditer(text):
        _scan_block(stats, m.group(1))
    return stats


def _scan_docling_json(path: Path) -> FormulaBlockStats:
    """Scan ``FormulaItem.text`` from a cached ``DoclingDocument`` JSON.

    Uses the JSON layer directly — no Docling import, no model load —
    by walking ``texts`` and selecting items with ``label == "formula"``.
    The cached JSON is faithful to ``DoclingDocument.save_as_json``,
    which serialises FormulaItems under the ``texts`` array with their
    ``label`` discriminator preserved.
    """
    stats = FormulaBlockStats(source=path.name)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return stats
    texts = doc.get("texts") or []
    for item in texts:
        if item.get("label") != "formula":
            continue
        latex = (item.get("text") or "").strip()
        if latex:
            _scan_block(stats, latex)
    return stats


def _format_offender_row(stats: FormulaBlockStats) -> str:
    # ASCII-sanitize the source name before printing — Windows
    # ``cp1252`` consoles choke on U+2010 (Unicode hyphen) and similar
    # punctuation that appears in real paper filenames. The mapping
    # is lossy on purpose: this is a diagnostic line, not a dataset.
    name = stats.source.encode("ascii", "replace").decode("ascii")
    return (
        f"  rate={stats.rate:5.1%} "
        f"({stats.contaminated_blocks:>3}/{stats.blocks:>3}) "
        f"tokens={stats.token_share:5.1%} "
        f"longest_run={stats.longest_run:>4}  "
        f"{name}"
    )


def _aggregate(per_paper: list[FormulaBlockStats]) -> dict[str, float]:
    total_blocks = sum(s.blocks for s in per_paper)
    contaminated_blocks = sum(s.contaminated_blocks for s in per_paper)
    block_tokens = sum(s.block_tokens for s in per_paper)
    contaminated_tokens = sum(s.contaminated_tokens for s in per_paper)
    leaked_tokens = sum(s.leaked_tokens for s in per_paper)
    files_with_leaks = sum(1 for s in per_paper if s.contaminated_blocks > 0)
    return {
        "files_scanned": len(per_paper),
        "files_with_leaks": files_with_leaks,
        "total_blocks": total_blocks,
        "contaminated_blocks": contaminated_blocks,
        "block_rate": (
            contaminated_blocks / total_blocks if total_blocks else 0.0
        ),
        "block_tokens": block_tokens,
        "contaminated_tokens": contaminated_tokens,
        "token_share": (
            contaminated_tokens / block_tokens if block_tokens else 0.0
        ),
        "leaked_tokens": leaked_tokens,
    }


def _print_report(
    label: str,
    per_paper: list[FormulaBlockStats],
    *,
    top: int,
    out: object,
) -> None:
    summary = _aggregate(per_paper)
    print(f"\n[{label}]", file=out)
    if not per_paper:
        print("  (no sources scanned)", file=out)
        return
    print(
        f"  files: {summary['files_scanned']}  "
        f"with_leaks: {summary['files_with_leaks']}  "
        f"blocks: {summary['total_blocks']}  "
        f"contaminated: {summary['contaminated_blocks']}  "
        f"block_rate: {summary['block_rate']:.2%}",
        file=out,
    )
    print(
        f"  tokens: {summary['block_tokens']}  "
        f"contaminated_tokens: {summary['contaminated_tokens']}  "
        f"token_share: {summary['token_share']:.2%}  "
        f"leaked_tag_count: {summary['leaked_tokens']}",
        file=out,
    )

    offenders = sorted(
        (s for s in per_paper if s.contaminated_blocks > 0),
        key=lambda s: (s.rate, s.contaminated_blocks),
        reverse=True,
    )
    if offenders:
        print(f"  top {top} offenders:", file=out)
        for s in offenders[:top]:
            print(_format_offender_row(s), file=out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "corpus",
        type=Path,
        help="corpus root (e.g. build/ald_docling_2026_05_06/)",
    )
    p.add_argument(
        "--top",
        type=int,
        default=10,
        help="number of worst offenders to print per source",
    )
    args = p.parse_args(argv)

    corpus_root: Path = args.corpus
    md_dir = corpus_root / "markdown"
    doc_dir = corpus_root / "derived" / "doclingdoc"

    if not md_dir.is_dir() and not doc_dir.is_dir():
        print(
            f"error: neither {md_dir} nor {doc_dir} exists; not a corpus root?",
            file=sys.stderr,
        )
        return 2

    md_stats: list[FormulaBlockStats] = []
    if md_dir.is_dir():
        for path in sorted(md_dir.glob("*.md")):
            md_stats.append(_scan_markdown_sidecar(path))

    doc_stats: list[FormulaBlockStats] = []
    if doc_dir.is_dir():
        for path in sorted(doc_dir.glob("*.json")):
            doc_stats.append(_scan_docling_json(path))

    _print_report(
        "markdown sidecars", md_stats, top=args.top, out=sys.stdout,
    )
    _print_report(
        "cached DoclingDocument JSON",
        doc_stats,
        top=args.top,
        out=sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
