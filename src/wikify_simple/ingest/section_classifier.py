"""Classify raw section headings into canonical academic section types.

Ported from the legacy ``wikify.ingest.extract.section_classifier``. Maps
the wild variety of heading formats (numbered, Roman numerals, bold
markdown, journal artifacts) into a small enum of standard types. This
enables cross-document queries like "get all conclusions" or "compare
methods sections."
"""

from __future__ import annotations

import re
from enum import Enum


class SectionType(str, Enum):
    """Canonical section types for academic papers."""

    ABSTRACT = "abstract"
    INTRODUCTION = "introduction"
    BACKGROUND = "background"
    METHODS = "methods"
    RESULTS = "results"
    DISCUSSION = "discussion"
    CONCLUSION = "conclusion"
    REFERENCES = "references"
    ACKNOWLEDGMENTS = "acknowledgments"
    APPENDIX = "appendix"
    BODY = "body"


# Patterns: list of (regex, SectionType). First match wins.
# Regexes match against cleaned, lowercased heading text.
_PATTERNS: list[tuple[re.Pattern, SectionType]] = [
    (re.compile(r"\babstract\b"), SectionType.ABSTRACT),
    (re.compile(r"\bintroduction\b"), SectionType.INTRODUCTION),
    (
        re.compile(r"\b(background|literature\s+review|related\s+work|prior\s+work)\b"),
        SectionType.BACKGROUND,
    ),
    (
        re.compile(
            r"\b(methods?\b|experimental|fabricat|procedure|characterization"
            r"|synthesis|preparation|device\s+structure|growth\s+condition"
            r"|deposition)"
        ),
        SectionType.METHODS,
    ),
    (re.compile(r"\bresult"), SectionType.RESULTS),
    (re.compile(r"\bdiscussion\b"), SectionType.DISCUSSION),
    (
        re.compile(r"\b(conclusion|concluding|summary\b(?!.*information))"),
        SectionType.CONCLUSION,
    ),
    (re.compile(r"\b(references?|bibliography|works\s+cited)\b"), SectionType.REFERENCES),
    (re.compile(r"\backnowledg"), SectionType.ACKNOWLEDGMENTS),
    (
        re.compile(r"\b(appendix|supplementary|supporting\s+information)\b"),
        SectionType.APPENDIX,
    ),
]


def _clean_heading(raw: str) -> str:
    """Strip numbering, markdown, and artifacts from a heading."""
    text = raw.strip()
    text = re.sub(r"[*_`]+", "", text)
    # Strip journal artifacts like ■[EXPERIMENTAL][PROCEDURE]
    text = re.sub(r"[^a-zA-Z0-9\s]?\[([^\]]*)\]", r"\1", text)
    # Strip leading numbering: "1.", "I.", "II.", "3.2.", "A."
    text = re.sub(r"^[IVXLC]+\.\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^[\d.]+\s*", "", text)
    text = re.sub(r"^[A-Z]\.\s*", "", text)
    return text.strip()


def classify_section(heading: str) -> SectionType:
    """Classify a raw section heading into a canonical type."""
    cleaned = _clean_heading(heading).lower()
    if not cleaned or len(cleaned) < 3:
        return SectionType.BODY
    for pattern, section_type in _PATTERNS:
        if pattern.search(cleaned):
            return section_type
    return SectionType.BODY


def classify_section_path(path: list[str]) -> SectionType:
    """Classify a section path (list of heading titles) by checking each.

    The most specific (deepest) match wins. If no component matches,
    returns BODY.
    """
    if not path:
        return SectionType.BODY
    best = SectionType.BODY
    for part in path:
        classified = classify_section(part)
        if classified != SectionType.BODY:
            best = classified
    return best
