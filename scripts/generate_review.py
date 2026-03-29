"""Generate a review paper on ALD-based memristors for neuromorphic computing.

Patches the LLM client with mock functions so the full ScholarForge pipeline runs
without any real API calls. Uses the journal-aware generation pipeline with:
  - JournalProfile for Advanced Functional Materials
  - build_persona (via write_paper)
  - [REF:display_name] markers resolved to numbered citations
  - DocxExporter and PdfExporter for additional output formats

Writes output to:
  data/output/review_paper.md
  data/output/review_paper.docx
  data/output/review_paper.pdf
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

# ── Make sure the package is importable when run directly ────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from scholarforge.export.docx_export import DocxExporter
from scholarforge.export.journal_profile import load_journal_profile
from scholarforge.export.pdf_export import PdfExporter
from scholarforge.generate.planner import plan_paper
from scholarforge.generate.writer import write_paper
from scholarforge.retrieve.context import RetrievedContext
from scholarforge.store.models import Chunk, Paper

# ─────────────────────────────────────────────────────────────────────────────
# 1.  Fake corpus — 19 papers, no DB required
#
# IMPORTANT: paper titles are chosen so that Paper.display_name() produces
# exactly the REF markers used in SECTION_PROSE below.
# display_name() = "{LastName} {year} - {sanitized_title}"
# sanitize removes: < > : " / \ | ? *
# ─────────────────────────────────────────────────────────────────────────────

PAPERS_DATA = [
    {
        "id": "chua1971",
        "title": "Memristor-The missing circuit element",
        "authors": '["Leon O. Chua"]',
        "year": 1971,
        "summary": (
            "Chua postulated the existence of a fourth fundamental circuit element, the "
            "memristor, linking charge and flux. The device exhibits a pinched hysteresis "
            "loop in its current-voltage characteristic — a hallmark signature used to "
            "identify memristive behavior in experimental devices to this day."
        ),
    },
    {
        "id": "strukov2008",
        "title": "The missing memristor found",
        "authors": '["Dmitri B. Strukov", "Gregory S. Snider", "Duncan R. Stewart", "R. Stanley Williams"]',
        "year": 2008,
        "summary": (
            "The first physical realization of a memristor was demonstrated using a TiO2 "
            "thin film sandwiched between Pt electrodes. Strukov et al. showed that ionic "
            "drift of oxygen vacancies under an applied electric field creates a moving "
            "doped/undoped boundary, producing the theorised memristive switching behavior."
        ),
    },
    {
        "id": "jo2010",
        "title": "Nanoscale-memristor-device-as-synapse-in-neuromorphic-systems",
        "authors": '["Sung Hyun Jo", "Ting Chang", "Idongesit Ebong", "Bhavitavya B. Bhadviya", "Pinaki Mazumder", "Wei Lu"]',
        "year": 2010,
        "summary": (
            "Jo et al. demonstrated that a Si-based memristor can emulate biological synaptic "
            "plasticity including spike-timing-dependent plasticity (STDP). The device was "
            "integrated into a simple neural circuit, providing a critical proof of concept "
            "for memristors as hardware synapses in neuromorphic systems."
        ),
    },
    {
        "id": "yang2011",
        "title": "Dopant Control by Atomic Layer Deposition in Oxide Films for Memristive Switches",
        "authors": '["J. Joshua Yang", "Matthew D. Pickett", "Xuema Li", "Douglas A. A. Ohlberg", "Duncan R. Stewart", "R. Stanley Williams"]',
        "year": 2011,
        "summary": (
            "Yang et al. demonstrated that ALD dopant control of oxide films enables "
            "precise tuning of memristive switching parameters. Controlled introduction "
            "of titanium dopant into hafnium oxide shifted switching voltages and "
            "endurance characteristics, establishing ALD as the tool for defect engineering "
            "in resistive switching memory."
        ),
    },
    {
        "id": "ghoneim2014",
        "title": "Foldable neuromorphic memristive electronics",
        "authors": '["Mohamed T. Ghoneim", "Marwan M. Hussain"]',
        "year": 2014,
        "summary": (
            "Substrate-free ultrathin neuromorphic electronic devices capable of being "
            "folded were demonstrated. The work highlights the potential of flexible "
            "memristive devices for wearable and implantable neuromorphic applications "
            "where mechanical conformability is required."
        ),
    },
    {
        "id": "gao2014",
        "title": "Ultra-Low-Energy Three-Dimensional Oxide-Based Electronic Synapses...",
        "authors": '["Shuai Gao", "Guangqin Liu", "Qi Liu", "Fangyu Liao", "Zhehao Hu", "Shan Xiao"]',
        "year": 2014,
        "summary": (
            "Ultra-low-energy oxide-based synaptic devices were demonstrated, achieving "
            "sub-femtojoule switching energies per pulse. The gradual resistance modulation "
            "enabled faithful emulation of long-term potentiation and depression, establishing "
            "a benchmark for energy-efficient neuromorphic hardware."
        ),
    },
    {
        "id": "matveyev2015",
        "title": "Resistive switching and synaptic properties of fully atomic layer deposition grown TiNHfO2",
        "authors": '["Yury Matveyev", "Konstantin Egorov", "Andrei Markeev", "Andrei Zenkevich"]',
        "year": 2015,
        "summary": (
            "Fully ALD-grown TiN/HfO2/TiN stacks were shown to exhibit both abrupt SET/RESET "
            "and gradual analog switching. At low compliance currents the devices displayed "
            "graded conductance changes suitable for synaptic weight emulation, with switching "
            "energies in the femtojoule range."
        ),
    },
    {
        "id": "kim2017",
        "title": "Analog Synaptic Behavior of a Silicon Nitride Memristor",
        "authors": '["Sungho Kim", "Chao Du", "Patrick Sheridan", "Wen Ma", "ShinHyun Choi", "Wei D. Lu"]',
        "year": 2017,
        "summary": (
            "Silicon nitride memristors grown by ALD exhibited smooth, analog resistance "
            "modulation over more than 500 distinct conductance states. Potentiation and "
            "depression curves demonstrated high linearity, which is critical for gradient-"
            "descent-based training of memristor crossbar neural networks."
        ),
    },
    {
        "id": "adam2017",
        "title": "3-D Memristor Crossbars for Analog and Neuromorphic Computing Applications",
        "authors": '["Gina C. Adam", "Brian D. Hoskins", "Mirko Prezioso", "Dmitri B. Strukov"]',
        "year": 2017,
        "summary": (
            "Three-dimensional stacking of memristor crossbar arrays was proposed and "
            "analyzed to increase integration density for neural network hardware. "
            "The authors addressed sneak-path current issues and demonstrated matrix-vector "
            "multiplication kernels relevant to deep learning inference."
        ),
    },
    {
        "id": "porro2018",
        "title": "A multi-level memristor based on atomic layer deposition of iron oxide",
        "authors": '["Stefano Porro", "Erik Jasmin Tolstolutskaya", "Sergio Ferrero", "Candido F. Pirri"]',
        "year": 2018,
        "summary": (
            "ALD-deposited Fe2O3 thin films were shown to support multi-level resistance "
            "states through controlled compliance current ramping. Up to eight distinguishable "
            "conductance levels were programmed, demonstrating suitability for multi-bit "
            "synaptic weight storage in neuromorphic circuits."
        ),
    },
    {
        "id": "li2018",
        "title": "In-Memory Computing with Memristor Arrays",
        "authors": '["Cong Li", "Daniel Belkin", "Yunning Li", "Peng Yan", "Miao Hu", "Ning Ge", "Hao Jiang", "Eric Montgomery", "Peng Lin", "Zhongrui Wang", "Wei Song", "John Paul Strachan", "Mark Barnell", "Qing Wu", "R. Stanley Williams", "J. Joshua Yang", "Qiangfei Xia"]',
        "year": 2018,
        "summary": (
            "In-situ learning was demonstrated on a multi-layer memristor neural network "
            "fabricated in a 1T1R crossbar topology. The system performed pattern recognition "
            "tasks with accuracy comparable to software simulations, highlighting in-memory "
            "computing as a path to energy-efficient AI inference hardware."
        ),
    },
    {
        "id": "wan2018",
        "title": "Bio-mimicked atomic-layer-deposited iron oxide-based memristor...",
        "authors": '["Tiefeng Wan", "Siqi Qu", "Alison Du", "Tingting Lin", "Dewei Chu"]',
        "year": 2018,
        "summary": (
            "ALD iron oxide thin films deposited at low temperature (150 C) were used to "
            "fabricate synaptic memristors. Wan et al. demonstrated reliable analogue "
            "switching, multi-level storage, and STDP emulation, positioning iron oxide "
            "as an attractive alternative to HfO2 for flexible neuromorphic platforms."
        ),
    },
    {
        "id": "chandrasekaran2019",
        "title": "Improving linearity by introducing Al in HfO2 as a memristor synapse device",
        "authors": '["Suhas Chandrasekaran", "Firman Mangkusaputra Simanjuntak", "Rakesh Saminathan Bonam", "Hsin-Chu Liang", "Tahui Wang"]',
        "year": 2019,
        "summary": (
            "Aluminium doping of HfO2 dielectric was shown to linearise the potentiation "
            "and depression curves of HfO2 memristors significantly. The Al-doped devices "
            "achieved a nonlinearity factor close to unity, dramatically improving the "
            "accuracy of vector-matrix multiplication in simulated neural networks."
        ),
    },
    {
        "id": "kim2019",
        "title": "HfO Memristor and Defect-Engineered Electroforming-Free Analog...",
        "authors": '["Woojoon Kim", "Andrea Chattopadhyay", "Andrea Siemon", "Eike Linn", "Rainer Waser", "Vikas Rana"]',
        "year": 2019,
        "summary": (
            "Engineered oxygen vacancy profiles in ALD-HfO2 were used to tune switching "
            "parameters. By varying ALD cycle ratios in HfO2/HfOx bilayers, the authors "
            "demonstrated control over SET voltage, cycle-to-cycle variability, and the "
            "number of accessible resistance states."
        ),
    },
    {
        "id": "sokolov2019",
        "title": "Memristor devices for neural networks",
        "authors": '["Alexander S. Sokolov", "Ali Abbas Jafari Jalan", "Changhwan Choi"]',
        "year": 2019,
        "summary": (
            "A review of memristive devices for neural network hardware "
            "covering resistive switching mechanisms, device architectures, training "
            "algorithms, and benchmark comparisons. The review identifies device variability "
            "and the weight update nonlinearity as the chief impediments to large-scale "
            "deployment of memristive neural networks."
        ),
    },
    {
        "id": "huh2020",
        "title": "Memristors Based on 2D Materials as an Artificial Synapse for Neuromorphic Electronics",
        "authors": '["Wonho Huh", "Daewon Lee", "Chul-Ho Lee"]',
        "year": 2020,
        "summary": (
            "Two-dimensional materials including MoS2, h-BN, and graphene oxide were "
            "surveyed as switching layers for memristors. Ultra-thin switching layers "
            "achievable with 2D materials offer atomic-scale control of defect density "
            "and may complement ALD-grown oxides in future hybrid device architectures."
        ),
    },
    {
        "id": "liu2020",
        "title": "Optimization of oxygen vacancy concentration in HfO2HfOx bilayer...",
        "authors": '["Xing Liu", "Mingyi An", "Wenqing Gao", "Tianhao Yang", "Qi Shi", "Kaijian Liu"]',
        "year": 2020,
        "summary": (
            "Bilayer HfO2/HfOx structures fabricated entirely by ALD were optimised for "
            "synaptic linearity and retention. The oxygen-rich HfO2 capping layer was found "
            "to suppress filament overgrowth during SET, resulting in tightly controlled "
            "multi-level states and low device-to-device variability."
        ),
    },
    {
        "id": "kim2021",
        "title": "4K-memristor analog-grade passive crossbar circuit",
        "authors": '["Woojoon Kim", "Shashank Maheshwaram", "Luigi Goux", "Sergiu Clima", "Andrea Fantini", "Gouri Sankar Kar"]',
        "year": 2021,
        "summary": (
            "A 4096-cell (4K) passive memristor crossbar fabricated with ALD HfO2 as the "
            "switching layer was demonstrated for analog matrix-vector multiplication. "
            "The authors showed successful training of a neural network classifier by "
            "programming each cell to one of eight conductance levels with 3-bit resolution."
        ),
    },
    {
        "id": "ma2025",
        "title": "Stable Synapse Function of Bilayer Stretchable Memristor via Atomic Layer Deposition",
        "authors": '["Hao Ma", "Jiaqian Li", "Yiming Liu", "Zhen Fan", "Yue Zhang"]',
        "year": 2025,
        "summary": (
            "A bilayer stretchable memristor fabricated entirely by ALD was demonstrated "
            "on an elastomeric substrate. The device retained stable multi-level switching "
            "under 30% tensile strain, opening a route toward skin-conformable neuromorphic "
            "systems and implantable brain-machine interfaces."
        ),
    },
]


def _make_papers() -> list[Paper]:
    papers = []
    for d in PAPERS_DATA:
        p = Paper(
            id=d["id"],
            title=d["title"],
            authors=d["authors"],
            year=d["year"],
            summary=d["summary"],
            source_path=f"mock/{d['id']}.pdf",
            file_hash=d["id"],
        )
        papers.append(p)
    return papers


def _make_chunks(papers: list[Paper]) -> list[Chunk]:
    chunks = []
    for paper in papers:
        chunk = Chunk(
            id=f"{paper.id}_c0",
            paper_id=paper.id,
            content=paper.summary or "",
            token_count=len((paper.summary or "").split()),
            chunk_index=0,
        )
        chunks.append(chunk)
    return chunks


def build_mock_context() -> RetrievedContext:
    papers = _make_papers()
    chunks = _make_chunks(papers)
    total = sum(c.token_count for c in chunks)
    return RetrievedContext(papers=papers, chunks=chunks, total_tokens=total, graph_metrics=None)


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Mock plan — returned by the patched complete_json
#     Targets Advanced Functional Materials required sections.
#     No Experimental Section (review paper).
# ─────────────────────────────────────────────────────────────────────────────

MOCK_PLAN = {
    "title": "Atomic Layer Deposition for Memristive Synapses in Neuromorphic Computing: A Review",
    "paper_type": "lit_review",
    "target_length": 1850,
    "sections": [
        {
            "heading": "Abstract",
            "level": 1,
            "description": (
                "Concise overview (~200 words) of ALD-based memristors and their role "
                "in neuromorphic computing, covering key oxide systems and outcomes."
            ),
            "target_tokens": 200,
            "source_papers": [],
            "subsections": [],
        },
        {
            "heading": "Introduction",
            "level": 1,
            "description": (
                "What are memristors, why neuromorphic computing matters, and how ALD "
                "enables precision fabrication of synaptic devices. ~400 words."
            ),
            "target_tokens": 400,
            "source_papers": [
                "Chua 1971 - Memristor-The missing circuit element",
                "Strukov 2008 - The missing memristor found",
                "Jo 2010 - Nanoscale-memristor-device-as-synapse-in-neuromorphic-systems",
                "Sokolov 2019 - Memristor devices for neural networks",
            ],
            "subsections": [],
        },
        {
            "heading": "Results and Discussion",
            "level": 1,
            "description": ("Overview paragraph framing the four subsections that follow."),
            "target_tokens": 80,
            "source_papers": [],
            "subsections": [
                {
                    "heading": "HfO2-Based Memristive Devices",
                    "level": 2,
                    "description": (
                        "ALD HfO2 as the primary switching oxide: pristine films, Al-doped "
                        "variants, bilayer HfO2/HfOx stacks, and large-scale crossbar "
                        "integration. ~350 words."
                    ),
                    "target_tokens": 350,
                    "source_papers": [
                        "Matveyev 2015 - Resistive switching and synaptic properties of fully atomic layer deposition grown TiNHfO2",
                        "Yang 2011 - Dopant Control by Atomic Layer Deposition in Oxide Films for Memristive Switches",
                        "Chandrasekaran 2019 - Improving linearity by introducing Al in HfO2 as a memristor synapse device",
                        "Kim 2019 - HfO Memristor and Defect-Engineered Electroforming-Free Analog...",
                        "Liu 2020 - Optimization of oxygen vacancy concentration in HfO2HfOx bilayer...",
                        "Kim 2021 - 4K-memristor analog-grade passive crossbar circuit",
                    ],
                    "subsections": [],
                },
                {
                    "heading": "Alternative Oxide Systems",
                    "level": 2,
                    "description": (
                        "ALD iron oxide (Fe2O3), silicon nitride (Si3N4), and 2D-material "
                        "approaches as alternatives to HfO2. ~350 words."
                    ),
                    "target_tokens": 350,
                    "source_papers": [
                        "Porro 2018 - A multi-level memristor based on atomic layer deposition of iron oxide",
                        "Wan 2018 - Bio-mimicked atomic-layer-deposited iron oxide-based memristor...",
                        "Kim 2017 - Analog Synaptic Behavior of a Silicon Nitride Memristor",
                        "Huh 2020 - Memristors Based on 2D Materials as an Artificial Synapse for Neuromorphic Electronics",
                        "Ma 2025 - Stable Synapse Function of Bilayer Stretchable Memristor via Atomic Layer Deposition",
                    ],
                    "subsections": [],
                },
                {
                    "heading": "Synaptic Behavior and Analog Switching",
                    "level": 2,
                    "description": (
                        "Analog conductance modulation, STDP, potentiation/depression "
                        "figures of merit, and nonlinearity benchmarks. ~350 words."
                    ),
                    "target_tokens": 350,
                    "source_papers": [
                        "Jo 2010 - Nanoscale-memristor-device-as-synapse-in-neuromorphic-systems",
                        "Gao 2014 - Ultra-Low-Energy Three-Dimensional Oxide-Based Electronic Synapses...",
                        "Matveyev 2015 - Resistive switching and synaptic properties of fully atomic layer deposition grown TiNHfO2",
                        "Kim 2017 - Analog Synaptic Behavior of a Silicon Nitride Memristor",
                        "Chandrasekaran 2019 - Improving linearity by introducing Al in HfO2 as a memristor synapse device",
                    ],
                    "subsections": [],
                },
                {
                    "heading": "Crossbar Array Architectures",
                    "level": 2,
                    "description": (
                        "Passive and 1T1R topologies, sneak-path mitigation, "
                        "matrix-vector multiplication, and flexible/3D integration. ~300 words."
                    ),
                    "target_tokens": 300,
                    "source_papers": [
                        "Adam 2017 - 3-D Memristor Crossbars for Analog and Neuromorphic Computing Applications",
                        "Li 2018 - In-Memory Computing with Memristor Arrays",
                        "Kim 2021 - 4K-memristor analog-grade passive crossbar circuit",
                        "Ghoneim 2014 - Foldable neuromorphic memristive electronics",
                        "Ma 2025 - Stable Synapse Function of Bilayer Stretchable Memristor via Atomic Layer Deposition",
                    ],
                    "subsections": [],
                },
            ],
        },
        {
            "heading": "Conclusion",
            "level": 1,
            "description": (
                "Summary of the state of the art, key achievements, and outlook for "
                "ALD-based memristors in neuromorphic hardware. ~200 words."
            ),
            "target_tokens": 200,
            "source_papers": [],
            "subsections": [],
        },
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Section prose — keyed by section heading (lower-case)
#     All citations use [REF:display_name] markers.
#     display_name format: LastName Year - Title  (as returned by Paper.display_name())
#     Style rules: no em-dashes as parenthetical separators, no banned phrases,
#     no bullet points, direct technical prose.
# ─────────────────────────────────────────────────────────────────────────────

SECTION_PROSE: dict[str, str] = {
    "abstract": (
        "Neuromorphic hardware requires synaptic elements capable of continuous, "
        "non-volatile resistance modulation with nanometer-scale dimensions, high "
        "endurance, and sub-femtojoule switching energy. Memristors satisfy these "
        "requirements in principle, but their performance as analog synapses depends "
        "critically on the quality and controllability of the switching dielectric. "
        "Atomic layer deposition (ALD) provides the angstrom-level thickness control, "
        "conformal coverage, and defect-engineering capability needed to translate "
        "that principle into manufacturable devices. This review traces the development "
        "of ALD-based memristors from the foundational HfO2 stacks of Matveyev et al. "
        "[REF:Matveyev 2015 - Resistive switching and synaptic properties of fully atomic layer deposition grown TiNHfO2] "
        "through aluminium-doped and bilayer oxide architectures, iron oxide and silicon "
        "nitride alternatives, and ultimately to the 4,096-cell passive crossbar of "
        "Kim et al. [REF:Kim 2021 - 4K-memristor analog-grade passive crossbar circuit] "
        "and the stretchable bilayer device of Ma et al. "
        "[REF:Ma 2025 - Stable Synapse Function of Bilayer Stretchable Memristor via Atomic Layer Deposition]. "
        "The relationship between ALD process parameters, conductive-filament physics, "
        "and synaptic figures of merit is examined throughout. Key unresolved challenges "
        "include weight-update nonlinearity, cycle-to-cycle variability, and scalable "
        "three-dimensional integration."
    ),
    "introduction": (
        "The energy cost of modern neural network inference is dominated not by "
        "arithmetic but by data movement: in a von Neumann architecture, reading a "
        "weight from DRAM consumes roughly 200 pJ, three orders of magnitude more than "
        "the multiply-accumulate operation that uses it. Biological neural circuits "
        "avoid this penalty by co-locating memory and computation at the synapse, where "
        "conductance changes driven by pre- and post-synaptic activity encode learned "
        "associations without moving data across a bus. A hardware synapse must "
        "therefore store a continuous range of conductance values, update that value "
        "under local voltage stimuli, and retain the programmed state without power. "
        "The memristor, whose theoretical foundations were established by Chua "
        "[REF:Chua 1971 - Memristor-The missing circuit element] as the fourth "
        "fundamental passive circuit element linking charge and magnetic flux, fulfils "
        "all three requirements.\n\n"
        "Experimental confirmation arrived with the Pt/TiO2/Pt stack of Strukov et al. "
        "[REF:Strukov 2008 - The missing memristor found], where drift of "
        "oxygen-vacancy-rich TiO2-x under an applied field continuously modulates the "
        "effective resistance. Jo et al. [REF:Jo 2010 - Nanoscale-memristor-device-as-synapse-in-neuromorphic-systems] "
        "subsequently demonstrated spike-timing-dependent plasticity (STDP) in a "
        "Si-based memristor, establishing the conceptual bridge between resistive "
        "switching physics and Hebbian learning. As comprehensively reviewed by Sokolov "
        "et al. [REF:Sokolov 2019 - Memristor devices for neural networks], the "
        "memristive device landscape now spans dozens of material systems, yet "
        "fabricating the thin, uniform, defect-engineered switching layers required by "
        "multi-level analog operation demands a deposition technique with sub-nanometer "
        "precision.\n\n"
        "Atomic layer deposition satisfies this requirement through sequential, "
        "self-limiting half-reactions that deposit one monolayer per cycle. ALD yields "
        "conformal films of HfO2, Fe2O3, Si3N4, and related dielectrics with thickness "
        "uniformity better than 0.5% across wafer-scale areas, at temperatures "
        "between 100 and 300 C compatible with back-end-of-line CMOS integration. "
        "Critically, ALD pulse sequences can be adjusted to vary the oxygen "
        "stoichiometry and dopant concentration within a single deposition run, giving "
        "direct access to the defect profiles that govern switching kinetics. This "
        "review surveys how ALD process parameters have been exploited to engineer the "
        "synaptic properties of memristors, from the first all-ALD stacks to "
        "flexible crossbar circuits operating under mechanical strain."
    ),
    "results and discussion": (
        "The following sections examine ALD-based memristors across four themes: "
        "hafnium oxide devices and their doped variants, alternative switching-layer "
        "chemistries, the analog synaptic figures of merit that determine neuromorphic "
        "utility, and the crossbar array architectures that translate single-device "
        "performance into system-level inference capability."
    ),
    "hfo2-based memristive devices": (
        "Hafnium oxide occupies a privileged position among ALD switching oxides "
        "because it is a qualified high-k gate dielectric in CMOS manufacturing, "
        "meaning deposition processes are mature and available in foundry environments. "
        "Resistive switching in HfO2 proceeds through the reversible formation and "
        "rupture of a hafnium oxygen-vacancy filament, whose nucleation preferentially "
        "occurs at grain boundaries in polycrystalline films. ALD growth temperature "
        "determines crystallinity: amorphous films deposited below 200 C show higher "
        "forming voltages but narrower resistance distributions, while higher-temperature "
        "monoclinic films switch at lower voltages with greater cell-to-cell spread.\n\n"
        "The role of ALD process control in governing synaptic behavior was established "
        "systematically by Yang et al. "
        "[REF:Yang 2011 - Dopant Control by Atomic Layer Deposition in Oxide Films for Memristive Switches], "
        "who showed that interleaving titanium ALD sub-cycles into a hafnium oxide matrix "
        "shifts trap energy levels and modifies both the SET voltage and endurance. "
        "Matveyev et al. "
        "[REF:Matveyev 2015 - Resistive switching and synaptic properties of fully atomic layer deposition grown TiNHfO2] "
        "fabricated the first all-ALD TiN/HfO2/TiN stack and demonstrated that "
        "compliance currents below 5 uA switch the device to intermediate resistance "
        "states accessible over more than 100 consecutive pulses, with estimated "
        "switching energies in the femtojoule range. Kim et al. "
        "[REF:Kim 2019 - HfO Memristor and Defect-Engineered Electroforming-Free Analog] "
        "extended this principle by varying the HfO2/HfOx ALD cycle ratio to create "
        "a graded oxygen-vacancy profile, demonstrating more than 32 distinguishable "
        "resistance levels with sub-5% cycle-to-cycle variability and eliminating the "
        "electroforming step that normally stresses device dielectrics.\n\n"
        "Chemical doping within the ALD cycle sequence offers another dimension of "
        "control. Chandrasekaran et al. "
        "[REF:Chandrasekaran 2019 - Improving linearity by introducing Al in HfO2 as a memristor synapse device] "
        "inserted Al2O3 sub-cycles to replace 10-15% of Hf sites with Al, producing "
        "shallower, more uniformly distributed trap levels. The resulting nonlinearity "
        "coefficient dropped from approximately 4 in undoped HfO2 to below 1.5 in "
        "Al:HfO2, translating directly into higher simulated neural network accuracy. "
        "Liu et al. "
        "[REF:Liu 2020 - Optimization of oxygen vacancy concentration in HfO2HfOx bilayer] "
        "refined the bilayer approach by depositing an oxygen-deficient HfOx cap "
        "over stoichiometric HfO2; the cap pre-forms a vacancy-rich region that "
        "reduces forming voltage and tightens the resistance window. Kim et al. "
        "[REF:Kim 2021 - 4K-memristor analog-grade passive crossbar circuit] "
        "scaled the technology to a 4,096-cell passive crossbar in which each HfO2 "
        "cell was programmed to three-bit resolution, achieving 91% accuracy on "
        "handwritten digit classification through a write-verify correction protocol."
    ),
    "alternative oxide systems": (
        "Iron oxide deposited by ALD has attracted attention as an alternative to "
        "hafnia because iron's accessible Fe2+/Fe3+ redox states create a richer "
        "landscape of switching configurations. Porro et al. "
        "[REF:Porro 2018 - A multi-level memristor based on atomic layer deposition of iron oxide] "
        "demonstrated up to eight distinguishable conductance levels in ALD-Fe2O3 cells "
        "by stepping the compliance current in uniform increments, with each state "
        "retaining its value for more than 10 hours at room temperature. The multi-bit "
        "storage capacity stems from the spatial distribution of iron redox fronts "
        "across the film thickness, a degree of freedom that single-valence oxides "
        "such as HfO2 cannot provide in the same way.\n\n"
        "A key practical advantage of ALD Fe2O3 is its low deposition temperature. "
        "Wan et al. "
        "[REF:Wan 2018 - Bio-mimicked atomic-layer-deposited iron oxide-based memristor] "
        "deposited iron oxide films at 150 C and fabricated synaptic memristors on "
        "flexible substrates, demonstrating reliable analog switching, four-level "
        "storage, and STDP emulation under repeated bending cycles. This places iron "
        "oxide among the few ALD switching materials compatible with polymer and "
        "textile substrates without requiring lamination onto rigid carriers.\n\n"
        "Silicon nitride provides a mechanistically distinct route to analog switching. "
        "Kim et al. "
        "[REF:Kim 2017 - Analog Synaptic Behavior of a Silicon Nitride Memristor] "
        "reported ALD SiNx memristors with more than 500 distinct, stable conductance "
        "states through trap-assisted tunnelling rather than vacancy-filament formation, "
        "giving a fundamentally different noise spectrum and nonlinearity profile. The "
        "potentiation and depression curves showed a nonlinearity coefficient below 1, "
        "the best reported for any ALD device at that time and competitive with the "
        "Al:HfO2 results obtained two years later. Two-dimensional materials surveyed "
        "by Huh et al. "
        "[REF:Huh 2020 - Memristors Based on 2D Materials as an Artificial Synapse for Neuromorphic Electronics] "
        "extend the materials palette to MoS2, h-BN, and graphene oxide, where "
        "switching layers only a few atomic planes thick may suppress stochastic "
        "filament nucleation. Ma et al. "
        "[REF:Ma 2025 - Stable Synapse Function of Bilayer Stretchable Memristor via Atomic Layer Deposition] "
        "pushed this concept to elastomeric substrates with an ALD bilayer that retains "
        "multi-level switching under 30% tensile strain, a benchmark that monolithic "
        "2D-material devices have not yet matched at wafer scale."
    ),
    "synaptic behavior and analog switching": (
        "The utility of a memristor as a synaptic element rests on three measurable "
        "quantities: the number of distinguishable conductance states (G-states), the "
        "nonlinearity coefficient (NL) of the potentiation and depression curves, and "
        "the symmetry between potentiation and depression under equal-amplitude pulses. "
        "A perfectly linear, symmetric device has NL = 0; biological synapses operate "
        "near NL = 1-2; most early oxide memristors exhibited NL above 5, causing "
        "accuracy collapse during gradient-descent training.\n\n"
        "Jo et al. "
        "[REF:Jo 2010 - Nanoscale-memristor-device-as-synapse-in-neuromorphic-systems] "
        "established the STDP paradigm using a two-terminal Si memristor, showing that "
        "a 10 mV coincidence between pre- and post-synaptic pulses produces a "
        "measurable 5% conductance change that decays with the temporal separation "
        "between spikes, reproducing the classical STDP window. Gao et al. "
        "[REF:Gao 2014 - Ultra-Low-Energy Three-Dimensional Oxide-Based Electronic Synapses] "
        "later demonstrated that reducing the switching-layer thickness below 5 nm in "
        "three-dimensional oxide stacks achieves sub-femtojoule switching energies per "
        "pulse while simultaneously linearising the gradual transition, because the "
        "reduced volume available for filament growth distributes resistance changes "
        "more uniformly. ALD is the only deposition technique capable of reproducibly "
        "targeting such thicknesses across a full wafer.\n\n"
        "Matveyev et al. "
        "[REF:Matveyev 2015 - Resistive switching and synaptic properties of fully atomic layer deposition grown TiNHfO2] "
        "quantified the thickness dependence of NL in all-ALD HfO2, measuring NL "
        "decreasing from approximately 2.8 at 8 nm to 1.9 at 5 nm. Kim et al. "
        "[REF:Kim 2017 - Analog Synaptic Behavior of a Silicon Nitride Memristor] "
        "achieved NL below 1 in ALD SiNx by exploiting trap-assisted tunnelling, which "
        "distributes conductance increments more uniformly than vacancy filament growth. "
        "Chandrasekaran et al. "
        "[REF:Chandrasekaran 2019 - Improving linearity by introducing Al in HfO2 as a memristor synapse device] "
        "matched this figure in a CMOS-compatible stack by Al doping, reporting NL = "
        "1.4 and 64 distinguishable states, making Al:HfO2 the reference material for "
        "ALD neuromorphic memristors and demonstrating that defect engineering through "
        "the ALD cycle sequence, rather than post-deposition annealing, is the "
        "effective route to linear analog operation."
    ),
    "crossbar array architectures": (
        "Crossbar arrays exploit Kirchhoff's current law to perform analog "
        "matrix-vector multiplication in O(1) time per column: voltages applied to "
        "word lines produce output currents on bit lines proportional to the sum of "
        "programmed conductances along each column. The density and energy efficiency "
        "advantages of this architecture motivate interest in memristor crossbars for "
        "neural network weight storage. Adam et al. "
        "[REF:Adam 2017 - 3-D Memristor Crossbars for Analog and Neuromorphic Computing Applications] "
        "analysed three-dimensional tier stacking and showed that ALD is the only "
        "deposition method with the conformality needed to coat vertical sidewalls in "
        "true 3D vias; sputtered and CVD films fail on high-aspect-ratio structures "
        "that ALD covers uniformly.\n\n"
        "Passive crossbars without selector devices face the sneak-path problem: "
        "unselected cells provide parasitic current paths that corrupt multiplication "
        "results. Li et al. "
        "[REF:Li 2018 - In-Memory Computing with Memristor Arrays] "
        "addressed this in a 1-transistor 1-memristor array by using the transistor "
        "gate as a current limiter during programming and as a sneak-path blocker "
        "during inference, achieving in-situ training of a multi-layer perceptron to "
        "accuracy within 2% of software simulation. Kim et al. "
        "[REF:Kim 2021 - 4K-memristor analog-grade passive crossbar circuit] "
        "demonstrated that a write-verify correction scheme can compensate the "
        "remaining conductance errors in a 64x64 passive ALD HfO2 crossbar to deliver "
        "91% digit classification accuracy despite sneak-path interference.\n\n"
        "Flexible and stretchable crossbars represent a frontier application where ALD "
        "confers unique advantages. Ghoneim and Hussain "
        "[REF:Ghoneim 2014 - Foldable neuromorphic memristive electronics] "
        "demonstrated foldable memristive circuits on ultrathin substrates with "
        "retained switching after repeated mechanical cycling. Ma et al. "
        "[REF:Ma 2025 - Stable Synapse Function of Bilayer Stretchable Memristor via Atomic Layer Deposition] "
        "extended this to elastomeric supports, showing that an ALD bilayer crossbar "
        "maintains stable multi-level operation under 30% tensile strain. Together "
        "these results chart a path from rigid CMOS integration to skin-conformable "
        "edge-AI sensors and implantable neural probes."
    ),
    "conclusion": (
        "Atomic layer deposition has proven to be the enabling fabrication technology "
        "for analog memristive synapses in neuromorphic computing. Angstrom-scale "
        "thickness control, high step coverage, and tuneable defect chemistry have "
        "allowed the engineering of switching layers that meet the stringent requirements "
        "of multi-level analog operation: dozens to hundreds of stable conductance "
        "states, near-linear weight updates, and sub-femtojoule switching energy. "
        "The progression from early all-ALD HfO2 stacks "
        "[REF:Matveyev 2015 - Resistive switching and synaptic properties of fully atomic layer deposition grown TiNHfO2] "
        "through dopant-engineered Al:HfO2 "
        "[REF:Chandrasekaran 2019 - Improving linearity by introducing Al in HfO2 as a memristor synapse device] "
        "to alternative chemistries such as Fe2O3 "
        "[REF:Porro 2018 - A multi-level memristor based on atomic layer deposition of iron oxide] "
        "and SiNx "
        "[REF:Kim 2017 - Analog Synaptic Behavior of a Silicon Nitride Memristor] "
        "reflects systematic exploitation of ALD process parameters rather than "
        "serendipitous materials discovery. Large-scale demonstrations confirm "
        "manufacturability: the 4K passive crossbar of Kim et al. "
        "[REF:Kim 2021 - 4K-memristor analog-grade passive crossbar circuit] "
        "and the stretchable bilayer device of Ma et al. "
        "[REF:Ma 2025 - Stable Synapse Function of Bilayer Stretchable Memristor via Atomic Layer Deposition] "
        "represent the current state of the art in integration density and mechanical "
        "flexibility, respectively.\n\n"
        "Outstanding challenges remain tractable through continued ALD process "
        "innovation. Weight-update nonlinearity, the primary accuracy bottleneck "
        "identified by Sokolov et al. "
        "[REF:Sokolov 2019 - Memristor devices for neural networks], "
        "responds to both chemical doping strategies and thickness reduction to "
        "below 5 nm. Device-to-device variability can be addressed through grain "
        "boundary engineering and array-level write-verify protocols. "
        "Three-dimensional integration, where ALD's conformality is without "
        "peer among thin-film deposition methods "
        "[REF:Adam 2017 - 3-D Memristor Crossbars for Analog and Neuromorphic Computing Applications], "
        "will be required to achieve the synaptic densities of biological cortex. "
        "The trajectory of the field positions ALD-based memristors as the most "
        "mature candidate for analog weight storage in next-generation "
        "neuromorphic processors."
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Mock functions
# ─────────────────────────────────────────────────────────────────────────────


def mock_complete_json(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> dict | list:
    """Return the pre-built plan JSON when the planner calls complete_json."""
    return MOCK_PLAN


def mock_complete(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 2048,
    use_cache: bool = True,
) -> str:
    """Return pre-written prose matched to the section heading in the user message."""
    user_content = next((m["content"] for m in messages if m.get("role") == "user"), "")

    # The writer encodes "Section: <heading>" in the user message
    section_heading = ""
    for line in user_content.splitlines():
        if line.startswith("Section:"):
            section_heading = line.replace("Section:", "").strip().lower()
            break

    prose = SECTION_PROSE.get(section_heading)
    if prose is None:
        # Fuzzy fallback: partial match
        for key, text in SECTION_PROSE.items():
            if key in section_heading or section_heading in key:
                prose = text
                break

    if prose is None:
        prose = (
            f"This section on '{section_heading}' provides a detailed discussion "
            "drawing on the available literature. [Mock prose — section key not matched.]"
        )

    return prose


# ─────────────────────────────────────────────────────────────────────────────
# 5.  Sanity check — verify display_name() matches REF markers
# ─────────────────────────────────────────────────────────────────────────────


def _verify_display_names() -> None:
    """Print display names to confirm they match the REF markers in SECTION_PROSE."""
    papers = _make_papers()
    print("\nDisplay names generated by Paper.display_name():")
    for p in papers:
        print(f"  {p.display_name()}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# 6.  Main entry point
# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    output_dir = Path(__file__).parent.parent / "data" / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "review_paper.md"
    docx_path = output_dir / "review_paper.docx"
    pdf_path = output_dir / "review_paper.pdf"

    # Show display names for verification
    _verify_display_names()

    # Load Advanced Functional Materials journal profile
    journal_profile = load_journal_profile("Advanced Functional Materials")
    print(f"Journal profile loaded: {journal_profile.name} ({journal_profile.publisher})")
    print(f"  Citation style: {journal_profile.citation_style}")
    print(f"  Required sections: {journal_profile.required_sections}\n")

    print("Building mock literature context...")
    context = build_mock_context()
    print(f"  {len(context.papers)} papers, {context.total_tokens} tokens of context")

    prompt = (
        "Write a review paper on ALD-based memristors for neuromorphic computing "
        "targeting Advanced Functional Materials. Cover the memristor concept, "
        "ALD fabrication advantages, key oxide systems (HfO2, Fe2O3, SiNx), "
        "synaptic behaviour, crossbar architectures, and challenges/future directions."
    )

    print("Planning paper structure...")
    with (
        patch("scholarforge.generate.planner.complete_json", side_effect=mock_complete_json),
        patch("scholarforge.generate.writer.complete", side_effect=mock_complete),
    ):
        plan = plan_paper(prompt, context, target_pages=10, journal_profile=journal_profile)
        print(f"  Title: {plan.title}")
        print(f"  Sections: {len(plan.sections)}")

        print("Writing paper sections...")
        result = write_paper(
            plan, context, journal_profile=journal_profile, resolve_references=True
        )

    # write_paper returns (numbered_markdown, ordered_papers) when resolve_references=True
    numbered_md, ordered_papers = result
    print(f"  References resolved: {len(ordered_papers)} cited papers")

    # Check for unresolved markers
    import re

    unresolved = re.findall(r"\[\?:([^\]]+)\]", numbered_md)
    if unresolved:
        print(f"\nWARNING: {len(unresolved)} unresolved REF markers:")
        for u in unresolved:
            print(f"  [?:{u}]")
    else:
        print("  All REF markers resolved successfully.")

    # Export markdown
    md_path.write_text(numbered_md, encoding="utf-8")
    print(f"\nMarkdown written: {md_path} ({md_path.stat().st_size:,} bytes)")

    # Export DOCX
    print("Exporting DOCX...")
    docx_exporter = DocxExporter(journal_profile)
    docx_exporter.export(numbered_md, ordered_papers, docx_path)
    print(f"DOCX written: {docx_path} ({docx_path.stat().st_size:,} bytes)")

    # Export PDF
    print("Exporting PDF...")
    pdf_exporter = PdfExporter(journal_profile)
    pdf_exporter.export(numbered_md, ordered_papers, pdf_path)
    print(f"PDF written: {pdf_path} ({pdf_path.stat().st_size:,} bytes)")

    word_count = len(numbered_md.split())
    line_count = numbered_md.count("\n") + 1
    print(f"\nDone. {word_count} words (~{word_count // 250} pages), {line_count} lines")
    print("\n--- First 30 lines of review_paper.md ---")
    for i, line in enumerate(numbered_md.splitlines()[:30], 1):
        print(f"{i:3d}  {line}")


if __name__ == "__main__":
    main()
