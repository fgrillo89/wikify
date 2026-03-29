"""Standalone mock generation test for ScholarForge.

Patches LLM calls with realistic mock responses and validates all generation
paths end-to-end: plan_paper, write_paper, plan_slides, chat_once.

Run with: uv run python tests/mock_generation_test.py
"""

from __future__ import annotations

import json
import re
from unittest.mock import patch

from scholarforge.generate.chat import chat_once
from scholarforge.generate.planner import plan_paper, plan_slides
from scholarforge.generate.writer import write_paper
from scholarforge.retrieve.context import RetrievedContext
from scholarforge.store.models import Chunk, Paper

# ── Mock data ─────────────────────────────────────────────────────────────────

MOCK_PAPER_PLAN_JSON = {
    "title": "Atomic Layer Deposition for Advanced Semiconductor Applications: A Survey",
    "paper_type": "lit_review",
    "target_length": 2500,
    "sections": [
        {
            "heading": "Abstract",
            "level": 1,
            "description": (
                "Brief overview of ALD and its significance in semiconductor fabrication."
            ),
            "target_tokens": 150,
            "source_papers": ["George 2010 - Atomic layer deposition"],
            "subsections": [],
        },
        {
            "heading": "Introduction",
            "level": 1,
            "description": "Motivation, historical context, and paper organisation.",
            "target_tokens": 300,
            "source_papers": [
                "George 2010 - Atomic layer deposition",
                "Miikkulainen 2013 - Crystallinity of inorganic films",
            ],
            "subsections": [
                {
                    "heading": "Historical Development",
                    "level": 2,
                    "description": "Origins of ALD from molecular layer epitaxy.",
                    "target_tokens": 150,
                    "source_papers": ["George 2010 - Atomic layer deposition"],
                }
            ],
        },
        {
            "heading": "Fundamental ALD Mechanisms",
            "level": 1,
            "description": (
                "Self-limiting surface reactions, precursor chemistry, and growth modes."
            ),
            "target_tokens": 500,
            "source_papers": [
                "George 2010 - Atomic layer deposition",
                "Knoops 2019 - Status and prospects of plasma-enhanced ALD",
            ],
            "subsections": [
                {
                    "heading": "Self-Limiting Reactions",
                    "level": 2,
                    "description": "Saturation behaviour and ALD window.",
                    "target_tokens": 200,
                    "source_papers": ["George 2010 - Atomic layer deposition"],
                },
                {
                    "heading": "Thermal vs. Plasma-Enhanced ALD",
                    "level": 2,
                    "description": "Comparison of energy sources and process windows.",
                    "target_tokens": 200,
                    "source_papers": ["Knoops 2019 - Status and prospects of plasma-enhanced ALD"],
                },
            ],
        },
        {
            "heading": "Applications in Semiconductor Devices",
            "level": 1,
            "description": "High-k gate dielectrics, diffusion barriers, and 3D nanostructures.",
            "target_tokens": 600,
            "source_papers": [
                "Miikkulainen 2013 - Crystallinity of inorganic films",
                "Ritala 2000 - Atomic layer epitaxy",
            ],
            "subsections": [
                {
                    "heading": "High-k Gate Dielectrics",
                    "level": 2,
                    "description": "HfO2, Al2O3, and integration challenges.",
                    "target_tokens": 250,
                    "source_papers": ["Miikkulainen 2013 - Crystallinity of inorganic films"],
                }
            ],
        },
        {
            "heading": "Discussion and Future Directions",
            "level": 1,
            "description": "Emerging precursors, area-selective ALD, and open challenges.",
            "target_tokens": 400,
            "source_papers": ["Knoops 2019 - Status and prospects of plasma-enhanced ALD"],
            "subsections": [],
        },
        {
            "heading": "Conclusion",
            "level": 1,
            "description": "Summary of key findings and outlook.",
            "target_tokens": 150,
            "source_papers": [],
            "subsections": [],
        },
    ],
}

MOCK_SLIDES_JSON = [
    {
        "title": "Atomic Layer Deposition: Principles and Applications",
        "bullets": [
            "ALD enables atomic-level thickness control",
            "Self-limiting surface chemistry ensures uniformity",
            "Key enabler for sub-10 nm semiconductor nodes",
        ],
        "notes": "Title slide — introduce speaker and overview.",
        "source_papers": [],
    },
    {
        "title": "Outline",
        "bullets": [
            "Motivation and historical context",
            "Fundamental ALD mechanisms",
            "Materials deposited by ALD",
            "Semiconductor applications",
            "Future directions",
        ],
        "notes": "Walk the audience through the structure of the talk.",
        "source_papers": [],
    },
    {
        "title": "What is ALD?",
        "bullets": [
            "Cyclic process: precursor pulse → purge → co-reactant → purge",
            "Each cycle deposits a sub-monolayer (0.1–0.3 Å/cycle)",
            "Excellent step coverage on complex 3D features",
            "Conformal deposition distinguishes ALD from CVD",
        ],
        "notes": "Emphasise the self-limiting nature and how it differs from CVD (George, 2010).",
        "source_papers": ["George 2010"],
    },
    {
        "title": "Self-Limiting Reaction Kinetics",
        "bullets": [
            "Surface saturation independent of precursor dose (within ALD window)",
            "Growth per cycle (GPC) is a process fingerprint",
            "Temperature window: too low → condensation, too high → decomposition",
            "Nucleation delay common on inert surfaces",
        ],
        "notes": "Show GPC vs. temperature plot here.",
        "source_papers": ["George 2010", "Knoops 2019"],
    },
    {
        "title": "Thermal vs. Plasma-Enhanced ALD",
        "bullets": [
            "Thermal ALD: relies on thermally activated surface chemistry",
            "PE-ALD: plasma provides reactive radicals at lower substrate temperatures",
            "PE-ALD enables wider material set and lower thermal budgets",
            "Trade-off: plasma damage, conformality loss in high-AR features",
        ],
        "notes": "Reference Knoops et al. 2019 for comprehensive comparison.",
        "source_papers": ["Knoops 2019"],
    },
    {
        "title": "High-k Gate Dielectrics",
        "bullets": [
            "SiO2 replaced by HfO2 at 45 nm node (Intel, 2007)",
            "ALD HfO2 with TMA interface passivation",
            "Al2O3 as blocking layer in flash memory",
            "EOT scaling to < 0.5 nm demonstrated",
        ],
        "notes": "Miikkulainen 2013 provides detailed crystallinity analysis for HfO2.",
        "source_papers": ["Miikkulainen 2013"],
    },
    {
        "title": "Diffusion Barriers and Interconnects",
        "bullets": [
            "TaN/Ta bilayer replaced by ALD TaN as Cu barrier",
            "Sub-2 nm barriers required at 7 nm node",
            "ALD WN and MoN explored as alternatives",
            "Resistivity challenges at ultrathin limits",
        ],
        "notes": "Discuss roadmap pressure from IRDS scaling requirements.",
        "source_papers": ["Ritala 2000"],
    },
    {
        "title": "Area-Selective ALD",
        "bullets": [
            "Bottom-up patterning alternative to lithography",
            "Inhibitor molecules block growth on non-growth areas",
            "SAM-based and small-molecule inhibitors studied",
            "Selectivity window limited — thermal budget critical",
        ],
        "notes": "Emerging topic; reference recent review papers from 2020–2023.",
        "source_papers": [],
    },
    {
        "title": "Conclusion and Future Outlook",
        "bullets": [
            "ALD is indispensable for advanced semiconductor manufacturing",
            "Area-selective ALD and new precursors are key research frontiers",
            "Integration with ALE for sub-angstrom process control",
            "Data-driven precursor design accelerating development",
        ],
        "notes": "End with an open question to the audience.",
        "source_papers": [],
    },
    {
        "title": "References",
        "bullets": [
            "George S.M. (2010) Chem. Rev. 110, 111–131",
            "Knoops H.C.M. et al. (2019) J. Electrochem. Soc.",
            "Miikkulainen V. et al. (2013) J. Appl. Phys. 113",
            "Ritala M. & Leskelä M. (2000) Nanotechnology 11",
        ],
        "notes": "Full reference list for follow-up reading.",
        "source_papers": [],
    },
]

MOCK_SECTION_TEXT = (
    "Atomic layer deposition (ALD) has emerged as a critical thin-film deposition technique "
    "for advanced semiconductor manufacturing (George, 2010). The process relies on sequential, "
    "self-limiting surface reactions that enable angstrom-level thickness control and exceptional "
    "conformality on complex three-dimensional structures (Knoops, 2019). Unlike chemical vapour "
    "deposition (CVD), ALD separates reactant pulses with inert gas purge steps, preventing "
    "gas-phase reactions and ensuring that growth proceeds exclusively through surface-mediated "
    "pathways (George, 2010). As device dimensions continue to scale below 5 nm, the demand for "
    "conformal, pinhole-free films has driven widespread adoption of ALD across the semiconductor "
    "industry (Miikkulainen, 2013). Ritala and Leskelä first systematised the nucleation models "
    "that underpin modern ALD process development (Ritala, 2000), and subsequent work has extended "
    "the technique to over 500 material systems including oxides, nitrides, sulfides, and metals."
)

MOCK_CHAT_ANSWER = (
    "Based on the available literature, atomic layer deposition achieves sub-angstrom thickness "
    "control through alternating self-limiting surface reactions (George, 2010). The growth per "
    "cycle (GPC) typically ranges from 0.1 to 1.5 Å per cycle depending on the precursor system "
    "and substrate temperature. Plasma-enhanced variants extend the accessible temperature window "
    "and material set at the cost of potential plasma-induced damage in high-aspect-ratio features "
    "(Knoops, 2019). For high-k dielectric applications such as HfO2 gate oxides, ALD provides "
    "the conformality and composition control required to achieve equivalent oxide "
    "thicknesses below "
    "1 nm (Miikkulainen, 2013). In interconnect metallisation, ALD TaN barriers as thin as 1.5 nm "
    "maintain sufficient Cu diffusion resistance while minimising resistive penalty (Ritala, 2000)."
)

# ── Mock fixtures ─────────────────────────────────────────────────────────────


def make_mock_papers() -> list[Paper]:
    """Create mock Paper objects without DB access."""
    papers_data = [
        {
            "id": "sha256_george_2010",
            "title": "Atomic Layer Deposition: An Overview",
            "authors": json.dumps(["Steven M. George"]),
            "abstract": "ALD is reviewed with emphasis on surface chemistry and applications.",
            "year": 2010,
        },
        {
            "id": "sha256_knoops_2019",
            "title": "Status and Prospects of Plasma-Enhanced ALD",
            "authors": json.dumps(["Harm C. M. Knoops", "Sjoerd E. Potts"]),
            "abstract": (
                "Plasma-enhanced ALD enables low-temperature deposition of advanced materials."
            ),
            "year": 2019,
        },
        {
            "id": "sha256_miikkulainen_2013",
            "title": "Crystallinity of Inorganic Films Grown by ALD",
            "authors": json.dumps(["Ville Miikkulainen", "Markku Leskelä"]),
            "abstract": (
                "Systematic review of crystallinity behaviour in ALD-grown inorganic films."
            ),
            "year": 2013,
        },
        {
            "id": "sha256_ritala_2000",
            "title": "Atomic Layer Epitaxy — A Valuable Tool for Nanotechnology",
            "authors": json.dumps(["Mikko Ritala", "Markku Leskelä"]),
            "abstract": "Early review of ALE/ALD nucleation and growth on nanoscale features.",
            "year": 2000,
        },
    ]
    result = []
    for d in papers_data:
        p = Paper(**d)
        result.append(p)
    return result


def make_mock_chunks(papers: list[Paper]) -> list[Chunk]:
    """Create mock Chunk objects for each paper."""
    chunks = []
    for i, paper in enumerate(papers):
        chunk = Chunk(
            id=f"chunk_{paper.id}_0",
            paper_id=paper.id,
            section_path="1.Introduction",
            content=(
                f"This paper by {paper.parsed_authors[0]} ({paper.year}) addresses "
                f"key aspects of ALD. {paper.abstract or ''}"
            ),
            token_count=80,
            chunk_index=0,
        )
        chunks.append(chunk)
    return chunks


def make_mock_context() -> RetrievedContext:
    papers = make_mock_papers()
    chunks = make_mock_chunks(papers)
    return RetrievedContext(papers=papers, chunks=chunks, total_tokens=320)


# ── Mock LLM functions ────────────────────────────────────────────────────────


def mock_complete_json(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> dict | list:
    """Return different JSON payloads depending on which planner is calling."""
    system_content = ""
    for msg in messages:
        if msg.get("role") == "system":
            system_content = msg.get("content", "")
            break

    if "academic writing assistant" in system_content:
        return MOCK_PAPER_PLAN_JSON
    elif "presentation designer" in system_content:
        return MOCK_SLIDES_JSON
    else:
        # Fallback: return a minimal dict so callers don't crash
        return {"result": "mock"}


def mock_complete(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    use_cache: bool = True,
) -> str:
    """Return realistic academic paragraph text with (Author, Year) citations."""
    return MOCK_SECTION_TEXT


def mock_retrieve_for_query(
    query: str,
    max_papers: int = 20,
    max_tokens: int = 12000,
) -> RetrievedContext:
    """Return a mock RetrievedContext without DB or embedding access."""
    return make_mock_context()


# ── Test functions ────────────────────────────────────────────────────────────


def run_plan_paper(context: RetrievedContext) -> None:
    print("\n--- test_plan_paper ---")

    with patch("scholarforge.generate.planner.complete_json", side_effect=mock_complete_json):
        plan = plan_paper(
            prompt="Write a survey on atomic layer deposition for semiconductor manufacturing.",
            context=context,
        )

    assert plan.title, "PaperPlan.title must not be empty"
    assert len(plan.sections) >= 3, (
        f"Expected >= 3 sections, got {len(plan.sections)}: {[s.heading for s in plan.sections]}"
    )

    sections_with_subsections = [s for s in plan.sections if s.subsections]
    assert sections_with_subsections, "At least one section must have subsections"

    print(f"  title: {plan.title!r}")
    print(f"  sections ({len(plan.sections)}): {[s.heading for s in plan.sections]}")
    for s in sections_with_subsections:
        print(f"  subsections of {s.heading!r}: {[sub.heading for sub in s.subsections]}")

    print("  PASS")


def run_write_paper(context: RetrievedContext) -> None:
    print("\n--- test_write_paper ---")

    # Build a real PaperPlan from the mock JSON (via plan_paper)
    with patch("scholarforge.generate.planner.complete_json", side_effect=mock_complete_json):
        plan = plan_paper(
            prompt="Write a survey on atomic layer deposition.",
            context=context,
        )

    with patch("scholarforge.generate.writer.complete", side_effect=mock_complete):
        markdown = write_paper(plan, context)

    assert markdown, "write_paper returned empty string"
    assert markdown.startswith(f"# {plan.title}"), (
        f"Document should start with '# {plan.title}'; got: {markdown[:100]!r}"
    )

    # Check that all top-level section headings appear in the markdown
    for section in plan.sections:
        heading_pattern = rf"#+ {re.escape(section.heading)}"
        assert re.search(heading_pattern, markdown), (
            f"Section heading {section.heading!r} not found in written paper"
        )

    # Verify citations are present in section bodies
    assert re.search(r"\(\w+,\s*\d{4}\)", markdown), (
        "Written paper should contain at least one (Author, Year) citation"
    )

    print(f"  Document length: {len(markdown)} chars")
    print(f"  Headings verified: {len(plan.sections)} top-level sections")
    print(f"  First 200 chars: {markdown[:200]!r}")
    print("  PASS")


def run_plan_slides(context: RetrievedContext) -> None:
    print("\n--- test_plan_slides ---")

    with patch("scholarforge.generate.planner.complete_json", side_effect=mock_complete_json):
        slides = plan_slides(
            prompt="Atomic layer deposition for semiconductor manufacturing",
            context=context,
            num_slides=10,
        )

    assert isinstance(slides, list), f"plan_slides should return a list, got {type(slides)}"
    assert len(slides) > 0, "Slides list must not be empty"

    for i, slide in enumerate(slides):
        assert "title" in slide, f"Slide {i} missing 'title'"
        assert isinstance(slide["title"], str) and slide["title"], (
            f"Slide {i} 'title' must be a non-empty string"
        )
        assert "bullets" in slide, f"Slide {i} missing 'bullets'"
        assert isinstance(slide["bullets"], list), f"Slide {i} 'bullets' must be a list"
        assert "notes" in slide, f"Slide {i} missing 'notes'"
        assert isinstance(slide["notes"], str), f"Slide {i} 'notes' must be a string"

    print(f"  Slides ({len(slides)}):")
    for s in slides:
        print(f"    - {s['title']!r} ({len(s['bullets'])} bullets)")
    print("  PASS")


def run_chat_once() -> None:
    print("\n--- test_chat_once ---")

    with (
        patch("scholarforge.generate.chat.retrieve_for_query", side_effect=mock_retrieve_for_query),
        patch("scholarforge.generate.chat.complete", side_effect=mock_complete),
    ):
        answer = chat_once(
            "How does ALD achieve sub-angstrom thickness control?",
        )

    assert isinstance(answer, str) and answer, "chat_once must return a non-empty string"

    # Verify the answer references papers with (Author, Year) format
    citation_matches = re.findall(r"\(\w+,\s*\d{4}\)", answer)
    assert citation_matches, (
        f"Answer should contain (Author, Year) citations; got: {answer[:200]!r}"
    )

    print(f"  Answer length: {len(answer)} chars")
    print(f"  Citations found: {citation_matches}")
    print(f"  First 200 chars: {answer[:200]!r}")
    print("  PASS")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("ScholarForge mock generation tests")
    print("=" * 60)

    context = make_mock_context()
    print(f"Mock context: {len(context.papers)} papers, {len(context.chunks)} chunks")

    failures: list[str] = []

    for test_fn, kwargs in [
        (run_plan_paper, {"context": context}),
        (run_write_paper, {"context": context}),
        (run_plan_slides, {"context": context}),
        (run_chat_once, {}),
    ]:
        try:
            test_fn(**kwargs)
        except Exception as exc:
            name = test_fn.__name__
            print(f"  FAIL: {exc}")
            failures.append(f"{name}: {exc}")

    print("\n" + "=" * 60)
    if failures:
        print(f"FAILED ({len(failures)} failures):")
        for f in failures:
            print(f"  - {f}")
        raise SystemExit(1)
    else:
        print("ALL TESTS PASSED")
