"""Journal profile system for formatting generated papers to journal standards."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel

_JOURNALS_DIR = Path(__file__).parent / "journals"


class JournalProfile(BaseModel):
    """Formatting and structural requirements for a target journal."""

    name: str
    publisher: str = ""
    citation_style: Literal["numbered", "author_year"] = "numbered"
    word_limit: Optional[int] = None
    abstract_word_limit: Optional[int] = None
    required_sections: list[str] = []
    font_family: str = "Times New Roman"
    font_size_pt: int = 12
    line_spacing: float = 2.0
    column_layout: Literal["single", "double"] = "single"
    reference_format: str = "{number}. {authors}. {title}. {journal} {volume}, {pages} ({year})."
    figure_min_dpi: int = 300
    notes: str = ""


# ── Default profile (used when no journal specified) ─────────────────────────

_DEFAULT_PROFILE = JournalProfile(
    name="Generic Academic",
    publisher="",
    citation_style="numbered",
    word_limit=None,
    abstract_word_limit=250,
    required_sections=[
        "Abstract",
        "Introduction",
        "Conclusion",
    ],
    font_family="Times New Roman",
    font_size_pt=12,
    line_spacing=2.0,
    reference_format="{number}. {authors}. {title}. ({year}).",
)


def load_journal_profile(name: str) -> JournalProfile:
    """Load a journal profile by name (fuzzy-matched against available profiles).

    Falls back to the default generic profile if no match found.
    """
    if not name:
        return _DEFAULT_PROFILE

    available = _list_profile_files()
    name_lower = name.lower().strip()

    # Load all profiles and match against their name field
    profiles: list[tuple[Path, JournalProfile]] = []
    for path in available:
        try:
            profiles.append((path, _load_from_file(path)))
        except Exception:
            continue

    # Exact match on profile name (case-insensitive)
    for path, profile in profiles:
        if profile.name.lower() == name_lower:
            return profile

    # Exact match on filename stem
    name_underscored = name_lower.replace(" ", "_").replace("-", "_")
    for path, profile in profiles:
        if path.stem == name_underscored:
            return profile

    # Fuzzy: check word overlap against profile name and filename
    name_words = set(name_lower.split())
    best_profile: JournalProfile | None = None
    best_score = 0
    for path, profile in profiles:
        profile_words = set(profile.name.lower().split())
        stem_words = set(path.stem.split("_"))
        overlap = max(len(name_words & profile_words), len(name_words & stem_words))
        if overlap > best_score:
            best_score = overlap
            best_profile = profile

    if best_profile and best_score >= 1:
        return best_profile

    return _DEFAULT_PROFILE


def list_available_journals() -> list[str]:
    """Return names of all available journal profiles."""
    profiles = []
    for path in _list_profile_files():
        try:
            profile = _load_from_file(path)
            profiles.append(profile.name)
        except Exception:
            profiles.append(path.stem)
    return sorted(profiles)


def _list_profile_files() -> list[Path]:
    """List all .json profile files in the journals directory."""
    if not _JOURNALS_DIR.exists():
        return []
    return sorted(_JOURNALS_DIR.glob("*.json"))


def _load_from_file(path: Path) -> JournalProfile:
    """Load a JournalProfile from a JSON file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return JournalProfile(**data)
