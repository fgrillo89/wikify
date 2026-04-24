"""Smoke tests for .claude/skills/wikify/ skill-pack integrity.

Scope:
- Frontmatter parses as YAML with required fields.
- Every reference/<name>.md path mentioned in a skill body resolves on disk.
- Every src/wikify/<path>.py::<symbol> or wikify.<dotted> reference in a
  skill body resolves to a real importable module (and symbol, when named).
- The Phase 0 reference set exists.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest
import yaml

SKILLS_ROOT = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "wikify"
REPO_ROOT = Path(__file__).resolve().parents[2]

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
REFERENCE_PATH_RE = re.compile(
    r"\.claude/skills/wikify/reference/([a-z0-9\-]+)\.md"
    r"|(?:^|\s)reference/([a-z0-9\-]+)\.md",
    re.MULTILINE,
)

# Matches `src/wikify/<path>.py` and optional `::symbol` suffix.
# Used to extract Python symbols that skills claim exist.
PYTHON_PATH_RE = re.compile(
    r"src/wikify/([a-z0-9_/]+)\.py(?:::([A-Za-z_][A-Za-z0-9_]*))?"
)

# Free-form `wikify.<dotted>` references, e.g. wikify.baselines.pipeline.BaselineConfig.
# Only rooted at `wikify.` so we don't sweep up plain English words.
WIKIFY_DOTTED_RE = re.compile(r"\bwikify\.[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)+")


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


def _python_references() -> list[tuple[Path, str, str | None]]:
    """Collect every (skill, module_path, optional_symbol) referenced by any skill."""
    refs: list[tuple[Path, str, str | None]] = []
    for skill_path in _all_skill_files():
        body = skill_path.read_text(encoding="utf-8")
        for match in PYTHON_PATH_RE.finditer(body):
            raw_path, symbol = match.group(1), match.group(2)
            module = "wikify." + raw_path.replace("/", ".")
            refs.append((skill_path.relative_to(SKILLS_ROOT), module, symbol))
        for match in WIKIFY_DOTTED_RE.finditer(body):
            dotted = match.group(0)
            # Skip examples inside code blocks that reference nonexistent
            # chains like `wikify.session.session.json` — the last segment
            # is a file, not a symbol. We require at least 2 dots total
            # (wikify.X.Y) and resolve as module first, then symbol.
            parts = dotted.split(".")
            if len(parts) < 3:
                continue
            # Heuristic: if last segment looks file-suffix-y ("json", "md",
            # "py"), skip.
            if parts[-1] in {"json", "md", "py", "jsonl", "npz", "lock"}:
                continue
            # Try to split into module + symbol: last segment CamelCase or
            # ALL_CAPS => symbol; else treat whole thing as module.
            last = parts[-1]
            if last and (last[0].isupper() or last.isupper()):
                module = ".".join(parts[:-1])
                symbol = last
            else:
                module = ".".join(parts)
                symbol = None
            refs.append((skill_path.relative_to(SKILLS_ROOT), module, symbol))
    return refs


def test_referenced_python_symbols_import() -> None:
    """Every `src/wikify/<path>.py::<symbol>` and `wikify.<dotted>` reference
    in skills must resolve: module importable, symbol present if named.
    """
    missing: list[str] = []
    for skill_rel, module, symbol in _python_references():
        try:
            mod = importlib.import_module(module)
        except Exception as exc:  # pragma: no cover - exact type varies
            missing.append(f"{skill_rel}: module {module!r} not importable ({exc})")
            continue
        if symbol is not None and not hasattr(mod, symbol):
            missing.append(f"{skill_rel}: {module}.{symbol} does not exist")
    assert not missing, "Skills reference missing Python symbols:\n  " + "\n  ".join(missing)
