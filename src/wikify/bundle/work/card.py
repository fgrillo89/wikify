"""``work.md`` ControlCard — concept-folder working memory.

One ``work.md`` per ``<bundle>/work/concepts/<slug>/`` directory. The
file is markdown with YAML frontmatter; the frontmatter is the
small mutable header the agent and Python both write to, and the body
is freeform prose maintained by the writer/refiner subagent.

This module owns the parser/serialiser. It does not interpret the
body — that is the agent's job. It does interpret the frontmatter
(known fields are typed; unknown fields are passed through).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ...api import Bundle

_HEADER_DELIM = "---"


def slugify(title: str) -> str:
    """Cheap slug: lowercase, alphanumerics → hyphens. ALD page-naming
    helpers already exist for the wiki side; the work side uses a
    simpler form because slugs here are concept-folder names, not
    canonical page ids.
    """
    out = []
    prev_dash = False
    for ch in title.lower():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif not prev_dash:
            out.append("-")
            prev_dash = True
    return "".join(out).strip("-") or "untitled"


@dataclass
class WorkCard:
    """Parsed view of a ``work.md`` file."""

    front: dict[str, Any] = field(default_factory=dict)
    body: str = ""

    # ----- accessors for known frontmatter fields ---------------------

    @property
    def page_id(self) -> str:
        return str(self.front.get("page_id", ""))

    @property
    def kind(self) -> str:
        return str(self.front.get("kind", "article"))

    @property
    def status(self) -> str:
        return str(self.front.get("status", "active"))

    @property
    def aliases(self) -> list[str]:
        return list(self.front.get("aliases") or [])

    @property
    def needs_refine(self) -> bool:
        return bool(self.front.get("needs_refine", False))

    # ----- IO --------------------------------------------------------

    @classmethod
    def parse(cls, text: str) -> WorkCard:
        """Parse a ``work.md`` text into frontmatter + body."""
        if not text.startswith(_HEADER_DELIM):
            return cls(front={}, body=text)
        # Find the closing delimiter.
        lines = text.splitlines(keepends=True)
        end_idx = None
        for i, line in enumerate(lines[1:], start=1):
            if line.strip() == _HEADER_DELIM:
                end_idx = i
                break
        if end_idx is None:
            return cls(front={}, body=text)
        front_text = "".join(lines[1:end_idx])
        body = "".join(lines[end_idx + 1 :])
        try:
            front = yaml.safe_load(front_text) or {}
            if not isinstance(front, dict):
                front = {}
        except yaml.YAMLError:
            front = {}
        return cls(front=front, body=body.lstrip("\n"))

    def serialise(self) -> str:
        """Render the card back to ``work.md`` text."""
        front_text = yaml.safe_dump(
            self.front, sort_keys=False, allow_unicode=True
        ).rstrip()
        body = self.body.rstrip() + "\n" if self.body else ""
        return f"{_HEADER_DELIM}\n{front_text}\n{_HEADER_DELIM}\n\n{body}"


# ----- bundle-relative IO --------------------------------------------


def card_path(bundle: Bundle, slug: str) -> Path:
    return bundle.work_concept_dir(slug) / "work.md"


def load_card(bundle: Bundle, slug: str) -> WorkCard:
    """Load ``<bundle>/work/concepts/<slug>/work.md`` or return an empty card."""
    p = card_path(bundle, slug)
    if not p.exists():
        return WorkCard()
    return WorkCard.parse(p.read_text(encoding="utf-8"))


def save_card(bundle: Bundle, slug: str, card: WorkCard) -> Path:
    """Write the card. Creates the concept directory if needed."""
    p = card_path(bundle, slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(card.serialise(), encoding="utf-8")
    return p


def create_concept(
    bundle: Bundle,
    *,
    page_id: str,
    kind: str = "article",
    aliases: list[str] | None = None,
    slug: str | None = None,
    seed_doc_handles: list[str] | None = None,
) -> tuple[str, WorkCard]:
    """Create a fresh concept folder and return ``(slug, card)``.

    Idempotent on slug: calling twice with the same title returns the
    same slug; the existing card is overwritten only when the caller
    explicitly opts in by passing a fresh ``slug``.

    ``seed_doc_handles`` carries the extractor's high-precision evidence
    hint — corpus doc handles the extractor saw and judged relevant. The
    evidence-builder uses these as a prior, then tops up via corpus find
    to reach the quota.
    """
    s = slug or slugify(page_id)
    existing = load_card(bundle, s)
    if existing.page_id == page_id:
        return s, existing
    front: dict = {
        "page_id": page_id,
        "kind": kind,
        "status": "active",
        "aliases": list(aliases or []),
        "evidence_chunks": 0,
        "evidence_docs": 0,
        "needs_refine": False,
    }
    if seed_doc_handles:
        front["seed_doc_handles"] = list(seed_doc_handles)
    card = WorkCard(
        front=front,
        body="",
    )
    save_card(bundle, s, card)
    return s, card


def list_concept_slugs(bundle: Bundle) -> list[str]:
    """Return every slug that has a ``work.md`` on disk, sorted."""
    if not bundle.work_concepts_dir.is_dir():
        return []
    out = []
    for entry in sorted(bundle.work_concepts_dir.iterdir()):
        if entry.is_dir() and (entry / "work.md").is_file():
            out.append(entry.name)
    return out
