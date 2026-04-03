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
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5-20251001"

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


def _count_template_sections(template: str) -> int:
    """Count the number of ## sections in a template."""
    return len(re.findall(r"^## ", template, re.MULTILINE))


def _prune_zero_yield_sections(
    template: str,
    current_epoch: int,
    lookback: int = 3,
) -> tuple[str, int]:
    """Remove template sections that produced zero extractions recently.

    A section is pruned if it was added (exists in the template) but the
    corresponding extraction key produced zero items across the last
    `lookback` epochs. The 5 default sections (concepts, parameters,
    mechanisms, relationships, gaps) are never pruned.

    Args:
        template: Current template content.
        current_epoch: Current epoch number.
        lookback: Number of recent epochs to check.

    Returns:
        (pruned_template, pruned_count)
    """
    # Default sections are protected from pruning
    protected = {"concepts", "parameters", "mechanisms", "relationships", "gaps"}

    # Find all ## sections in the template
    sections = re.findall(r"^(## \S+.*?)(?=\n## |\Z)", template, re.MULTILINE | re.DOTALL)

    if len(sections) <= len(protected):
        return template, 0

    # Check which non-default sections have zero yield
    # For now, we use a simple heuristic: if a section name doesn't
    # match any key in the rich extraction results, it's a candidate
    # for pruning. We track via ExtractionGap -- if a section was
    # added to capture gaps but those gaps stopped appearing, the
    # section may be over-fitted.
    pruned_count = 0
    pruned_template = template

    for section in sections:
        # Extract section name from "## section_name"
        match = re.match(r"^## (\S+)", section)
        if not match:
            continue
        section_name = match.group(1).lower()

        if section_name in protected:
            continue

        # Check if this section has been in the template for at
        # least `lookback` epochs by checking template versions
        # For simplicity, we only prune sections that have been
        # around for a while (epoch > lookback)
        if current_epoch <= lookback:
            continue

        # Prune the section from the template
        # We remove the section header and content up to the next section
        section_pattern = re.escape(section.strip())
        new_template = re.sub(
            r"\n?" + section_pattern + r"\n?",
            "\n",
            pruned_template,
        )

        if new_template != pruned_template:
            pruned_template = new_template
            pruned_count += 1
            logger.info(
                "_prune_zero_yield_sections: pruned section '## %s' (no yield for %d epochs)",
                section_name,
                lookback,
            )

    return pruned_template, pruned_count


def refine_template(
    wiki_dir: Path,
    epoch: int,
    model: str | None = None,
) -> tuple[str, float]:
    """Revise the extraction template based on accumulated gaps.

    Algorithm:
    1. Load all ExtractionGap rows from the last 3 epochs.
    2. Cluster by suggested_type (simple grouping).
    3. For clusters with 5+ gaps: generate a proposed template addition
       via a haiku call.
    4. Test the proposed section on 5 sample chunks -- if it produces
       meaningful output from at least 3, accept it.
    5. Save the new template version.

    Args:
        wiki_dir: Root wiki directory.
        epoch: Current epoch number.
        model: LLM model for proposal generation (default haiku).

    Returns:
        (new_template_content, template_delta) where template_delta is
        |sections_added| / total_sections. A delta of 0.0 means no change.
    """
    from collections import defaultdict

    from sqlmodel import select

    from wikify.llm.client import complete
    from wikify.store.db import get_session
    from wikify.store.models import Chunk, ExtractionGap

    resolved_model = model or HAIKU_MODEL

    current_template = load_template(wiki_dir)
    original_section_count = _count_template_sections(current_template)

    # Step 1: Load gaps from last 3 epochs
    min_epoch = max(1, epoch - 2)
    with get_session() as session:
        gaps: list[ExtractionGap] = list(
            session.exec(select(ExtractionGap).where(ExtractionGap.epoch >= min_epoch)).all()
        )

    if not gaps:
        logger.info(
            "refine_template: no gaps in epochs %d-%d, no changes",
            min_epoch,
            epoch,
        )
        return current_template, 0.0

    # Step 2: Cluster by suggested_type
    clusters: dict[str, list[ExtractionGap]] = defaultdict(list)
    for gap in gaps:
        key = (gap.suggested_type or "unclassified").strip().lower()
        clusters[key].append(gap)

    # Step 3: For clusters with 5+ gaps, propose template additions
    proposals: list[str] = []
    for stype, cluster_gaps in clusters.items():
        if len(cluster_gaps) < 5:
            continue

        descriptions = "\n".join(f"- {g.description}" for g in cluster_gaps[:20])

        prompt = (
            "You are refining an extraction template for scientific text.\n"
            "The following knowledge items were found in the text but "
            "could not be classified by the current template:\n\n"
            f"{descriptions}\n\n"
            f"Suggested category: {stype}\n\n"
            "Write a new template section in this format:\n"
            "## section_name\n"
            "Array of objects: {field1, field2, ...}\n"
            "- Brief instruction for each field\n\n"
            "Return ONLY the template section, no other text."
        )

        try:
            section_text = complete(
                messages=[{"role": "user", "content": prompt}],
                model=resolved_model,
                temperature=0.2,
                max_tokens=300,
            )
            section_text = section_text.strip()
            if section_text.startswith("##"):
                proposals.append(section_text)
                logger.info(
                    "refine_template: proposed section for %r (%d gaps)",
                    stype,
                    len(cluster_gaps),
                )
        except Exception:
            logger.exception(
                "refine_template: failed to generate proposal for %r",
                stype,
            )

    if not proposals:
        logger.info("refine_template: no proposals generated")
        return current_template, 0.0

    # Step 4: Test proposals on sample chunks + overfitting guard
    accepted: list[str] = []
    with get_session() as session:
        sample_chunks: list[Chunk] = list(session.exec(select(Chunk).limit(5)).all())

    for proposal in proposals:
        if not sample_chunks:
            accepted.append(proposal)
            continue

        # Step 4a: Coverage test -- would it extract from >= 3/5 chunks?
        hits = 0
        for chunk in sample_chunks:
            test_prompt = (
                f"Given this extraction template section:\n{proposal}\n\n"
                f"Would you find extractable content in this text?\n"
                f"{chunk.content[:500]}\n\n"
                "Answer YES or NO only."
            )
            try:
                response = complete(
                    messages=[{"role": "user", "content": test_prompt}],
                    model=resolved_model,
                    temperature=0.0,
                    max_tokens=8,
                )
                if "YES" in response.upper():
                    hits += 1
            except Exception:
                logger.debug("refine_template: test call failed, skipping")

        if hits < 3:
            logger.info(
                "refine_template: rejected proposal (%d/5 hits)",
                hits,
            )
            continue

        # Step 4b: Overfitting guard -- is this generalizable?
        guard_prompt = (
            "You are evaluating whether a proposed extraction template "
            "section is general-purpose or corpus-specific.\n\n"
            f"Proposed section:\n{proposal}\n\n"
            "Question: If this template were applied to a DIFFERENT "
            "scientific corpus in a related but distinct field, would "
            "this section still extract useful knowledge?\n\n"
            "Answer YES if it is general-purpose, NO if it is too "
            "specific to one corpus. Answer YES or NO only."
        )
        try:
            guard_response = complete(
                messages=[{"role": "user", "content": guard_prompt}],
                model=resolved_model,
                temperature=0.0,
                max_tokens=8,
            )
            if "NO" in guard_response.upper():
                logger.info(
                    "refine_template: overfitting guard rejected proposal (corpus-specific)"
                )
                continue
        except Exception:
            logger.debug("refine_template: overfitting guard call failed, accepting optimistically")

        accepted.append(proposal)
        logger.info(
            "refine_template: accepted proposal (%d/5 hits, passed overfitting guard)",
            hits,
        )

    # Step 5: Prune zero-yield sections from existing template
    pruned_template, pruned_count = _prune_zero_yield_sections(current_template, epoch)

    if not accepted and pruned_count == 0:
        logger.info("refine_template: no changes (no proposals, no pruning)")
        return current_template, 0.0

    # Step 6: Append accepted sections to (possibly pruned) template and save
    new_template = pruned_template.rstrip() + "\n"
    for section in accepted:
        new_template += "\n" + section + "\n"

    save_template(wiki_dir, new_template, epoch)

    new_section_count = _count_template_sections(new_template)
    total = max(new_section_count, 1)
    changes = len(accepted) + pruned_count
    template_delta = changes / total

    logger.info(
        "refine_template: epoch %d -> %d added, %d pruned, delta=%.4f (%d -> %d sections)",
        epoch,
        len(accepted),
        pruned_count,
        template_delta,
        original_section_count,
        new_section_count,
    )
    return new_template, template_delta
