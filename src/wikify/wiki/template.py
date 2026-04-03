"""Extraction template management for the Discovery Engine pipeline.

The extraction template defines what knowledge to extract from each chunk.
It evolves per epoch as gap feedback accumulates.

Functions:
    load_template()              -- read current template from disk
    save_template()              -- write template with versioned backups
    get_default_template()       -- return the initial v0 template string
    build_extraction_prompt()    -- build LLM messages from template + chunk
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_TEMPLATE_FILENAME = "_template.md"
_VERSIONS_DIR = "_template_versions"


def get_default_template() -> str:
    """Return the initial extraction template (v0) as a string."""
    return """\
# Extraction Template v0

Extract the following from the text below. Return as JSON with these sections.

## concepts
Array of objects: {name, type, aliases, definition, evidence}
- type: one of technique | material | phenomenon | method | theory | dataset
- evidence: exact quote from the text (max 50 words) supporting this concept
- definition: max 25 words

## parameters
Array of objects: {concept_name, parameter_name, value, unit, conditions, evidence}
- Only extract explicitly stated quantitative values with units
- conditions: experimental conditions (temperature, pressure, etc.)
- evidence: exact quote containing the value

## mechanisms
Array of objects: {description, causes, effects, evidence}
- Causal or process mechanisms described in the text
- evidence: exact quote

## relationships
Array of objects: {source_concept, target_concept, relation_type, evidence}
- relation_type: IS-A | PART-OF | USED-IN | ENABLES | CONTRASTS-WITH
- Only extract relationships explicitly stated in the text

## gaps
Array of objects: {description, suggested_type}
- Knowledge in this text that does NOT fit the categories above
- suggested_type: what new category would capture it
"""


def load_template(wiki_dir: Path) -> str:
    """Read the current extraction template from disk.

    Falls back to the default template if the file doesn't exist.

    Args:
        wiki_dir: Root wiki directory (e.g. data/wiki/).

    Returns:
        Template content string.
    """
    template_path = wiki_dir / _TEMPLATE_FILENAME
    if template_path.exists():
        return template_path.read_text(encoding="utf-8")
    logger.info("load_template: no template at %s, using default", template_path)
    return get_default_template()


def save_template(wiki_dir: Path, content: str, epoch: int) -> None:
    """Write the template to disk with a versioned backup.

    Backups are stored in wiki_dir/_template_versions/template_epoch_N.md.

    Args:
        wiki_dir: Root wiki directory.
        content: New template content.
        epoch: Current epoch number (used for version naming).
    """
    template_path = wiki_dir / _TEMPLATE_FILENAME

    # Create versioned backup of the current template before overwriting
    versions_dir = wiki_dir / _VERSIONS_DIR
    versions_dir.mkdir(parents=True, exist_ok=True)

    if template_path.exists():
        backup_path = versions_dir / f"template_epoch_{epoch}.md"
        shutil.copy2(template_path, backup_path)
        logger.debug("save_template: backed up to %s", backup_path)

    template_path.write_text(content, encoding="utf-8")
    logger.info("save_template: wrote template for epoch %d (%d chars)", epoch, len(content))


def build_extraction_prompt(
    template: str,
    chunk_content: str,
    prior_concepts: list[str],
) -> list[dict]:
    """Build LLM messages for extraction using the template.

    Args:
        template: The extraction template content.
        chunk_content: Text content of the chunk to extract from.
        prior_concepts: Concept names already seen in earlier chunks of
            the same source (avoids redundant re-extraction).

    Returns:
        List of message dicts suitable for complete_json().
    """
    prior_str = ", ".join(prior_concepts) if prior_concepts else "none"

    user_msg = (
        f"{template}\n\n"
        f"Previously extracted concepts from earlier sections of this source: {prior_str}. "
        "Do not re-extract these unless this section adds new information about them.\n\n"
        "Include only concepts that are clearly named and domain-specific. "
        "Skip generic terms like 'experiment', 'data', 'result'.\n\n"
        "Return ONLY valid JSON with keys: concepts, parameters, mechanisms, "
        "relationships, gaps. No prose before or after the JSON.\n\n"
        "--- TEXT ---\n"
        f"{chunk_content}\n"
        "--- END TEXT ---"
    )

    return [{"role": "user", "content": user_msg}]
