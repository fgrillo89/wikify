"""``notebook.md`` — researcher notebook + provenance for one concept.

Sits next to ``work.md`` and ``evidence.jsonl`` in
``<bundle>/work/concepts/<slug>/``. The frontmatter carries the maturity
snapshot, provenance (which docs and chunks the notebook has absorbed),
the exploration log, and the round history. The body is freeform prose
the explorer/editor edits as a working summary.

The ``wikify-investigate`` workflow writes here; the writer reads it
alongside ``evidence.jsonl`` when producing the final article.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from ...api import Bundle

_HEADER_DELIM = "---"
MAX_EXPLORATION_LOG = 8
MAX_ROUND_HISTORY = 5


class CoveredDoc(BaseModel):
    doc_id: str
    n_chunks: int = 0
    first_round: int = 0
    last_round: int = 0


class ExplorationLogEntry(BaseModel):
    round: int
    pattern: str
    target: str = ""
    depth: int = 0
    accepted: int = 0

    model_config = {"extra": "allow"}


class RoundHistoryEntry(BaseModel):
    round: int
    score: float = 0.0
    appended_chunks: int = 0

    model_config = {"extra": "allow"}


class MaturitySnapshot(BaseModel):
    score: float = 0.0
    band: str = "new"  # new | growing | stalled | ready | parked
    last_computed_round: int = 0
    kind_stencil: str = "article-method"
    gates_passed: bool = False

    model_config = {"extra": "allow"}


class Provenance(BaseModel):
    seed_docs: list[str] = Field(default_factory=list)
    covered_docs: list[CoveredDoc] = Field(default_factory=list)
    covered_chunks: list[str] = Field(default_factory=list)


class NotebookFront(BaseModel):
    """Parsed notebook frontmatter."""

    slug: str = ""
    kind: str = "article"
    maturity: MaturitySnapshot = Field(default_factory=MaturitySnapshot)
    provenance: Provenance = Field(default_factory=Provenance)
    exploration_log: list[ExplorationLogEntry] = Field(default_factory=list)
    round_history: list[RoundHistoryEntry] = Field(default_factory=list)
    new_doc_action_needed: bool = False

    model_config = {"extra": "allow"}


@dataclass
class Notebook:
    front: NotebookFront = field(default_factory=NotebookFront)
    body: str = ""

    @classmethod
    def parse(cls, text: str) -> Notebook:
        if not text.startswith(_HEADER_DELIM):
            return cls(front=NotebookFront(), body=text)
        lines = text.splitlines(keepends=True)
        end_idx = None
        for i, line in enumerate(lines[1:], start=1):
            if line.strip() == _HEADER_DELIM:
                end_idx = i
                break
        if end_idx is None:
            return cls(front=NotebookFront(), body=text)
        front_text = "".join(lines[1:end_idx])
        body = "".join(lines[end_idx + 1 :]).lstrip("\n")
        try:
            raw: Any = yaml.safe_load(front_text) or {}
            if not isinstance(raw, dict):
                raw = {}
            front = NotebookFront.model_validate(raw)
        except Exception:
            front = NotebookFront()
        return cls(front=front, body=body)

    def serialise(self) -> str:
        front_dict = self.front.model_dump(mode="json", exclude_none=False)
        front_text = yaml.safe_dump(
            front_dict, sort_keys=False, allow_unicode=True
        ).rstrip()
        body = self.body.rstrip() + "\n" if self.body else ""
        return f"{_HEADER_DELIM}\n{front_text}\n{_HEADER_DELIM}\n\n{body}"


def notebook_path(bundle: Bundle, slug: str) -> Path:
    return bundle.work_concept_dir(slug) / "notebook.md"


def read_notebook(bundle: Bundle, slug: str) -> Notebook:
    p = notebook_path(bundle, slug)
    if not p.exists():
        return Notebook(front=NotebookFront(slug=slug))
    return Notebook.parse(p.read_text(encoding="utf-8"))


def _atomic_write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".notebook-", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, p)
    except Exception:
        if Path(tmp).exists():
            Path(tmp).unlink()
        raise


def save_notebook(bundle: Bundle, slug: str, notebook: Notebook) -> Path:
    p = notebook_path(bundle, slug)
    _atomic_write(p, notebook.serialise())
    return p


def init_notebook(
    bundle: Bundle,
    *,
    slug: str,
    kind: str = "article",
    seed_docs: list[str] | None = None,
    kind_stencil: str | None = None,
) -> Notebook:
    """Create a fresh notebook skeleton. Idempotent — returns the existing
    notebook if one is already on disk.
    """
    p = notebook_path(bundle, slug)
    if p.exists():
        return read_notebook(bundle, slug)
    stencil = kind_stencil or ("person" if kind == "person" else "article-method")
    front = NotebookFront(
        slug=slug,
        kind=kind,
        maturity=MaturitySnapshot(kind_stencil=stencil),
        provenance=Provenance(seed_docs=list(seed_docs or [])),
    )
    body = (
        "## Working summary\n\n"
        "(explorer/editor fill this in as the notebook matures)\n\n"
        "## Open questions\n\n"
    )
    n = Notebook(front=front, body=body)
    save_notebook(bundle, slug, n)
    return n


def merge_covered_docs(
    existing: list[CoveredDoc], *, additions: dict[str, int], round_: int
) -> list[CoveredDoc]:
    """Fold ``{doc_id: n_chunks_added_this_round}`` into the covered_docs list."""
    by_doc = {d.doc_id: d for d in existing}
    for doc_id, n in additions.items():
        if doc_id in by_doc:
            d = by_doc[doc_id]
            d.n_chunks += n
            d.last_round = round_
        else:
            by_doc[doc_id] = CoveredDoc(
                doc_id=doc_id, n_chunks=n, first_round=round_, last_round=round_
            )
    return list(by_doc.values())


def merge_covered_chunks(existing: list[str], additions: list[str]) -> list[str]:
    """Union ``existing`` with ``additions``, preserving insertion order."""
    seen = set(existing)
    out = list(existing)
    for cid in additions:
        if cid in seen:
            continue
        seen.add(cid)
        out.append(cid)
    return out


def set_new_doc_action_needed(
    bundle: Bundle, slug: str, value: bool
) -> Notebook:
    """Flip ``new_doc_action_needed`` on a notebook. Materialises an
    empty notebook if one does not exist yet.
    """
    if not notebook_path(bundle, slug).exists():
        nb = init_notebook(bundle, slug=slug)
    else:
        nb = read_notebook(bundle, slug)
    nb.front.new_doc_action_needed = value
    save_notebook(bundle, slug, nb)
    return nb


def append_exploration_log(
    log: list[ExplorationLogEntry], entry: ExplorationLogEntry
) -> list[ExplorationLogEntry]:
    out = list(log) + [entry]
    return out[-MAX_EXPLORATION_LOG:]


def append_round_history(
    history: list[RoundHistoryEntry], entry: RoundHistoryEntry
) -> list[RoundHistoryEntry]:
    out = list(history) + [entry]
    return out[-MAX_ROUND_HISTORY:]


def list_notebook_slugs(bundle: Bundle) -> list[str]:
    """Return every slug that has a ``notebook.md`` on disk, sorted."""
    if not bundle.work_concepts_dir.is_dir():
        return []
    out = []
    for entry in sorted(bundle.work_concepts_dir.iterdir()):
        if entry.is_dir() and (entry / "notebook.md").is_file():
            out.append(entry.name)
    return out
