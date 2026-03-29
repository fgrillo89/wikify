"""Extract figure and table references (captions) from academic paper markdown text.

Caption-first approach — no binary images required. Scans markdown lines for
figure/table caption patterns and returns structured FigureRef records.
"""

from __future__ import annotations

import hashlib
import re

from scholarforge.store.models import FigureRef

# Matches figure captions: Fig., Fig, Figure, FIG followed by number + optional letter.
# Also matches inline patterns like "**Fig. 1.** caption text" (bold key followed by caption).
_FIG_CAPTION_RE = re.compile(
    r"(?i)\*{0,2}(fig(?:ure)?\.?\s*\d+[a-z]?)\*{0,2}[.:\s\u2014\-]+(.+)"
)

# Matches table captions: Table, TABLE, Tbl followed by number.
_TABLE_CAPTION_RE = re.compile(
    r"(?i)\*{0,2}(table\.?\s*\d+[a-z]?)\*{0,2}[.:\s\u2014\-]+(.+)"
)

# Matches headings: one or more leading '#' characters.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)")


def extract_figure_refs(md_text: str, paper_id: str) -> list[FigureRef]:
    """Extract figure and table captions from markdown text.

    Scans each line for heading markers (to track section context) and
    figure/table caption patterns. Returns one FigureRef per unique key,
    keeping the first occurrence when duplicates appear.

    Searches both at line start and within lines to catch inline captions
    like "**Fig. 1.** The measured I-V curve..."

    Args:
        md_text:  Full markdown text of the paper.
        paper_id: Stable identifier for the paper (used in ID hashing).

    Returns:
        List of FigureRef instances, possibly empty if no captions found.
    """
    refs: list[FigureRef] = []
    seen_keys: set[str] = set()
    current_section: str | None = None

    patterns = [_FIG_CAPTION_RE, _TABLE_CAPTION_RE]

    for line in md_text.splitlines():
        # Update section context from heading lines.
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            current_section = heading_match.group(2).strip()
            continue

        stripped = line.strip()

        # Try each pattern — search anywhere in line (not just start)
        for pattern in patterns:
            caption_match = pattern.search(stripped)
            if not caption_match:
                continue

            figure_key = caption_match.group(1).strip()
            # Remove any remaining bold markers from the key
            figure_key = figure_key.replace("*", "")
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
