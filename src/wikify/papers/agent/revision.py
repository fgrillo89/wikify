"""Targeted revision of the weakest section in a generated literature review.

Implements the final step of the canonical research pipeline:
  generate -> verify -> PI review -> rewrite weakest section -> re-evaluate

Usage:
    from wikify.papers.agent.revision import revise_weakest_section
    revised = revise_weakest_section(review_text, pi_result, topic="ALD memristors")
"""

from __future__ import annotations

import logging
import re

from wikify.papers.evaluate.pi_review import PIReviewResult

logger = logging.getLogger(__name__)

_REVISION_SYSTEM_PROMPT = """\
You are a senior academic writer revising one section of a literature review.

Your task:
1. Read the PI reviewer's specific feedback about this section.
2. Read the fresh evidence from the corpus provided below.
3. Rewrite ONLY the section, addressing the PI's concern directly.

Rules:
- Return ONLY the rewritten section markdown, starting with the section heading.
- Do not alter the heading level (## or ###).
- Keep citations in [REF:AuthorName Year - Title] format.
- No em-dashes as parenthetical separators.
- No meta-commentary: never write "this section" or "this review" as a subject.
- Be specific: cite numbers, measurements, and mechanisms.
- Target: 700-900 words for a body section, 200-300 for abstract/conclusion.
"""


def _find_section_bounds(review_text: str, section_name: str) -> tuple[int, int] | None:
    """Return (start, end) character indices of the named section in the review.

    Matches a heading line (## or ###) that contains the section name (case-insensitive).
    The section ends at the next same-or-higher-level heading, or end of text.
    Returns None if the section is not found.
    """
    heading_pat = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
    matches = list(heading_pat.finditer(review_text))

    target_idx = None
    target_level = 0
    for i, m in enumerate(matches):
        if section_name.lower() in m.group(2).lower():
            target_idx = i
            target_level = len(m.group(1))
            break

    if target_idx is None:
        return None

    start = matches[target_idx].start()

    # End = next heading of same or higher level (fewer #), or end of text
    end = len(review_text)
    for m in matches[target_idx + 1 :]:
        level = len(m.group(1))
        if level <= target_level:
            end = m.start()
            break

    return start, end


def _build_revision_prompt(
    section_text: str,
    section_name: str,
    pi_feedback: str,
    topic: str,
    evidence: str,
) -> str:
    parts = [
        f"## Section to revise: {section_name}",
        "",
        f"**Topic context:** {topic}",
        "",
        "**PI reviewer's feedback on this section:**",
        pi_feedback,
        "",
        "**Fresh evidence from the corpus:**",
        evidence if evidence else "(No additional evidence found — use what is in the section.)",
        "",
        "**Current section text:**",
        section_text,
        "",
        "Rewrite the section now, starting with the heading.",
    ]
    return "\n".join(parts)


def revise_weakest_section(
    review_text: str,
    pi_result: PIReviewResult,
    topic: str = "",
    model: str | None = None,
    max_evidence_chars: int = 8000,
    run_context=None,
) -> str:
    """Rewrite the section flagged as weakest by the PI reviewer.

    Args:
        review_text: Full markdown text of the generated review.
        pi_result: Parsed PIReviewResult from evaluate_pi + parse_pi_review.
        topic: Topic or prompt that generated the review (used for targeted search).
        model: litellm model string. Defaults to settings.llm_model.
        max_evidence_chars: Max characters of corpus evidence to include in prompt.
        run_context: Optional RunContext for telemetry.

    Returns:
        Full review text with the weakest section replaced by the revised version.
        Returns the original text unchanged if the weakest section cannot be found.
    """
    from wikify.core.llm.client import complete

    weakest = pi_result.weakest_section.strip()
    if not weakest:
        logger.warning("PI result has no weakest_section; skipping revision.")
        return review_text

    # Extract the section name from the PI's weakest_section description.
    # The field may be "Introduction — too vague" or just "Introduction".
    section_name = re.split(r"[—\-–:]", weakest)[0].strip()

    bounds = _find_section_bounds(review_text, section_name)
    if bounds is None:
        logger.warning("Could not locate section %r in review; skipping revision.", section_name)
        return review_text

    start, end = bounds
    section_text = review_text[start:end]

    # Fetch targeted evidence from the corpus
    evidence = _fetch_section_evidence(section_name, topic, max_evidence_chars)

    pi_feedback = weakest  # the full weakest_section string from the PI
    user_msg = _build_revision_prompt(section_text, section_name, pi_feedback, topic, evidence)

    revised_section = complete(
        messages=[
            {"role": "system", "content": _REVISION_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        model=model,
        temperature=0.3,
        max_tokens=2000,
        use_cache=False,
    )

    revised_review = review_text[:start] + revised_section.rstrip() + "\n\n" + review_text[end:]

    if run_context is not None:
        from wikify.papers.agent.run_context import add_run_warning

        msg = f"Revision applied to section: {section_name} (PI: {pi_result.overall_score}/10)"
        add_run_warning(run_context, msg)

    return revised_review


def _fetch_section_evidence(section_name: str, topic: str, max_chars: int) -> str:
    """Search the corpus for evidence relevant to the weakest section."""
    try:
        from wikify.papers.agent.tools import search_papers

        query = f"{topic} {section_name}".strip() if topic else section_name
        raw = search_papers(query=query, top_k=5, reason=f"Revision evidence for '{section_name}'")

        if isinstance(raw, dict):
            import json

            text = json.dumps(raw, indent=2)
        else:
            text = str(raw)

        return text[:max_chars]
    except Exception:
        logger.debug("Evidence fetch failed for revision; proceeding without.", exc_info=True)
        return ""
