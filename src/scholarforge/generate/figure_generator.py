"""Figure generation from FigurePlan specs."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

from scholarforge.store.models import FigurePlan  # noqa: E402

# ── constants ──────────────────────────────────────────────────────────────────

_DPI = 300
_DEFAULT_COLOR = "#4C72B0"
_FIGSIZE = (8, 5)

# ── helpers ────────────────────────────────────────────────────────────────────


def _apply_spec_defaults(spec: dict[str, Any]) -> dict[str, Any]:
    """Fill missing keys in a generation_spec with sensible defaults."""
    return {
        "title": spec.get("title", ""),
        "x_label": spec.get("x_label", ""),
        "y_label": spec.get("y_label", ""),
        "data": spec.get("data", []),
        "color": spec.get("color", _DEFAULT_COLOR),
    }


# ── chart handlers ─────────────────────────────────────────────────────────────


def _bar_chart(spec: dict[str, Any], output_path: Path) -> None:
    """Generate a bar chart."""
    s = _apply_spec_defaults(spec)
    xs = [str(row[0]) for row in s["data"]]
    ys = [float(row[1]) for row in s["data"]]

    fig, ax = plt.subplots(figsize=_FIGSIZE)
    ax.bar(xs, ys, color=s["color"], edgecolor="black", linewidth=0.7)
    ax.set_title(s["title"], fontsize=13, fontweight="bold", pad=10)
    ax.set_xlabel(s["x_label"], fontsize=11)
    ax.set_ylabel(s["y_label"], fontsize=11)
    ax.tick_params(axis="both", labelsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(output_path, dpi=_DPI)
    plt.close(fig)


def _line_chart(spec: dict[str, Any], output_path: Path) -> None:
    """Generate a line chart."""
    s = _apply_spec_defaults(spec)
    xs = [float(row[0]) for row in s["data"]]
    ys = [float(row[1]) for row in s["data"]]

    fig, ax = plt.subplots(figsize=_FIGSIZE)
    ax.plot(xs, ys, color=s["color"], linewidth=2, marker="o", markersize=5)
    ax.set_title(s["title"], fontsize=13, fontweight="bold", pad=10)
    ax.set_xlabel(s["x_label"], fontsize=11)
    ax.set_ylabel(s["y_label"], fontsize=11)
    ax.tick_params(axis="both", labelsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(output_path, dpi=_DPI)
    plt.close(fig)


def _heatmap(spec: dict[str, Any], output_path: Path) -> None:
    """Generate a heatmap from a 2D data grid."""
    s = _apply_spec_defaults(spec)
    data_raw = s["data"]
    # Expect data as list of rows (list of lists)
    matrix = np.array(data_raw, dtype=float)

    fig, ax = plt.subplots(figsize=_FIGSIZE)
    im = ax.imshow(matrix, aspect="auto", cmap="viridis")
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    ax.set_title(s["title"], fontsize=13, fontweight="bold", pad=10)
    ax.set_xlabel(s["x_label"], fontsize=11)
    ax.set_ylabel(s["y_label"], fontsize=11)
    ax.tick_params(axis="both", labelsize=10)
    fig.tight_layout()
    fig.savefig(output_path, dpi=_DPI)
    plt.close(fig)


def _ald_schematic(spec: dict[str, Any], output_path: Path) -> None:
    """Generate a publication-quality ALD 4-step cycle schematic."""
    title = spec.get("title", "ALD Process Cycle")

    # Layout: 4 boxes arranged in a 2×2 grid with circular arrows between them
    fig, ax = plt.subplots(figsize=(9, 7))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.set_aspect("equal")
    ax.axis("off")

    # Step definitions: (label, sub-label, box center x, box center y, box color)
    steps = [
        ("Step 1", "Precursor\nPulse", 2.8, 7.2, "#AED6F1"),
        ("Step 2", "Purge", 7.2, 7.2, "#A9DFBF"),
        ("Step 3", "Co-reactant\nPulse", 7.2, 2.8, "#F9E79F"),
        ("Step 4", "Purge", 2.8, 2.8, "#F5CBA7"),
    ]

    box_w, box_h = 2.8, 1.8

    for step_label, sub_label, cx, cy in [s[:4] for s in steps]:
        color = [s[4] for s in steps if s[0] == step_label][0]
        box = FancyBboxPatch(
            (cx - box_w / 2, cy - box_h / 2),
            box_w,
            box_h,
            boxstyle="round,pad=0.12",
            facecolor=color,
            edgecolor="#2C3E50",
            linewidth=1.8,
        )
        ax.add_patch(box)
        # Step label (bold)
        ax.text(
            cx,
            cy + 0.28,
            step_label,
            ha="center",
            va="center",
            fontsize=10,
            fontweight="bold",
            color="#1A252F",
        )
        # Sub-label
        ax.text(
            cx,
            cy - 0.28,
            sub_label,
            ha="center",
            va="center",
            fontsize=9,
            color="#2C3E50",
        )

    # Arrows: 1→2 (top), 2→3 (right), 3→4 (bottom), 4→1 (left)
    arrow_props = dict(
        arrowstyle="-|>",
        color="#2C3E50",
        lw=2.0,
        mutation_scale=18,
    )

    # 1→2 (horizontal, top): from right edge of box1 to left edge of box2
    ax.add_patch(FancyArrowPatch((4.2, 7.2), (5.8, 7.2), **arrow_props))
    # 2→3 (vertical, right): from bottom edge of box2 to top edge of box3
    ax.add_patch(FancyArrowPatch((7.2, 6.3), (7.2, 3.7), **arrow_props))
    # 3→4 (horizontal, bottom): from left edge of box3 to right edge of box4
    ax.add_patch(FancyArrowPatch((5.8, 2.8), (4.2, 2.8), **arrow_props))
    # 4→1 (vertical, left): from top edge of box4 to bottom edge of box1
    ax.add_patch(FancyArrowPatch((2.8, 3.7), (2.8, 6.3), **arrow_props))

    # "Cycle" label in the center
    ax.text(
        5.0,
        5.0,
        "ALD\nCycle",
        ha="center",
        va="center",
        fontsize=11,
        fontweight="bold",
        color="#7F8C8D",
        style="italic",
    )

    # Circular cycle indicator (dashed ellipse)
    ellipse = mpatches.Ellipse(
        (5.0, 5.0),
        width=3.2,
        height=3.2,
        fill=False,
        linestyle="--",
        edgecolor="#BDC3C7",
        linewidth=1.2,
    )
    ax.add_patch(ellipse)

    # Title
    ax.text(
        5.0,
        9.4,
        title,
        ha="center",
        va="center",
        fontsize=13,
        fontweight="bold",
        color="#1A252F",
    )

    fig.tight_layout()
    fig.savefig(output_path, dpi=_DPI, bbox_inches="tight")
    plt.close(fig)


# ── dispatcher map ─────────────────────────────────────────────────────────────

_HANDLERS: dict[str, Any] = {
    "bar": _bar_chart,
    "line": _line_chart,
    "heatmap": _heatmap,
    "schematic": _ald_schematic,
}


# ── public API ─────────────────────────────────────────────────────────────────


class FigureGenerator:
    """Generate figures from FigurePlan specifications."""

    def generate(self, plan: FigurePlan, output_dir: Path) -> Path | None:
        """Generate a figure from a FigurePlan spec. Returns PNG path or None on failure."""
        if plan.type != "generate":
            return None
        spec = plan.generation_spec or {}
        chart_type = spec.get("type", "bar")
        handler = _HANDLERS.get(chart_type)
        if handler is None:
            raise ValueError(f"Unknown chart type: {chart_type!r}. Supported: {list(_HANDLERS)}")

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Derive filename from title or type
        title_slug = spec.get("title", chart_type).lower().replace(" ", "_").replace("/", "_")[:50]
        output_path = output_dir / f"{title_slug}.png"

        handler(spec, output_path)
        return output_path
