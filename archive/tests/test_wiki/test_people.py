"""Tests for wikify.wiki.people — person discovery and deduplication."""

from __future__ import annotations

import json

from wikify.core.store.models import ConceptRecord, Paper
from wikify.wiki.people import (
    create_person_records,
    deduplicate_people,
    match_person_to_authors,
    normalize_person_name,
)

# ── normalize_person_name ───────────────────────────────────────────────────


class TestNormalizePersonName:
    def test_last_comma_first(self):
        assert normalize_person_name("Yang, J.J.") == "J.J. Yang"

    def test_last_comma_first_with_spaces(self):
        assert normalize_person_name("Yang, J. J.") == "J.J. Yang"

    def test_strip_prof_title(self):
        assert normalize_person_name("Prof. Alice Smith") == "Alice Smith"

    def test_strip_dr_title(self):
        assert normalize_person_name("Dr. Bob Jones") == "Bob Jones"

    def test_strip_professor_title(self):
        assert normalize_person_name("Professor Carol White") == "Carol White"

    def test_collapse_initials(self):
        assert normalize_person_name("J. J. Yang") == "J.J. Yang"

    def test_extra_spaces(self):
        result = normalize_person_name("  extra   spaces  ")
        assert result == "extra spaces"

    def test_empty_string(self):
        assert normalize_person_name("") == ""

    def test_none_input(self):
        assert normalize_person_name(None) == ""

    def test_simple_name(self):
        assert normalize_person_name("Alice Smith") == "Alice Smith"

    def test_single_name(self):
        assert normalize_person_name("Madonna") == "Madonna"


# ── deduplicate_people ──────────────────────────────────────────────────────


class TestDeduplicatePeople:
    def _make_concept(self, name: str, aliases: list[str] | None = None) -> ConceptRecord:
        return ConceptRecord(
            id=name.lower().replace(" ", "_"),
            name=name,
            aliases=json.dumps(aliases or []),
            concept_type="person",
        )

    def test_removes_exact_match(self):
        existing = [self._make_concept("Alice Smith")]
        new = [{"name": "Alice Smith", "aliases": [], "role": "researcher"}]
        result = deduplicate_people(new, existing)
        assert result == []

    def test_removes_fuzzy_match(self):
        existing = [self._make_concept("J.J. Yang")]
        new = [{"name": "J. J. Yang", "aliases": [], "role": "researcher"}]
        result = deduplicate_people(new, existing)
        assert result == []

    def test_keeps_genuinely_new(self):
        existing = [self._make_concept("Alice Smith")]
        new = [{"name": "Bob Jones", "aliases": [], "role": "researcher"}]
        result = deduplicate_people(new, existing)
        assert len(result) == 1
        assert result[0]["name"] == "Bob Jones"

    def test_alias_match_removes(self):
        existing = [self._make_concept("Joshua Yang", aliases=["J.J. Yang"])]
        new = [{"name": "J.J. Yang", "aliases": [], "role": "researcher"}]
        result = deduplicate_people(new, existing)
        assert result == []

    def test_new_alias_against_existing_name(self):
        existing = [self._make_concept("Joshua Yang")]
        new = [{"name": "Josh Yang", "aliases": ["Joshua Yang"], "role": "researcher"}]
        result = deduplicate_people(new, existing)
        assert result == []

    def test_empty_name_skipped(self):
        existing = [self._make_concept("Alice Smith")]
        new = [{"name": "", "aliases": [], "role": ""}]
        result = deduplicate_people(new, existing)
        assert result == []


# ── match_person_to_authors ─────────────────────────────────────────────────


class TestMatchPersonToAuthors:
    def _make_paper(self, paper_id: str, authors: list[str]) -> Paper:
        return Paper(
            id=paper_id,
            title="Test Paper",
            authors=json.dumps(authors),
        )

    def test_finds_matching_author(self):
        papers = [self._make_paper("p1", ["J.J. Yang", "Alice Smith"])]
        matches = match_person_to_authors("J.J. Yang", papers)
        assert len(matches) == 1
        assert matches[0] == ("p1", "J.J. Yang")

    def test_fuzzy_matches_variant(self):
        papers = [self._make_paper("p1", ["J. J. Yang"])]
        matches = match_person_to_authors("J.J. Yang", papers)
        assert len(matches) == 1

    def test_no_match(self):
        papers = [self._make_paper("p1", ["Alice Smith"])]
        matches = match_person_to_authors("Bob Jones", papers)
        assert matches == []

    def test_multiple_papers(self):
        papers = [
            self._make_paper("p1", ["Alice Smith"]),
            self._make_paper("p2", ["Alice Smith", "Bob Jones"]),
        ]
        matches = match_person_to_authors("Alice Smith", papers)
        assert len(matches) == 2

    def test_empty_person(self):
        papers = [self._make_paper("p1", ["Alice Smith"])]
        matches = match_person_to_authors("", papers)
        assert matches == []


# ── create_person_records ───────────────────────────────────────────────────


class TestCreatePersonRecords:
    def test_creates_correct_record(self):
        people = [
            {
                "name": "Alice Smith",
                "aliases": ["A. Smith"],
                "role": "Professor",
                "affiliations": ["MIT"],
                "contributions": ["ALD process"],
            }
        ]
        records = create_person_records(people, epoch=1, domain="material_science")
        assert len(records) == 1
        r = records[0]
        assert r.concept_type == "person"
        assert r.name == "Alice Smith"
        assert r.id == "alice_smith"
        assert r.epoch_discovered == 1
        assert r.domain == "material_science"

        defn = json.loads(r.definition)
        assert defn["role"] == "Professor"
        assert defn["affiliations"] == ["MIT"]

        aliases = json.loads(r.aliases)
        assert "A. Smith" in aliases

    def test_skips_empty_name(self):
        people = [{"name": "", "aliases": [], "role": ""}]
        records = create_person_records(people, epoch=1)
        assert records == []

    def test_normalizes_name(self):
        people = [
            {
                "name": "Prof. Yang, J.J.",
                "aliases": [],
                "role": "researcher",
                "affiliations": [],
                "contributions": [],
            }
        ]
        records = create_person_records(people, epoch=2)
        assert len(records) == 1
        assert records[0].name == "J.J. Yang"
