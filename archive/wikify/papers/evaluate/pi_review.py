"""LLM-as-PI qualitative review for generated literature reviews.

Scores a review on 7 domain-agnostic criteria using a senior researcher
persona. Complements automated embedding-based metrics, which cannot detect
cross-community synthesis or thesis coherence.

Usage:
    from wikify.papers.evaluate.pi_review import evaluate_pi
    report = evaluate_pi(review_text, domain_hint="materials science")
    print(report)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PI_SYSTEM_PROMPT = """\
You are a senior researcher with 20+ years of experience publishing and reviewing
for high-impact journals. You have broad expertise across scientific disciplines
and a reputation for rigorous, constructive peer review.

Your task is to evaluate a literature review on the following 7 criteria. Score
each from 1 (poor) to 10 (excellent). Then give an overall score and a brief
verdict (2-3 sentences).

Criteria:
1. **Scientific accuracy** — Are claims supported by cited evidence? Are there
   unsupported assertions or factual errors?
2. **Argument progression** — Does each section build coherently on the previous?
   Is there a clear thesis that the body develops and defends?
3. **Synthesis quality** — Does the review draw conclusions that go beyond
   summarizing individual papers? Are there sentences that connect 2+ papers
   to a conclusion neither stated alone?
4. **Gap identification** — Are gaps specific and actionable? Could a postdoc
   design an experiment based on each gap? Or are gaps vague ("more research needed")?
5. **Citation integration** — Is each citation doing functional work? Are citations
   woven into arguments, or front-loaded and never referenced again?
6. **Prose quality** — Are sentences clear and specific? Does the abstract lead
   with the most important finding? Are there em-dash or hedging violations?
7. **Specificity** — Are quantitative claims cited with numbers and units? Or
   does the review rely on vague descriptors ("high", "significant", "improved")?

Format your response EXACTLY as follows (no extra text before or after):

## PI Review

| Criterion | Score | Comment |
|-----------|-------|---------|
| Scientific accuracy | X/10 | <one sentence> |
| Argument progression | X/10 | <one sentence> |
| Synthesis quality | X/10 | <one sentence> |
| Gap identification | X/10 | <one sentence> |
| Citation integration | X/10 | <one sentence> |
| Prose quality | X/10 | <one sentence> |
| Specificity | X/10 | <one sentence> |

**Overall: X/10**

**Verdict:** <2-3 sentences on whether this is publishable, what the primary
weakness is, and the single most impactful fix>

**Strongest sentence:** <quote the single best sentence from the review>

**Weakest section:** <identify the section that needs the most work and why>
"""

_WORD_LIMIT = 8000  # truncate very long reviews to keep prompt manageable


@dataclass
class PIReviewResult:
    """Structured representation of an LLM-as-PI review."""

    report: str
    scores: dict[str, float] = field(default_factory=dict)
    overall_score: float | None = None
    verdict: str = ""
    strongest_sentence: str = ""
    weakest_section: str = ""


def evaluate_pi(
    review_text: str,
    domain_hint: str = "",
    model: str | None = None,
) -> str:
    """Score a literature review using an LLM-as-senior-researcher rubric.

    Args:
        review_text: The full markdown text of the review to evaluate.
        domain_hint: Optional one-line field description ("condensed matter physics",
            "computational biology", etc.). Helps the PI persona calibrate expectations.
            Leave empty for a fully domain-agnostic evaluation.
        model: litellm model string. Defaults to settings.llm_model.

    Returns:
        Formatted markdown report with per-criterion scores and verdict.
    """
    from wikify.core.llm.client import complete

    # Truncate to avoid context overflow
    body = review_text[: _WORD_LIMIT * 5]  # ~5 chars/word estimate

    domain_line = ""
    if domain_hint:
        domain_line = f"\nField context: {domain_hint}\n"

    user_msg = f"{domain_line}\nHere is the literature review to evaluate:\n\n---\n{body}\n---"

    response = complete(
        messages=[
            {"role": "system", "content": _PI_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        model=model,
        temperature=0.2,
        max_tokens=1200,
        use_cache=False,  # PI reviews should not be cached (same text, fresh judgment)
    )
    return response


def parse_pi_scores(pi_report: str) -> dict[str, float]:
    """Extract per-criterion and overall scores from a PI review report.

    Returns a dict mapping criterion name -> score (float, 1-10 scale).
    Keys: "scientific_accuracy", "argument_progression", "synthesis_quality",
          "gap_identification", "citation_integration", "prose_quality",
          "specificity", "overall".
    Returns empty dict if parsing fails.
    """
    scores: dict[str, float] = {}

    _criterion_map = {
        "scientific accuracy": "scientific_accuracy",
        "argument progression": "argument_progression",
        "synthesis quality": "synthesis_quality",
        "gap identification": "gap_identification",
        "citation integration": "citation_integration",
        "prose quality": "prose_quality",
        "specificity": "specificity",
    }

    # Table rows: | Criterion | X/10 | Comment |
    row_pat = re.compile(r"\|\s*([^|]+?)\s*\|\s*(\d+(?:\.\d+)?)/10\s*\|", re.IGNORECASE)
    for m in row_pat.finditer(pi_report):
        label = m.group(1).strip().lower()
        score = float(m.group(2))
        key = _criterion_map.get(label)
        if key:
            scores[key] = score

    # Overall score: **Overall: X/10**
    overall_pat = re.compile(r"\*\*Overall:\s*(\d+(?:\.\d+)?)/10\*\*", re.IGNORECASE)
    m = overall_pat.search(pi_report)
    if m:
        scores["overall"] = float(m.group(1))

    return scores


def parse_pi_review(pi_report: str) -> PIReviewResult:
    """Parse a PI review report into a structured result."""
    scores = parse_pi_scores(pi_report)

    verdict_match = re.search(r"\*\*Verdict:\*\*\s*(.+)", pi_report, re.IGNORECASE)
    strongest_match = re.search(
        r"\*\*Strongest sentence:\*\*\s*(.+)",
        pi_report,
        re.IGNORECASE,
    )
    weakest_match = re.search(
        r"\*\*Weakest section:\*\*\s*(.+)",
        pi_report,
        re.IGNORECASE,
    )

    return PIReviewResult(
        report=pi_report,
        scores=scores,
        overall_score=scores.get("overall"),
        verdict=verdict_match.group(1).strip() if verdict_match else "",
        strongest_sentence=strongest_match.group(1).strip() if strongest_match else "",
        weakest_section=weakest_match.group(1).strip() if weakest_match else "",
    )
