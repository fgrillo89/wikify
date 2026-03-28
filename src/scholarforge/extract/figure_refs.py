"""Extract figure references (captions) from academic paper markdown text.

Caption-first approach — no binary images required. Scans markdown lines for
figure caption patterns and returns structured FigureRef records.
"""

from __future__ import annotations

import hashlib
import re

from scholarforge.store.models import FigureRef

# Matches Fig., Fig, Figure, FIG., FIG, FIGURE followed by a number and optional letter.
# Examples matched: "Fig. 1", "Figure 2a", "FIG. 3B", "fig 10c"
_CAPTION_RE = re.compile(r"(?i)(fig(?:ure)?\.?\s*\d+[a-z]?)[.:\s\u2014\-]+(.+)")

# Matches headings: one or more leading '#' characters.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)")


def extract_figure_refs(md_text: str, paper_id: str) -> list[FigureRef]:
    """Extract figure captions from markdown text.

    Scans each line for heading markers (to track section context) and figure
    caption patterns. Returns one FigureRef per unique figure_key, keeping the
    first occurrence when duplicates appear.

    Args:
        md_text:  Full markdown text of the paper.
        paper_id: Stable identifier for the paper (used in ID hashing).

    Returns:
        List of FigureRef instances, possibly empty if no captions found.
    """
    refs: list[FigureRef] = []
    seen_keys: set[str] = set()
    current_section: str | None = None

    for line in md_text.splitlines():
        # Update section context from heading lines.
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            current_section = heading_match.group(2).strip()
            continue

        # Attempt to match a figure caption pattern.
        caption_match = _CAPTION_RE.match(line.strip())
        if not caption_match:
            continue

        figure_key = caption_match.group(1).strip()
        caption_text = caption_match.group(2).strip()[:500]

        if not caption_text:
            continue

        # Deduplicate by figure_key within this paper.
        if figure_key.lower() in seen_keys:
            continue
        seen_keys.add(figure_key.lower())

        ref_id = hashlib.sha256((paper_id + figure_key).encode()).hexdigest()[:16]

        refs.append(
            FigureRef(
                id=ref_id,
                paper_id=paper_id,
                figure_key=figure_key,
                caption_text=caption_text,
                section_path=current_section,
                page_number=None,
            )
        )

    return refs
