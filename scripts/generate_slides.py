"""Generate a 10-slide PPTX on ALD-based memristors for neuromorphic computing.

Patches the LLM client with a mock that returns a realistic, technically accurate
slide plan, then runs the actual ScholarForge pipeline to produce the PPTX.

Usage:
    uv run python scripts/generate_slides.py
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Slide content — technically accurate for ALD/memristor research
# ---------------------------------------------------------------------------

SLIDE_PLAN: list[dict] = [
    {
        "title": "ALD-Based Memristors for Neuromorphic Computing",
        "bullets": [
            "A Review of Materials, Devices, and Architectures",
            "Based on 20 papers (2008–2025)",
        ],
        "notes": (
            "Overview of atomic layer deposition approaches for memristive synaptic devices"
        ),
        "source_papers": [],
    },
    {
        "title": "Presentation Outline",
        "bullets": [
            "Memristor fundamentals and the ALD advantage",
            "HfO\u2082 and alternative oxide systems",
            "Synaptic behavior: potentiation and depression",
            "Crossbar array architectures",
            "Challenges and future directions",
        ],
        "notes": (
            "This presentation covers the intersection of ALD fabrication "
            "and neuromorphic device design"
        ),
        "source_papers": [],
    },
    {
        "title": "Memristor Fundamentals",
        "bullets": [
            "Two-terminal device with tunable non-volatile resistance (Chua, 1971)",
            "First physical realization: Pt/TiO\u2082/Pt by HP Labs (Strukov, 2008)",
            "Resistive switching via ion migration and filament formation",
            "Key metrics: on/off ratio, endurance, retention, switching speed",
        ],
        "notes": (
            "Chua predicted the memristor as the 4th passive circuit element. "
            "Strukov et al. demonstrated it physically using TiO\u2082 thin films."
        ),
        "source_papers": ["Chua 1971", "Strukov 2008"],
    },
    {
        "title": "Why ALD for Memristors?",
        "bullets": [
            "Sub-nanometer thickness control (~1.1 \u00c5/cycle for HfO\u2082)",
            "Conformal coverage enables 3D crossbar stacking",
            "Precise dopant control at atomic level (Yang, 2011)",
            "CMOS-compatible process temperatures (200–300\u00b0C)",
            "Enables bilayer and laminate structures for synaptic optimization",
        ],
        "notes": (
            "ALD provides unmatched control over oxide thickness and composition, "
            "critical for reproducible resistive switching"
        ),
        "source_papers": ["Yang 2011"],
    },
    {
        "title": "HfO\u2082-Based Memristive Devices",
        "bullets": [
            "Most studied ALD oxide for memristors",
            "PE-ALD optimization of 3–4.5 nm HfO\u2082 films (Kim, 2019)",
            "Al doping improves synaptic linearity (Chandrasekaran, 2019)",
            "HfO\u2082/HfO\u2093 bilayer: optimized vacancy concentration (Liu, 2020)",
            "TiN/HfO\u2082/TiN stack by ALD shows synaptic properties (Matveyev, 2015)",
        ],
        "notes": (
            "HfO\u2082 dominates because of its CMOS compatibility and "
            "well-understood defect chemistry"
        ),
        "source_papers": ["Kim 2019", "Chandrasekaran 2019", "Liu 2020", "Matveyev 2015"],
    },
    {
        "title": "Alternative Oxide Systems",
        "bullets": [
            "SiNx memristors: analog switching, CMOS compatible (Kim, 2017)",
            "Fe\u2082O\u2083 by ALD: multi-level states using ferrocene/ozone (Porro, 2018)",
            "FeO\u2093 optomemristor for vision computing (Wan, 2018)",
            "Al\u2082O\u2083/HfO\u2082 bilayer: stretchable, survives 30% strain (Ma, 2025)",
        ],
        "notes": (
            "Diversifying beyond HfO\u2082 enables new functionalities like "
            "optical sensing and mechanical flexibility"
        ),
        "source_papers": ["Kim 2017", "Porro 2018", "Wan 2018", "Ma 2025"],
    },
    {
        "title": "Synaptic Behavior",
        "bullets": [
            "Analog conductance modulation mimics biological synapses",
            "Long-term potentiation/depression demonstrated (Jo, 2010)",
            "Ultra-low energy: ~1 pJ per synaptic event (Gao, 2014)",
            "Key challenge: nonlinearity in weight updates",
            "Pulse engineering improves linearity (identical pulses vs. incremental)",
        ],
        "notes": (
            "Jo et al. demonstrated nanoscale Si memristors as synapses. "
            "Gao achieved sub-pJ switching with 3D oxide structures."
        ),
        "source_papers": ["Jo 2010", "Gao 2014"],
    },
    {
        "title": "Crossbar Array Architectures",
        "bullets": [
            "Passive crossbar: highest density, analog-grade (Kim, 2021: 4K array)",
            "3D stacking: monolithic Pt/Al\u2082O\u2083/TiO\u2082\u2093 crossbars (Adam, 2017)",
            "In-memory computing: matrix-vector multiply in O(1) (Li, 2018)",
            "Sneak path mitigation via selector devices or 1T1R cells",
            "Foldable architectures for brain-like form factor (Ghoneim, 2014)",
        ],
        "notes": (
            "Kim et al. demonstrated the largest passive analog crossbar (64\u00d764). "
            "Li et al. showed dot-product computation accuracy."
        ),
        "source_papers": ["Kim 2021", "Adam 2017", "Li 2018", "Ghoneim 2014"],
    },
    {
        "title": "Challenges and Future Directions",
        "bullets": [
            "Device-to-device variability limits array yield",
            "Cycle-to-cycle variation affects inference accuracy",
            "Linearity-endurance tradeoff in analog switching",
            "Need for standardized benchmarking across materials",
            "Emerging: 2D materials (Huh, 2020), flexible substrates, optical memristors",
        ],
        "notes": (
            "Sokolov 2019 provides a comprehensive review of remaining challenges. "
            "2D materials and flexible substrates are frontier areas."
        ),
        "source_papers": ["Sokolov 2019", "Huh 2020"],
    },
    {
        "title": "Conclusions",
        "bullets": [
            "ALD is the enabling technology for reproducible memristors",
            "HfO\u2082 leads, but Fe\u2082O\u2083, SiNx, and bilayers expand the design space",
            "Crossbar arrays approach practical neuromorphic computing scale",
            "Key remaining challenges: variability, linearity, standardization",
            "Future: 3D integration, flexible neuromorphics, hybrid optical-electronic",
        ],
        "notes": (
            "ALD-based memristors are at the intersection of mature fabrication "
            "and emerging computing paradigms"
        ),
        "source_papers": [],
    },
]


def main() -> None:
    from scholarforge.export.pptx_export import export_slides
    from scholarforge.generate.planner import plan_slides
    from scholarforge.retrieve.context import retrieve_all_papers

    output_path = Path("data/output/presentation.pptx")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("Retrieving literature context...")
    context = retrieve_all_papers()
    print(f"  {len(context.papers)} papers loaded")

    print("Planning slides (mock LLM)...")
    with patch("scholarforge.generate.planner.complete_json", return_value=SLIDE_PLAN):
        slide_plan = plan_slides(
            prompt="ALD-based memristors for neuromorphic computing",
            context=context,
            num_slides=10,
        )

    print(f"  {len(slide_plan)} slides planned")

    print(f"Exporting to {output_path}...")
    export_slides(
        slide_plan,
        output_path,
        title="ALD-Based Memristors for Neuromorphic Computing",
    )

    size_kb = output_path.stat().st_size / 1024
    print(f"\nDone. File: {output_path}  ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
