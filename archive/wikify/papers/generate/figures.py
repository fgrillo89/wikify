"""Figure placeholder extraction from generated markdown."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Patterns for the two-line figure block the LLM produces:
#   ![Figure N: short caption](figure_N_placeholder.png)
#   **Figure N.** Full caption describing ...
_IMG_RE = re.compile(
    r"!\[Figure\s+(\d+):\s*([^\]]*)\]\([^)]*\)",
    re.IGNORECASE,
)
_CAPTION_RE = re.compile(
    r"\*\*Figure\s+(\d+)\.\*\*\s*(.*)",
    re.IGNORECASE,
)

# Keywords used to infer figure_type from caption text
_TYPE_KEYWORDS: list[tuple[str, str]] = [
    ("schematic", "schematic"),
    ("diagram", "schematic"),
    ("bar chart", "bar_chart"),
    ("bar graph", "bar_chart"),
    ("line plot", "line_plot"),
    ("line graph", "line_plot"),
    ("heatmap", "heatmap"),
    ("heat map", "heatmap"),
    ("TEM image", "micrograph"),
    ("SEM image", "micrograph"),
    ("micrograph", "micrograph"),
    ("AFM", "micrograph"),
    ("cross-section", "micrograph"),
    ("scatter plot", "scatter_plot"),
    ("scatter", "scatter_plot"),
    ("histogram", "histogram"),
    ("table", "table"),
    ("flowchart", "flowchart"),
    ("flow chart", "flowchart"),
]


def _infer_figure_type(caption: str) -> str:
    """Infer the figure type from keywords in the caption text."""
    lower = caption.lower()
    for keyword, type_label in _TYPE_KEYWORDS:
        if keyword.lower() in lower:
            return type_label
    return "illustration"


@dataclass
class FigurePlaceholder:
    """A figure placeholder extracted from generated markdown."""

    number: int
    caption: str  # detailed caption (from **Figure N.** line)
    figure_type: str  # inferred type: "schematic", "bar_chart", etc.
    section: str  # section heading this figure belongs to
    description: str  # same as caption — used by a figure-generation agent
    short_caption: str = field(default="")  # from the ![...] alt text


def extract_figure_placeholders(
    markdown: str,
    *,
    section_map: dict[int, str] | None = None,
) -> list[FigurePlaceholder]:
    """Extract all FigurePlaceholder objects from generated markdown.

    Parameters
    ----------
    markdown:
        Full document markdown string (output of write_paper).
    section_map:
        Optional mapping of figure number → section heading.  When not
        provided, the function infers section membership by scanning the
        markdown top-to-bottom and tracking the most-recent heading.

    Returns
    -------
    List of FigurePlaceholder, one per ``![Figure N: ...]`` found.
    """
    # --- Phase 1: collect short captions from ![Figure N: ...] lines ----------
    short_captions: dict[int, str] = {}
    for m in _IMG_RE.finditer(markdown):
        fig_num = int(m.group(1))
        short_captions[fig_num] = m.group(2).strip()

    # --- Phase 2: collect detailed captions from **Figure N.** lines ---------
    detailed_captions: dict[int, str] = {}
    for m in _CAPTION_RE.finditer(markdown):
        fig_num = int(m.group(1))
        detailed_captions[fig_num] = m.group(2).strip()

    # --- Phase 3: build section map by scanning line-by-line -----------------
    if section_map is None:
        section_map = _build_section_map(markdown)

    # --- Phase 4: assemble FigurePlaceholder objects --------------------------
    placeholders: list[FigurePlaceholder] = []
    for fig_num in sorted(short_captions):
        short = short_captions[fig_num]
        detailed = detailed_captions.get(fig_num, short)
        section = section_map.get(fig_num, "")
        placeholders.append(
            FigurePlaceholder(
                number=fig_num,
                caption=detailed,
                figure_type=_infer_figure_type(detailed or short),
                section=section,
                description=detailed or short,
                short_caption=short,
            )
        )

    return placeholders


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^#{1,3}\s+(.+)$")


def _build_section_map(markdown: str) -> dict[int, str]:
    """Return a dict mapping figure number → nearest preceding section heading."""
    current_section = ""
    section_by_fig: dict[int, str] = {}

    for line in markdown.splitlines():
        stripped = line.strip()
        h_match = _HEADING_RE.match(stripped)
        if h_match:
            current_section = h_match.group(1).strip()
            continue
        img_match = _IMG_RE.search(stripped)
        if img_match:
            fig_num = int(img_match.group(1))
            section_by_fig[fig_num] = current_section

    return section_by_fig
