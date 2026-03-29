"""Classify raw section headings into canonical academic section types.

Maps the wild variety of heading formats (numbered, Roman numerals,
bold markdown, journal artifacts) into a small enum of standard types.
This enables cross-paper queries like "get all conclusions" or
"compare methods sections."
"""

from __future__ import annotations

import re
from enum import Enum


class SectionType(str, Enum):
    """Canonical section types for academic papers."""

    ABSTRACT = "abstract"
    INTRODUCTION = "introduction"
    BACKGROUND = "background"  # literature review, related work
    METHODS = "methods"  # experimental, materials & methods, fabrication
    RESULTS = "results"  # results, results & discussion
    DISCUSSION = "discussion"  # standalone discussion (not merged with results)
    CONCLUSION = "conclusion"  # conclusion, summary, concluding remarks
    REFERENCES = "references"  # bibliography, works cited
    ACKNOWLEDGMENTS = "acknowledgments"
    APPENDIX = "appendix"  # supplementary, supporting information
    BODY = "body"  # topic-specific sections that don't fit above


# Patterns: list of (regex, SectionType). First match wins.
# Regexes match against cleaned, lowercased heading text.
_PATTERNS: list[tuple[re.Pattern, SectionType]] = [
    (re.compile(r"\babstract\b"), SectionType.ABSTRACT),
    # Introduction
    (re.compile(r"\bintroduction\b"), SectionType.INTRODUCTION),
    # Background / literature review
    (
        re.compile(r"\b(background|literature\s+review|related\s+work|prior\s+work)\b"),
        SectionType.BACKGROUND,
    ),
    # Methods / experimental
    (
        re.compile(
            r"\b(methods?\b|experimental|fabricat|procedure|characterization"
            r"|synthesis|preparation|device\s+structure|growth\s+condition"
            r"|deposition)"
        ),
        SectionType.METHODS,
    ),
    # Results (including "results and discussion")
    (re.compile(r"\bresult"), SectionType.RESULTS),
    # Standalone discussion
    (re.compile(r"\bdiscussion\b"), SectionType.DISCUSSION),
    # Conclusion
    (
        re.compile(r"\b(conclusion|concluding|summary\b(?!.*information))"),
        SectionType.CONCLUSION,
    ),
    # References
    (re.compile(r"\b(references?|bibliography|works\s+cited)\b"), SectionType.REFERENCES),
    # Acknowledgments
    (re.compile(r"\backnowledg"), SectionType.ACKNOWLEDGMENTS),
    # Appendix / supplementary
    (
        re.compile(r"\b(appendix|supplementary|supporting\s+information)\b"),
        SectionType.APPENDIX,
    ),
]


def _clean_heading(raw: str) -> str:
    """Strip numbering, markdown, and artifacts from a heading."""
    text = raw.strip()
    # Strip markdown bold/italic
    text = re.sub(r"[*_`]+", "", text)
    # Strip ■[] journal artifacts
    text = re.sub(r"■?\[([^\]]*)\]", r"\1", text)
    # Strip leading numbering: "1.", "I.", "II.", "3.2.", "A."
    text = re.sub(r"^[IVXLC]+\.\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^[\d.]+\s*", "", text)
    text = re.sub(r"^[A-Z]\.\s*", "", text)
    return text.strip()


def classify_section(heading: str) -> SectionType:
    """Classify a raw section heading into a canonical type.

    >>> classify_section("III. RESULTS AND DISCUSSION")
    <SectionType.RESULTS: 'results'>
    >>> classify_section("■[EXPERIMENTAL][PROCEDURE]")
    <SectionType.METHODS: 'methods'>
    >>> classify_section("4. Memristor devices for ANNs")
    <SectionType.BODY: 'body'>
    """
    cleaned = _clean_heading(heading).lower()

    if not cleaned or len(cleaned) < 3:
        return SectionType.BODY

    for pattern, section_type in _PATTERNS:
        if pattern.search(cleaned):
            return section_type

    return SectionType.BODY


def classify_section_path(section_path: str) -> SectionType:
    """Classify a dotted section path by checking each component.

    The most specific (deepest) match wins. If no component matches,
    returns BODY.

    >>> classify_section_path("3.Results and discussion.3.1.Electrical measurements")
    <SectionType.RESULTS: 'results'>
    """
    if not section_path:
        return SectionType.BODY

    # Split on dots, classify each part, take the most specific non-BODY match
    parts = section_path.split(".")
    best = SectionType.BODY
    for part in parts:
        part = part.strip()
        if not part:
            continue
        classified = classify_section(part)
        if classified != SectionType.BODY:
            best = classified  # Keep deepest match

    return best
