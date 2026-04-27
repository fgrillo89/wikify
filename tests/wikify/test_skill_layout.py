"""Asserts the .claude/skills/ canonical layout.

Every skill dir must have a SKILL.md with required frontmatter, no
references to retired commands, and no excessive size.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / ".claude" / "skills"
WIKIFY_REFERENCES = SKILLS_ROOT / "wikify" / "references"

CORE_SKILLS = (
    "wikify-search-corpus",
    "wikify-search-wiki",
    "wikify-write-page",
    "wikify-bundle",
)

# Forbidden substrings in any SKILL.md body. Each entry is a literal that
# must not appear anywhere in the rendered markdown — we use word-boundary-ish
# regex so e.g. "wiki sessions" never false-positives but "wikify session" does.
RETIRED_PATTERNS = (
    r"wikify\s+session\b",
    r"wikify\s+kg\b",
    r"wikify\s+meter\b",
    r"wikify\s+html\b",
    r"wikify\s+extract\b",
    r"wikify\s+validate\b",
    r"wikify\s+bundle\b",
    r"_session\b",
    r"_scratch\b",
    r"_calls\.jsonl\b",
    r"_run\.json\b",
    r"BaselineConfig\b",
    r"run_baseline\b",
)


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Tiny stdlib-only YAML-frontmatter scanner. Returns top-level scalar fields."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        # Only top-level `key: value` pairs (no nested mappings, no lists).
        m = re.match(r"^([A-Za-z][A-Za-z0-9_-]*)\s*:\s*(.*)$", line)
        if m:
            key, value = m.group(1), m.group(2).strip()
            out[key] = value
    return out


def _iter_skill_dirs() -> list[Path]:
    return sorted(p for p in SKILLS_ROOT.iterdir() if p.is_dir())


def test_old_singular_reference_dir_is_gone() -> None:
    assert not (SKILLS_ROOT / "wikify" / "reference").exists(), (
        "old singular reference/ directory must be removed; use references/"
    )


def test_old_workflows_dir_is_gone() -> None:
    assert not (SKILLS_ROOT / "wikify" / "workflows").exists(), (
        "old workflows/ directory must be removed; baseline lives under "
        "wikify-baseline/SKILL.md"
    )


def test_references_dir_present() -> None:
    assert WIKIFY_REFERENCES.is_dir(), "wikify/references/ must exist"
    # Spot-check canonical reference groups.
    for name in (
        "bundle/layout.md",
        "bundle/state.md",
        "cli/grammar.md",
        "cli/output-contract.md",
        "writing/schemas.md",
        "writing/citation-format.md",
        "writing/field-guides/generic.md",
        "exploration/concept-extraction.md",
        "exploration/sampling-patterns.md",
    ):
        assert (WIKIFY_REFERENCES / name).is_file(), f"missing reference {name}"


def test_every_skill_has_skill_md_with_matching_name() -> None:
    skill_dirs = _iter_skill_dirs()
    assert skill_dirs, "no skills found under .claude/skills/"
    for skill_dir in skill_dirs:
        skill_md = skill_dir / "SKILL.md"
        assert skill_md.is_file(), f"{skill_dir.name}/SKILL.md missing"
        front = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        assert "name" in front, f"{skill_dir.name}/SKILL.md missing name in frontmatter"
        assert front["name"] == skill_dir.name, (
            f"{skill_dir.name}/SKILL.md frontmatter name {front['name']!r} "
            f"does not match directory name {skill_dir.name!r}"
        )
        assert "description" in front, (
            f"{skill_dir.name}/SKILL.md missing description in frontmatter"
        )


def test_no_retired_command_strings_in_any_skill() -> None:
    skill_dirs = _iter_skill_dirs()
    offenders: list[str] = []
    for skill_dir in skill_dirs:
        skill_md = skill_dir / "SKILL.md"
        text = skill_md.read_text(encoding="utf-8")
        for pat in RETIRED_PATTERNS:
            if re.search(pat, text):
                offenders.append(f"{skill_dir.name}/SKILL.md matched {pat!r}")
    # Also scan references recursively.
    for ref in WIKIFY_REFERENCES.rglob("*.md"):
        text = ref.read_text(encoding="utf-8")
        for pat in RETIRED_PATTERNS:
            if re.search(pat, text):
                rel = ref.relative_to(WIKIFY_REFERENCES)
                offenders.append(f"references/{rel} matched {pat!r}")
    assert not offenders, "retired surface leaked: " + "; ".join(offenders)


def test_core_skills_stay_small() -> None:
    """Core capability skills should remain concise and strategy-free."""
    for name in CORE_SKILLS:
        skill_md = SKILLS_ROOT / name / "SKILL.md"
        assert skill_md.is_file(), f"core skill {name} missing"
        n_lines = len(skill_md.read_text(encoding="utf-8").splitlines())
        assert n_lines <= 200, (
            f"core skill {name}/SKILL.md is {n_lines} lines (> 200 ceiling)"
        )


def test_old_cli_noun_skills_are_not_discoverable() -> None:
    """CLI nouns are references now, not independently discoverable skills."""
    retired = (
        "wikify-corpus",
        "wikify-run",
        "wikify-work",
        "wikify-draft",
        "wikify-wiki",
        "wikify-render",
        "wikify-eval",
    )
    offenders = [
        name for name in retired if (SKILLS_ROOT / name / "SKILL.md").exists()
    ]
    assert not offenders, "retired CLI noun skills still discoverable: " + ", ".join(
        offenders
    )


def test_baseline_workflow_stays_reasonable() -> None:
    """Baseline workflow may be larger than atomics but stays under ~250."""
    skill_md = SKILLS_ROOT / "wikify-baseline" / "SKILL.md"
    assert skill_md.is_file(), "wikify-baseline/SKILL.md missing"
    n_lines = len(skill_md.read_text(encoding="utf-8").splitlines())
    assert n_lines <= 250, (
        f"wikify-baseline/SKILL.md is {n_lines} lines (> 250 ceiling)"
    )


def test_umbrella_lists_only_existing_workflows() -> None:
    """Every workflow named in the umbrella SKILL.md must have a matching dir.

    The umbrella's "When to use which workflow" section enumerates the
    available workflow skills. If it lists `wikify-foo`, then
    `.claude/skills/wikify-foo/SKILL.md` must exist — otherwise an agent
    will follow a dangling pointer.
    """
    umbrella = SKILLS_ROOT / "wikify" / "SKILL.md"
    assert umbrella.is_file(), "umbrella wikify/SKILL.md missing"
    text = umbrella.read_text(encoding="utf-8")
    # Match the leading bullet form: `- \`wikify-<name>\`` at line start.
    bullet_re = re.compile(r"(?m)^-\s+`(wikify-[a-z][a-z0-9-]*)`")
    referenced = set(bullet_re.findall(text))
    missing = sorted(
        name for name in referenced if not (SKILLS_ROOT / name / "SKILL.md").is_file()
    )
    assert not missing, (
        "umbrella SKILL.md references workflows that have no skill dir: "
        + ", ".join(missing)
    )


def test_every_reference_link_resolves() -> None:
    """Every `[link](references/foo.md)` in a SKILL.md must resolve.

    Skill subdirs reach the shared references via `../wikify/references/`.
    The umbrella skill reaches them via `references/`. Anything else is a
    typo or stale link.
    """
    link_re = re.compile(r"\]\((?P<target>[^)\s]+\.md)\)")
    skill_dirs = _iter_skill_dirs()
    missing: list[str] = []
    for skill_dir in skill_dirs:
        skill_md = skill_dir / "SKILL.md"
        text = skill_md.read_text(encoding="utf-8")
        for m in link_re.finditer(text):
            target = m.group("target")
            if "references/" not in target:
                continue
            # Resolve relative to the SKILL.md itself.
            resolved = (skill_md.parent / target).resolve()
            if not resolved.is_file():
                missing.append(f"{skill_dir.name}/SKILL.md -> {target} (not found)")
            # Must land somewhere under wikify/references/.
            try:
                resolved.relative_to(WIKIFY_REFERENCES.resolve())
            except ValueError:
                missing.append(
                    f"{skill_dir.name}/SKILL.md -> {target} resolves outside "
                    f"wikify/references/"
                )
    assert not missing, "broken reference links: " + "; ".join(missing)
