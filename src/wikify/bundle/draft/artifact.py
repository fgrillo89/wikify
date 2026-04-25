"""Per-attempt artifact IO — draft.json / response.json / validation.json.

Each concept folder may carry up to three transient files at any
moment::

    work/concepts/<slug>/
      draft.json        — WriteRequest payload (assembled by DraftBuilder)
      response.json     — WriteResponse from the writer subagent
      validation.json   — verdict from Validator

After ``wiki commit`` succeeds these are eligible for garbage
collection. This module owns reading/writing them as raw dicts so
both the builder and validator can hand them around without
re-parsing the on-disk shape.
"""

from __future__ import annotations

import json
from pathlib import Path

from ...api import Bundle


def draft_path(bundle: Bundle, slug: str) -> Path:
    return bundle.work_concept_dir(slug) / "draft.json"


def response_path(bundle: Bundle, slug: str) -> Path:
    return bundle.work_concept_dir(slug) / "response.json"


def validation_path(bundle: Bundle, slug: str) -> Path:
    return bundle.work_concept_dir(slug) / "validation.json"


def write_json(path: Path, payload: dict) -> None:
    """Write a JSON file. Parent directories are created on demand."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def gc_attempt(bundle: Bundle, slug: str) -> int:
    """Delete draft/response/validation for *slug*. Returns count removed."""
    n = 0
    for p in (
        draft_path(bundle, slug),
        response_path(bundle, slug),
        validation_path(bundle, slug),
    ):
        if p.exists():
            p.unlink()
            n += 1
    return n
