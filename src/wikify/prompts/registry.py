"""Immutable prompt registry for role templates.

All ``*.yaml`` files in this directory are loaded once at import time
into a frozen dict keyed by ``Prompt.name``. Bindings still receive
only the prompt id via ``ContextEnvelope``; the registry exists so the
Python side can look up the canonical template text from one place.

This module also exposes the **layered writer prompt** loaders:

- ``load_style_guide()`` -- the corpus-agnostic Academic Writing Style Guide
- ``load_field_guide(field_name)`` -- one of the field-specific guides
- ``load_artifact_template(artifact_name)`` -- one of the wiki-shaped
  artifact templates (``wiki_article`` or ``wiki_person``)
- ``compose_writer_prompt(...)`` -- assembles all four layers (style +
  field + artifact + persona) into one writer system message string

The layered loaders read from sibling directories ``fields/`` and
``artifact_types/``. The artifact templates are wiki-shaped.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

_PROMPTS_DIR = Path(__file__).resolve().parent
_STYLE_GUIDE_PATH = _PROMPTS_DIR / "style_guide.md"
_FIELDS_DIR = _PROMPTS_DIR / "fields"
_ARTIFACTS_DIR = _PROMPTS_DIR / "artifact_types"


@dataclass(frozen=True)
class Prompt:
    name: str
    role: str
    version: int
    input_schema: Any
    output_schema: Any
    prompt_template: str
    sampler_hint: str


def _load_all() -> MappingProxyType[str, Prompt]:
    items: dict[str, Prompt] = {}
    for path in sorted(_PROMPTS_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"prompt file {path} did not parse to a mapping")
        for key in ("name", "role", "version", "prompt_template"):
            if key not in data:
                raise ValueError(f"prompt file {path} missing required field: {key}")
        p = Prompt(
            name=str(data["name"]),
            role=str(data["role"]),
            version=int(data["version"]),
            input_schema=data.get("input_schema"),
            output_schema=data.get("output_schema"),
            prompt_template=str(data["prompt_template"]),
            sampler_hint=str(data.get("sampler_hint", "haiku")),
        )
        if not p.prompt_template.strip():
            raise ValueError(f"prompt file {path} has empty prompt_template")
        items[p.name] = p
    return MappingProxyType(items)


_REGISTRY: MappingProxyType[str, Prompt] = _load_all()


def load_prompt(name: str) -> Prompt:
    """Return the frozen ``Prompt`` for ``name`` or raise ``KeyError``."""
    try:
        return _REGISTRY[name]
    except KeyError as e:
        raise KeyError(f"no prompt registered with name {name!r}") from e


def all_prompts() -> MappingProxyType[str, Prompt]:
    return _REGISTRY


# --- layered writer-prompt loaders --------------------------------------


def load_style_guide() -> str:
    """Return the corpus-agnostic style guide text."""
    if not _STYLE_GUIDE_PATH.exists():
        raise FileNotFoundError(f"style guide missing: {_STYLE_GUIDE_PATH}")
    return _STYLE_GUIDE_PATH.read_text(encoding="utf-8")


def available_field_guides() -> tuple[str, ...]:
    return tuple(sorted(p.stem for p in _FIELDS_DIR.glob("*.md")))


def load_field_guide(field_name: str) -> str:
    """Return the field-specific writing guide text.

    Raises ``KeyError`` if ``field_name`` does not match a file in the
    ``fields/`` directory. There is no silent fallback; the caller must
    pick from ``available_field_guides()``.
    """
    path = _FIELDS_DIR / f"{field_name}.md"
    if not path.exists():
        raise KeyError(f"unknown field guide {field_name!r}; available: {available_field_guides()}")
    return path.read_text(encoding="utf-8")


def available_artifact_templates() -> tuple[str, ...]:
    return tuple(sorted(p.stem for p in _ARTIFACTS_DIR.glob("*.md")))


def load_artifact_template(artifact_name: str) -> str:
    """Return the artifact template text for the given artifact name.

    Raises ``KeyError`` if no matching file exists. wikify ships
    two templates: ``wiki_article`` and ``wiki_person``.
    """
    path = _ARTIFACTS_DIR / f"{artifact_name}.md"
    if not path.exists():
        raise KeyError(
            f"unknown artifact template {artifact_name!r}; "
            f"available: {available_artifact_templates()}"
        )
    return path.read_text(encoding="utf-8")


_GENERIC_PERSONA = (
    "You are a senior domain expert writing Wikipedia-style encyclopedia "
    "articles for a curated knowledge base. You write neutral, declarative "
    "prose grounded entirely in the supplied evidence list. You never "
    "invent claims, never reveal how the evidence was retrieved, and never "
    "describe the document you are writing in meta terms."
)


def _content_hash(text: str) -> str:
    """Return first 16 hex chars of sha256 of the utf-8 encoded text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def compose_writer_prompt_layer_hashes(field: str, artifact: str) -> dict[str, str]:
    """Return content-based hashes for each stable prompt layer.

    Keys: ``style_guide``, ``field_guide``, ``artifact_template``.
    Values: first 16 hex chars of sha256 of the layer text.

    The persona layer is NOT hashed here because it is corpus-specific
    and loaded separately by the pipeline. Callers should hash the persona
    text directly with :func:`_content_hash` if needed.
    """
    return {
        "style_guide": _content_hash(load_style_guide()),
        "field_guide": _content_hash(load_field_guide(field)),
        "artifact_template": _content_hash(load_artifact_template(artifact)),
    }


def compose_writer_prompt(
    *,
    style: str,
    field: str,
    artifact: str,
    persona: str | None,
    page_kind: str,
) -> str:
    """Assemble the four-layer writer system message.

    The order is fixed: persona -> style guide -> field guide -> artifact
    template -> a short composer footer naming the page kind. Callers
    pass already-loaded strings; this function does no I/O so it can be
    unit-tested without a filesystem.
    """
    persona_text = (persona or "").strip() or _GENERIC_PERSONA
    parts = [
        "# Author Persona",
        persona_text,
        "",
        "# Academic Writing Style Guide",
        style.strip(),
        "",
        "# Field-Specific Writing Guide",
        field.strip(),
        "",
        "# Output Template",
        artifact.strip(),
        "",
        "# Composition",
        (
            f'You are writing a `kind="{page_kind}"` page for the '
            "wikify wiki. Follow the style guide, field guide, and "
            "output template above. Stay grounded in the supplied evidence "
            "list. Respond with strict JSON matching the writer output "
            "schema. No commentary outside the JSON."
        ),
    ]
    return "\n".join(parts)
