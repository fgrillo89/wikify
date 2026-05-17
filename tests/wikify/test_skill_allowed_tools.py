"""Skill-layout regression test.

Every Bash command in a skill's fenced ```bash blocks must be invokable
under the skill's `allowed-tools` frontmatter. Catches the Codex finding
where wikify-gather-evidence used `cat <<EOF | wikify ...` but only
declared `Bash(wikify *)` — the `cat` entry-point falls outside the
allowlist and the workflow's commit step would be blocked under
canonical Claude Code permissions.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Shell tokens that aren't external commands — they're keywords / control
# flow / heredoc delimiters. Skip them when checking the first word of a
# command line.
_SHELL_NON_COMMANDS: frozenset[str] = frozenset({
    # control flow
    "if", "then", "else", "elif", "fi",
    "for", "while", "until", "do", "done",
    "case", "esac",
    "function",
    "{", "}",
    # shell builtins (no external process spawned — safe regardless of allow-list)
    "echo", "export", "set", "unset", "read", "shift", "return", "exit",
    "true", "false", "test", ":",
    "alias", "unalias", "source", ".",
    "pushd", "popd", "dirs",
    "cd", "pwd",
    # heredoc body delimiters appearing on their own line
    "EOF", "JSON", "END", "DONE", "STOP",
})

# Skill frontmatter delimiter.
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<fm>.*?)\n---\s*\n", re.DOTALL)

# ```bash ... ``` fenced code blocks. Match the OPENING fence as ```bash
# or ```sh (some skills may use either) on its own line, then capture
# everything up to the closing ``` on its own line.
_BASH_BLOCK_RE = re.compile(
    r"^```(?:bash|sh|shell)\s*\n(?P<body>.*?)^```",
    re.DOTALL | re.MULTILINE,
)

# Parse `allowed-tools: Bash(wikify *)` (string form) or
# `allowed-tools: ['Bash(wikify *)', 'Bash(rg *)']` (list form, YAML
# inline). Simple parser — the frontmatter is YAML-ish but we only need
# the allowed-tools key.
_ALLOWED_RE = re.compile(
    r"^allowed-tools\s*:\s*(?P<value>.+?)$",
    re.MULTILINE,
)
_BASH_PATTERN_RE = re.compile(r"Bash\(\s*([A-Za-z0-9_:./*-]+)(?:\s+[^)]*)?\)")


def _parse_allowed_tools(frontmatter: str) -> set[str] | None:
    """Return the set of bash entry-points allowed by this skill.

    Returns:
        ``None`` when no ``allowed-tools`` field is present (no
        restriction declared). Otherwise the set of allowed binary
        names. The wildcard sentinel ``"*"`` means "anything goes".
    """
    m = _ALLOWED_RE.search(frontmatter)
    if not m:
        return None
    raw = m.group("value").strip()
    if raw in {"*", "['*']", '["*"]'}:
        return {"*"}
    entries = _BASH_PATTERN_RE.findall(raw)
    if not entries:
        return None
    out: set[str] = set()
    for entry in entries:
        if entry == "*":
            out.add("*")
        else:
            # "wikify" from "Bash(wikify *)"; "git" from "Bash(git status)"
            out.add(entry)
    return out


def _command_entry_points(block_body: str) -> list[str]:
    """Extract the first word of each executable command inside a bash block.

    Algorithm:
    1. Merge backslash-continuation lines into logical commands first.
    2. Strip out comment lines and blank lines.
    3. For each logical command, detect heredoc body and skip those lines.
    4. Extract the first external-command token (skipping shell builtins,
       control flow, env-var prefixes, JSON / array tokens).
    """
    # Step 1 + 2: merge continuations, drop blanks / comments.
    logical_lines: list[str] = []
    buffer: list[str] = []
    for raw_line in block_body.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if not stripped and not buffer:
            continue
        # Trailing backslash → continuation. Strip the slash, keep the
        # rest, and continue accumulating.
        if line.endswith("\\"):
            buffer.append(line[:-1].rstrip())
            continue
        # End of a logical line.
        if buffer:
            buffer.append(line)
            logical_lines.append(" ".join(buffer).strip())
            buffer = []
        else:
            logical_lines.append(stripped)
    if buffer:
        logical_lines.append(" ".join(buffer).strip())

    # Step 3 + 4: walk logical lines, handle heredocs, extract first
    # external command token per line.
    entry_points: list[str] = []
    in_heredoc = False
    heredoc_delim: str | None = None
    for logical in logical_lines:
        if in_heredoc:
            if logical.strip() == heredoc_delim:
                in_heredoc = False
                heredoc_delim = None
            continue
        if not logical:
            continue
        # JSON / array / object content that ended up in the block body
        # outside a recognized heredoc (e.g. dangling examples). Skip.
        if logical[0] in "{[]}":
            continue
        # Detect heredoc start: `... <<'EOF'` or `... <<EOF` at end of line.
        heredoc_match = re.search(r"<<-?\s*['\"]?(\w+)['\"]?\s*$", logical)
        first_word = logical.split()[0]
        # Env-var prefix `VAR=val cmd args` → take the cmd token.
        if "=" in first_word and not first_word.startswith("="):
            parts = logical.split()
            if len(parts) > 1:
                first_word = parts[1]
            else:
                # Pure assignment, no command — skip.
                if heredoc_match:
                    in_heredoc = True
                    heredoc_delim = heredoc_match.group(1)
                continue
        if first_word in _SHELL_NON_COMMANDS:
            if heredoc_match:
                in_heredoc = True
                heredoc_delim = heredoc_match.group(1)
            continue
        if first_word in {"[", "]", "{", "}", "(", ")"}:
            if heredoc_match:
                in_heredoc = True
                heredoc_delim = heredoc_match.group(1)
            continue
        entry_points.append(first_word)
        if heredoc_match:
            in_heredoc = True
            heredoc_delim = heredoc_match.group(1)
    return entry_points


def _violations_for_skill(skill_path: Path) -> list[str]:
    """Return a list of human-readable violations, or [] if clean."""
    text = skill_path.read_text(encoding="utf-8")
    fm_match = _FRONTMATTER_RE.match(text)
    if not fm_match:
        return []  # no frontmatter — no allowed-tools restriction declared
    allowed = _parse_allowed_tools(fm_match.group("fm"))
    if allowed is None:
        return []  # no allowed-tools field — no restriction declared
    if "*" in allowed:
        return []  # wildcard — anything permitted
    body = text[fm_match.end() :]
    violations: list[str] = []
    for block_match in _BASH_BLOCK_RE.finditer(body):
        block_body = block_match.group("body")
        entry_points = _command_entry_points(block_body)
        for ep in entry_points:
            if ep not in allowed:
                violations.append(
                    f"{skill_path.name}: bash block uses {ep!r} but "
                    f"allowed-tools only permits {sorted(allowed)}"
                )
    return violations


# Discover skill files under the repo's .claude/skills/ tree. Restricted
# to the wikify-* skills since those are the ones this branch owns; other
# skills (codex:*, update-config, etc.) are out of scope for this test.
def _wikify_skill_paths() -> list[Path]:
    skills_root = Path(__file__).resolve().parents[2] / ".claude" / "skills"
    if not skills_root.is_dir():
        return []
    return sorted(skills_root.glob("wikify*/SKILL.md"))


@pytest.mark.parametrize("skill_path", _wikify_skill_paths(), ids=lambda p: p.parent.name)
def test_skill_bash_commands_match_allowed_tools(skill_path: Path) -> None:
    violations = _violations_for_skill(skill_path)
    assert not violations, "\n".join(violations)


def test_skill_layout_helper_catches_cat_pipe_violation(tmp_path: Path) -> None:
    """Sanity check the linter itself: a skill with cat | wikify but
    allowed-tools restricted to wikify must produce a violation."""
    skill = tmp_path / "SKILL.md"
    skill.write_text(
        "---\n"
        "name: test-skill\n"
        "allowed-tools: Bash(wikify *)\n"
        "---\n"
        "\n"
        "```bash\n"
        "cat <<'EOF' | wikify work foo\n"
        "payload\n"
        "EOF\n"
        "```\n",
        encoding="utf-8",
    )
    violations = _violations_for_skill(skill)
    assert any("'cat'" in v for v in violations), violations


def test_skill_layout_helper_passes_heredoc_redirection(tmp_path: Path) -> None:
    """A wikify command with `<<'EOF'` redirection but no cat-pipe stays
    inside the wikify allowlist."""
    skill = tmp_path / "SKILL.md"
    skill.write_text(
        "---\n"
        "name: test-skill\n"
        "allowed-tools: Bash(wikify *)\n"
        "---\n"
        "\n"
        "```bash\n"
        "wikify work build-evidence slug --from-ids @- <<'EOF'\n"
        '[{"chunk_id": "x"}]\n'
        "EOF\n"
        "```\n",
        encoding="utf-8",
    )
    assert _violations_for_skill(skill) == []
