"""Claim and page sampling for human evaluation.

Extracts factual sentences from wiki pages and creates stratified samples
for the faithfulness audit and verifiability/utility evaluation.
"""

from __future__ import annotations

import json
import random
import re
import statistics
from pathlib import Path

from wikify.bundle.wiki.page import Page, load_bundle

# Evidence marker pattern: [^eN] or [^e1][^e2] etc.
_EVIDENCE_RE = re.compile(r"\[\^[^\]]+\]")

# H2 sections to exclude from claim extraction.
_SKIP_SECTIONS = {"evidence", "references", "boilerplate", "see also"}


def _is_factual_sentence(line: str) -> bool:
    """Return True if the line looks like a factual prose sentence."""
    stripped = line.strip()
    if not stripped:
        return False
    # Skip headings
    if stripped.startswith("#"):
        return False
    # Skip list-only markers or very short fragments
    if len(stripped) < 20:
        return False
    # Skip evidence footnote definitions
    if re.match(r"^\[\^", stripped):
        return False
    return True


def _in_skip_section(line_idx: int, body: str) -> bool:
    """Check if a line index falls inside a section we want to skip."""
    lines = body.splitlines()
    current_section = ""
    for i, ln in enumerate(lines):
        m = re.match(r"^##\s+(.+?)\s*$", ln)
        if m:
            current_section = m.group(1).strip().lower()
        if i == line_idx:
            return current_section in _SKIP_SECTIONS
    return False


def _extract_factual_lines(page: Page) -> list[dict]:
    """Extract factual sentences from a page's clean body.

    Returns list of dicts with keys: text, evidence_markers, blinded_text.
    """
    lines = page.body_clean.splitlines()
    results = []
    current_section = ""
    for line in lines:
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            current_section = m.group(1).strip().lower()
            continue
        if current_section in _SKIP_SECTIONS:
            continue
        if not _is_factual_sentence(line):
            continue
        markers = _EVIDENCE_RE.findall(line)
        blinded = _EVIDENCE_RE.sub("", line).strip()
        # Collapse any double-spaces left by marker removal.
        blinded = re.sub(r"  +", " ", blinded)
        results.append({
            "text": line.strip(),
            "evidence_markers": markers,
            "blinded_text": blinded,
        })
    return results


def _page_evidence_count(page: Page) -> int:
    return len(page.evidence)


def _stratify_pages(
    pages: list[Page],
) -> dict[str, list[Page]]:
    """Split pages into 'high' and 'low' evidence strata."""
    if not pages:
        return {"high": [], "low": []}
    counts = [_page_evidence_count(p) for p in pages]
    med = statistics.median(counts)
    high = [p for p, c in zip(pages, counts) if c >= med]
    low = [p for p, c in zip(pages, counts) if c < med]
    # If all pages have the same evidence count, put all in "high".
    if not low:
        return {"high": high, "low": []}
    return {"high": high, "low": low}


def sample_claims(
    bundle_path: str | Path,
    n: int = 100,
    strata: tuple[str, ...] = ("high", "low"),
) -> list[dict]:
    """Sample n factual claims from a bundle, stratified by evidence.

    Returns a list of claim dicts with keys:
      claim_text, page_id, page_title, stratum,
      evidence_marker, source_passage.
    """
    bundle = load_bundle(bundle_path)
    stratified = _stratify_pages(bundle.pages)

    # Build evidence lookup: marker -> quote
    evidence_lookup: dict[str, dict[str, str]] = {}
    for page in bundle.pages:
        page_map: dict[str, str] = {}
        for ev in page.evidence:
            page_map[f"[^{ev.marker}]"] = ev.quote
        evidence_lookup[page.id] = page_map

    # Collect all candidate claims per stratum.
    candidates: dict[str, list[dict]] = {s: [] for s in strata}
    for stratum in strata:
        for page in stratified.get(stratum, []):
            lines = _extract_factual_lines(page)
            for line in lines:
                marker_str = (
                    line["evidence_markers"][0]
                    if line["evidence_markers"]
                    else ""
                )
                source = evidence_lookup.get(
                    page.id, {}
                ).get(marker_str, "")
                candidates[stratum].append({
                    "claim_text": line["blinded_text"],
                    "page_id": page.id,
                    "page_title": page.title,
                    "stratum": stratum,
                    "evidence_marker": marker_str,
                    "source_passage": source,
                })

    # Stratified sampling: equal split across active strata.
    active = [s for s in strata if candidates.get(s)]
    if not active:
        return []
    per_stratum = n // len(active)
    remainder = n % len(active)
    rng = random.Random(42)
    result: list[dict] = []
    for i, stratum in enumerate(active):
        pool = candidates[stratum]
        take = per_stratum + (1 if i < remainder else 0)
        take = min(take, len(pool))
        result.extend(rng.sample(pool, take))

    return result


def sample_pages(
    bundle_path: str | Path,
    n: int = 20,
) -> list[dict]:
    """Sample n pages from a bundle, stratified by length and evidence.

    Returns list of dicts with keys:
      page_id, title, kind, n_evidence, body_length, stratum.
    """
    bundle = load_bundle(bundle_path)
    if not bundle.pages:
        return []

    # Stratify by combined length + evidence quartile.
    pages = bundle.pages
    lengths = [len(p.body_clean) for p in pages]
    ev_counts = [_page_evidence_count(p) for p in pages]
    med_len = statistics.median(lengths)
    med_ev = statistics.median(ev_counts)

    strata: dict[str, list[Page]] = {
        "high_ev_long": [],
        "high_ev_short": [],
        "low_ev_long": [],
        "low_ev_short": [],
    }
    for p, ln, ec in zip(pages, lengths, ev_counts):
        ev_label = "high_ev" if ec >= med_ev else "low_ev"
        len_label = "long" if ln >= med_len else "short"
        strata[f"{ev_label}_{len_label}"].append(p)
    rng = random.Random(42)
    active = {k: v for k, v in strata.items() if v}
    per_stratum = n // max(len(active), 1)
    remainder = n % max(len(active), 1)

    result: list[dict] = []
    for i, (stratum, pool) in enumerate(sorted(active.items())):
        take = per_stratum + (1 if i < remainder else 0)
        take = min(take, len(pool))
        sampled = rng.sample(pool, take)
        for p in sampled:
            result.append({
                "page_id": p.id,
                "title": p.title,
                "kind": p.kind,
                "n_evidence": _page_evidence_count(p),
                "body_length": len(p.body_clean),
                "stratum": stratum,
            })
    return result


def save_sample(sample: list[dict], path: str | Path) -> None:
    """Write a sample list to JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sample, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_sample(path: str | Path) -> list[dict]:
    """Read a sample list from JSON."""
    return json.loads(Path(path).read_text(encoding="utf-8"))
