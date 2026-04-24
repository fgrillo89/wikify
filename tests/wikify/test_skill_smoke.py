"""Smoke tests for .claude/skills/wikify/ skill-pack integrity.

Scope:
- Frontmatter parses as YAML with required fields.
- Every reference/<name>.md path mentioned in a skill body resolves on disk.

The Python-symbol-import check is added in Commit 3 once the new wikify.session
and wikify.cli_cmds modules exist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

SKILLS_ROOT = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "wikify"

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
REFERENCE_PATH_RE = re.compile(
    r"\.claude/skills/wikify/reference/([a-z0-9\-]+)\.md"
    r"|(?:^|\s)reference/([a-z0-9\-]+)\.md",
    re.MULTILINE,
)


def _all_skill_files() -> list[Path]:
    return sorted(SKILLS_ROOT.rglob("*.md"))


@pytest.mark.parametrize(
    "skill_path",
    _all_skill_files(),
    ids=lambda p: str(p.relative_to(SKILLS_ROOT)),
)
def test_frontmatter_parses_as_yaml(skill_path: Path) -> None:
    text = skill_path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    assert match is not None, f"{skill_path}: missing frontmatter"
    meta = yaml.safe_load(match.group(1))
    assert isinstance(meta, dict), f"{skill_path}: frontmatter did not parse to a mapping"
    name = meta.get("name")
    assert isinstance(name, str) and name, f"{skill_path}: missing non-empty 'name'"
    description = meta.get("description")
    assert isinstance(description, str) and description, (
        f"{skill_path}: missing non-empty 'description'"
    )


def test_referenced_reference_files_exist() -> None:
    reference_dir = SKILLS_ROOT / "reference"
    assert reference_dir.is_dir(), f"missing reference directory: {reference_dir}"

    missing: list[tuple[Path, str]] = []
    for skill_path in _all_skill_files():
        body = skill_path.read_text(encoding="utf-8")
        for m in REFERENCE_PATH_RE.finditer(body):
            name = m.group(1) or m.group(2)
            target = reference_dir / f"{name}.md"
            if not target.is_file():
                missing.append((skill_path.relative_to(SKILLS_ROOT), name))
    assert not missing, "Skills reference missing files: " + ", ".join(
        f"{s} -> reference/{n}.md" for s, n in missing
    )


def test_expected_reference_files_present() -> None:
    """Lock in the Phase 0 reference consolidation set."""
    reference_dir = SKILLS_ROOT / "reference"
    expected = {
        "schemas.md",
        "cli-tool-surface.md",
        "write-constraints.md",
        "citation-format.md",
        "tiers.md",
        "escalation.md",
        "atoms.md",
    }
    present = {p.name for p in reference_dir.glob("*.md")}
    missing = expected - present
    assert not missing, f"Phase 0 reference files missing: {sorted(missing)}"
