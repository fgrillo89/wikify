"""People identification across the corpus.

Discovers researchers, authors, and other people mentioned in corpus documents.
People are stored as ConceptRecord rows with concept_type='person'.
"""

from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher

from wikify.store.models import ConceptRecord, Paper
from wikify.wiki.builder import slugify

# Titles / honorifics to strip from names
_TITLE_PREFIXES = re.compile(
    r"^(Prof\.?|Professor|Dr\.?|Sir|Dame|Mr\.?|Mrs\.?|Ms\.?|Mx\.?)\s+",
    re.IGNORECASE,
)


def _is_cjk(text: str) -> bool:
    """Return True if every non-space character is CJK."""
    for ch in text:
        if ch.isspace():
            continue
        if not unicodedata.category(ch).startswith("Lo"):
            return False
    return bool(text.strip())


def normalize_person_name(name: str) -> str:
    """Normalize a person name to 'Firstname Lastname' form.

    Handles:
    - "Yang, J.J." -> "J.J. Yang"
    - "J. J. Yang" -> "J.J. Yang"
    - "  extra   spaces  " -> "Extra Spaces"
    - Strips titles: Prof., Dr., etc.
    - CJK names are kept as-is.
    """
    if not name or not isinstance(name, str):
        return ""
    name = name.strip()
    if not name:
        return ""

    # CJK names: keep as-is
    if _is_cjk(name):
        return name

    # Strip titles
    name = _TITLE_PREFIXES.sub("", name).strip()

    # Handle "Last, First" format: split on first comma, swap
    if "," in name:
        parts = name.split(",", 1)
        last = parts[0].strip()
        first = parts[1].strip()
        name = f"{first} {last}"

    # Collapse initials: "J. J." -> "J.J."
    name = re.sub(r"(\b[A-Z])\.\s+(?=[A-Z]\.?\b)", r"\1.", name)

    # Collapse multiple spaces
    name = re.sub(r"\s+", " ", name).strip()

    return name


def match_person_to_authors(person_name: str, papers: list[Paper]) -> list[tuple[str, str]]:
    """Cross-reference a discovered person with Paper.parsed_authors.

    Returns list of (paper_id, matched_author_name) tuples where the person
    matches an author with fuzzy threshold >= 0.80.
    """
    norm_person = normalize_person_name(person_name).lower()
    if not norm_person:
        return []

    matches: list[tuple[str, str]] = []
    for paper in papers:
        for author in paper.parsed_authors:
            norm_author = normalize_person_name(author).lower()
            if not norm_author:
                continue
            ratio = SequenceMatcher(None, norm_person, norm_author).ratio()
            if ratio >= 0.80:
                matches.append((paper.id, author))
    return matches


def deduplicate_people(
    new_people: list[dict],
    existing_concepts: list[ConceptRecord],
    threshold: float = 0.85,
) -> list[dict]:
    """Deduplicate people against existing ConceptRecord entries.

    Args:
        new_people: List of dicts with keys: name, aliases, role,
            affiliations, contributions.
        existing_concepts: Existing ConceptRecord rows with
            concept_type='person'.
        threshold: SequenceMatcher ratio threshold for fuzzy matching.

    Returns:
        Deduplicated list (existing matches removed, new people only).
    """
    # Build a lookup of existing normalized names and aliases
    existing_names: list[tuple[str, list[str]]] = []
    for cr in existing_concepts:
        norm_name = normalize_person_name(cr.name).lower()
        aliases = [normalize_person_name(a).lower() for a in cr.parsed_aliases]
        existing_names.append((norm_name, aliases))

    result: list[dict] = []
    for person in new_people:
        p_name = normalize_person_name(person.get("name", "")).lower()
        if not p_name:
            continue

        p_aliases = [normalize_person_name(a).lower() for a in person.get("aliases", []) if a]

        matched = False
        for ex_name, ex_aliases in existing_names:
            # Check main name against existing name
            if SequenceMatcher(None, p_name, ex_name).ratio() >= threshold:
                matched = True
                break
            # Check new name against existing aliases
            for ex_alias in ex_aliases:
                if SequenceMatcher(None, p_name, ex_alias).ratio() >= threshold:
                    matched = True
                    break
            if matched:
                break
            # Check new aliases against existing name
            for p_alias in p_aliases:
                if SequenceMatcher(None, p_alias, ex_name).ratio() >= threshold:
                    matched = True
                    break
            if matched:
                break

        if not matched:
            result.append(person)

    return result


def create_person_records(
    people: list[dict],
    epoch: int,
    domain: str = "",
) -> list[ConceptRecord]:
    """Create ConceptRecord entries for discovered people.

    Each person becomes a ConceptRecord with:
    - concept_type = "person"
    - definition = JSON with role, affiliations, contributions
    - aliases = JSON list of name variants

    Args:
        people: List of dicts with keys: name, aliases, role,
            affiliations, contributions.
        epoch: Current epoch number.
        domain: Domain string for the concept.

    Returns:
        List of ConceptRecord instances (not yet committed to DB).
    """
    records: list[ConceptRecord] = []
    for person in people:
        name = normalize_person_name(person.get("name", ""))
        if not name:
            continue

        slug = slugify(name)
        aliases = person.get("aliases", [])
        definition_data = {
            "role": person.get("role", ""),
            "affiliations": person.get("affiliations", []),
            "contributions": person.get("contributions", []),
        }

        record = ConceptRecord(
            id=slug,
            name=name,
            aliases=json.dumps(aliases),
            definition=json.dumps(definition_data),
            concept_type="person",
            domain=domain,
            importance=0.0,
            epoch_discovered=epoch,
            epoch_last_updated=epoch,
            article_status="none",
            article_path="",
        )
        records.append(record)

    return records
