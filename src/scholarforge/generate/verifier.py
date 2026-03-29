"""Deterministic verification for generated paper sections and full documents.

Implements P0 Plan Verification Loop and P0 Independent Verification Pass
from the harness refactor plan. All checks are deterministic — no LLM calls.
"""

from __future__ import annotations

import re
from collections import Counter

from pydantic import BaseModel

from scholarforge.store.models import PaperPlan, SectionPlan

# Common English stopwords to exclude from key-term extraction.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "can",
        "shall",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "not",
        "no",
        "as",
        "if",
        "so",
        "than",
        "too",
        "very",
        "also",
        "such",
        "each",
        "which",
        "who",
        "whom",
        "what",
        "where",
        "when",
        "how",
        "all",
        "any",
        "both",
        "few",
        "more",
        "most",
        "other",
        "some",
        "their",
        "them",
        "they",
        "we",
        "our",
        "us",
        "he",
        "she",
        "his",
        "her",
        "you",
        "your",
        "i",
        "me",
        "my",
        "about",
        "above",
        "after",
        "before",
        "between",
        "into",
        "through",
        "during",
        "over",
        "under",
        "then",
        "there",
        "here",
        "up",
        "out",
        "off",
        "down",
        "only",
        "own",
        "same",
        "just",
        "because",
        "while",
        "however",
        "therefore",
        "although",
        "since",
        "until",
        "yet",
        "still",
        "even",
        "well",
        "much",
        "many",
    }
)


def _word_count(text: str) -> int:
    """Count words in text."""
    return len(text.split())


def _extract_ref_markers(text: str) -> list[str]:
    """Extract [REF:...] markers from text."""
    return re.findall(r"\[REF:([^\]]+)\]", text)


def _extract_numbered_refs(text: str) -> list[str]:
    """Extract numbered citation markers like [1], [2,3], [1-3] from text."""
    return re.findall(r"\[(\d+(?:[,\-\s]*\d+)*)\]", text)


def _extract_key_terms(text: str, min_count: int = 1) -> set[str]:
    """Extract significant terms from text (lowercased, stopwords removed).

    Returns words that appear at least *min_count* times.
    """
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    counts = Counter(words)
    return {word for word, count in counts.items() if count >= min_count and word not in _STOPWORDS}


# ── Section-level verification (P0) ─────────────────────────────────────────


def verify_section_against_plan(
    section_text: str,
    plan: SectionPlan,
    source_papers: list[str] | None = None,
) -> list[str]:
    """Check if a written section complies with its plan.

    All checks are deterministic — no LLM calls.

    Returns a list of issue strings (empty means the section passes).
    """
    if source_papers is None:
        source_papers = []

    issues: list[str] = []

    # 1. Word count check: within 40% of target_tokens
    if plan.target_tokens > 0:
        wc = _word_count(section_text)
        lower = int(plan.target_tokens * 0.6)
        upper = int(plan.target_tokens * 1.4)
        if wc < lower:
            issues.append(
                f"Word count too low: {wc} words vs target {plan.target_tokens} "
                f"(minimum {lower}). Expand the section."
            )
        elif wc > upper:
            issues.append(
                f"Word count too high: {wc} words vs target {plan.target_tokens} "
                f"(maximum {upper}). Trim the section."
            )

    # 2. Source paper coverage: at least 50% of assigned papers cited
    if plan.source_papers:
        ref_markers = _extract_ref_markers(section_text)
        cited_papers = set()
        for assigned in plan.source_papers:
            assigned_lower = assigned.lower()
            for marker in ref_markers:
                if assigned_lower in marker.lower() or marker.lower() in assigned_lower:
                    cited_papers.add(assigned)
                    break
        coverage = len(cited_papers) / len(plan.source_papers)
        if coverage < 0.5:
            uncited = [p for p in plan.source_papers if p not in cited_papers]
            issues.append(
                f"Source paper coverage too low: cited "
                f"{len(cited_papers)}/{len(plan.source_papers)} "
                f"assigned papers ({coverage:.0%}). "
                f"Missing: {', '.join(uncited[:5])}"
            )

    # 3. Description coverage: key terms from plan.description in section
    if plan.description:
        desc_terms = _extract_key_terms(plan.description, min_count=1)
        section_terms = _extract_key_terms(section_text, min_count=1)
        if desc_terms:
            matched = desc_terms & section_terms
            coverage = len(matched) / len(desc_terms)
            if coverage < 0.3:
                missing = desc_terms - matched
                issues.append(
                    f"Description coverage low: only {coverage:.0%} of key terms from "
                    f"the plan description appear in the section. "
                    f"Missing terms: {', '.join(sorted(missing)[:10])}"
                )

    return issues


# ── Full-paper verification (P0) ────────────────────────────────────────────


class PaperVerificationResult(BaseModel):
    """Result of deterministic verification on an assembled paper."""

    passed: bool
    total_words: int
    sections_found: int
    sections_planned: int
    unresolved_refs: list[str] = []
    duplicate_content: list[str] = []
    issues: list[str] = []


def _extract_section_headings(markdown: str) -> list[str]:
    """Extract markdown headings (## or ###) from the document."""
    headings: list[str] = []
    for line in markdown.splitlines():
        m = re.match(r"^(#{1,4})\s+(.+)$", line.strip())
        if m:
            headings.append(m.group(2).strip())
    return headings


def _extract_sentences(text: str) -> list[str]:
    """Split text into sentences (simple heuristic)."""
    # Split on period/exclamation/question followed by space or end-of-string
    raw = re.split(r"(?<=[.!?])\s+", text.strip())
    return [s.strip() for s in raw if len(s.strip()) > 20]


def _split_sections(markdown: str) -> dict[str, str]:
    """Split markdown into sections keyed by heading."""
    sections: dict[str, str] = {}
    current_heading = ""
    current_lines: list[str] = []

    for line in markdown.splitlines():
        m = re.match(r"^#{1,4}\s+(.+)$", line.strip())
        if m:
            if current_heading:
                sections[current_heading] = "\n".join(current_lines).strip()
            current_heading = m.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_heading:
        sections[current_heading] = "\n".join(current_lines).strip()

    return sections


def verify_paper(
    full_markdown: str,
    plan: PaperPlan,
) -> PaperVerificationResult:
    """Run deterministic checks on the fully assembled paper.

    Checks:
    - All planned sections have headings in the output
    - No [?:...] unresolved reference markers
    - Total word count within 30% of plan.target_length
    - No two sections share >3 identical sentences (copy-paste detection)
    """
    issues: list[str] = []

    # Total word count
    total_words = _word_count(full_markdown)

    # Check section headings
    found_headings = _extract_section_headings(full_markdown)
    found_lower = {h.lower() for h in found_headings}
    planned_sections = plan.flat_sections()
    sections_planned = len(planned_sections)

    missing_sections: list[str] = []
    for sp in planned_sections:
        if sp.heading.lower() not in found_lower:
            missing_sections.append(sp.heading)
    sections_found = sections_planned - len(missing_sections)

    if missing_sections:
        issues.append(f"Missing planned sections: {', '.join(missing_sections)}")

    # Unresolved reference markers [?:...]
    unresolved = re.findall(r"\[\?:[^\]]*\]", full_markdown)
    if unresolved:
        issues.append(
            f"Found {len(unresolved)} unresolved reference markers: {', '.join(unresolved[:5])}"
        )

    # Word count vs target
    if plan.target_length > 0:
        lower = int(plan.target_length * 0.7)
        upper = int(plan.target_length * 1.3)
        if total_words < lower:
            issues.append(
                f"Paper too short: {total_words} words vs target {plan.target_length} "
                f"(minimum {lower})"
            )
        elif total_words > upper:
            issues.append(
                f"Paper too long: {total_words} words vs target {plan.target_length} "
                f"(maximum {upper})"
            )

    # Duplicate content: check for >3 identical sentences across sections
    doc_sections = _split_sections(full_markdown)
    duplicate_content: list[str] = []
    section_names = list(doc_sections.keys())
    for i in range(len(section_names)):
        sentences_i = set(_extract_sentences(doc_sections[section_names[i]]))
        for j in range(i + 1, len(section_names)):
            sentences_j = set(_extract_sentences(doc_sections[section_names[j]]))
            shared = sentences_i & sentences_j
            if len(shared) > 3:
                pair = (
                    f"'{section_names[i]}' and '{section_names[j]}' share {len(shared)} sentences"
                )
                duplicate_content.append(pair)

    if duplicate_content:
        issues.append(f"Duplicate content detected: {'; '.join(duplicate_content)}")

    passed = len(issues) == 0

    return PaperVerificationResult(
        passed=passed,
        total_words=total_words,
        sections_found=sections_found,
        sections_planned=sections_planned,
        unresolved_refs=unresolved,
        duplicate_content=duplicate_content,
        issues=issues,
    )
