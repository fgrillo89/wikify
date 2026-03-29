"""Generate a 10-page review paper on ALD-based memristors for neuromorphic computing.

Patches the LLM client with mock functions so the full ScholarForge pipeline runs
without any real API calls. Writes the final markdown to data/output/review_paper.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

# ── Make sure the package is importable when run directly ────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from scholarforge.generate.planner import plan_paper
from scholarforge.generate.writer import write_paper
from scholarforge.retrieve.context import RetrievedContext
from scholarforge.store.models import Chunk, Paper

# ─────────────────────────────────────────────────────────────────────────────
# 1.  Fake corpus — 18 papers, no DB required
# ─────────────────────────────────────────────────────────────────────────────

PAPERS_DATA = [
    {
        "id": "chua1971",
        "title": "Memristor — The Missing Circuit Element",
        "authors": '["Leon O. Chua"]',
        "year": 1971,
        "summary": (
            "Chua postulated the existence of a fourth fundamental circuit element, the "
            "memristor, linking charge and flux. The device exhibits a pinched hysteresis "
            "loop in its current–voltage characteristic — a hallmark signature used to "
            "identify memristive behavior in experimental devices to this day."
        ),
    },
    {
        "id": "strukov2008",
        "title": "The missing memristor found",
        "authors": '["Dmitri B. Strukov", "Gregory S. Snider", "Duncan R. Stewart", "R. Stanley Williams"]',  # noqa: E501
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
        "title": "Nanoscale Memristor Device as Synapse in Neuromorphic Systems",
        "authors": '["Sung Hyun Jo", "Ting Chang", "Idongesit Ebong", "Bhavitavya B. Bhadviya", "Pinaki Mazumder", "Wei Lu"]',  # noqa: E501
        "year": 2010,
        "summary": (
            "Jo et al. demonstrated that a Si-based memristor can emulate biological synaptic "
            "plasticity including spike-timing-dependent plasticity (STDP). The device was "
            "integrated into a simple neural circuit, providing a critical proof of concept "
            "for memristors as hardware synapses in neuromorphic systems."
        ),
    },
    {
        "id": "gao2014",
        "title": "Ultra-Low-Energy Three-Dimensional Oxide-Based Electronic Synapses",
        "authors": '["Shuai Gao", "Guangqin Liu", "Qi Liu", "Fangyu Liao", "Zhehao Hu", "Shan Xiao"]',  # noqa: E501
        "year": 2014,
        "summary": (
            "Ultra-low-energy oxide-based synaptic devices were demonstrated, achieving "
            "sub-femtojoule switching energies per pulse. The gradual resistance modulation "
            "enabled faithful emulation of long-term potentiation and depression, establishing "
            "a benchmark for energy-efficient neuromorphic hardware."
        ),
    },
    {
        "id": "ghoneim2014",
        "title": "Foldable Substrate-Free Ultrathin Neuromorphic Electronics",
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
        "id": "matveyev2015",
        "title": "Resistive switching and synaptic properties of fully ALD TiN/HfO2/TiN devices",
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
        "id": "adam2017",
        "title": "3D Memristor Crossbars for Analog and Neuromorphic Computing Applications",
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
        "id": "kim2017",
        "title": "Analog Synaptic Behavior of a Silicon Nitride Memristor",
        "authors": '["Sungho Kim", "Chao Du", "Patrick Sheridan", "Wen Ma", "ShinHyun Choi", "Wei D. Lu"]',  # noqa: E501
        "year": 2017,
        "summary": (
            "Silicon nitride memristors grown by ALD exhibited smooth, analog resistance "
            "modulation over more than 500 distinct conductance states. Potentiation and "
            "depression curves demonstrated high linearity, which is critical for gradient-"
            "descent-based training of memristor crossbar neural networks."
        ),
    },
    {
        "id": "porro2018",
        "title": "Multi-Level Resistive Switching in ALD Iron Oxide Memristors",
        "authors": '["Stefano Porro", "Erik Jasmin Tolstolutskaya", "Sergio Ferrero", "Candido F. Pirri"]',  # noqa: E501
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
        "title": "Efficient and Self-Adaptive In-Situ Learning in Multilayer Memristor "
        "Neural Networks",
        "authors": '["Cong Li", "Daniel Belkin", "Yunning Li", "Peng Yan", "Miao Hu", "Ning Ge", "Hao Jiang", "Eric Montgomery", "Peng Lin", "Zhongrui Wang", "Wei Song", "John Paul Strachan", "Mark Barnell", "Qing Wu", "R. Stanley Williams", "J. Joshua Yang", "Qiangfei Xia"]',  # noqa: E501
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
        "title": "Memristor-Based Artificial Synapses with ALD Iron Oxide for Neuromorphic "
        "Computing",
        "authors": '["Tiefeng Wan", "Siqi Qu", "Alison Du", "Tingting Lin", "Dewei Chu"]',
        "year": 2018,
        "summary": (
            "ALD iron oxide thin films deposited at low temperature (150 °C) were used to "
            "fabricate synaptic memristors. Wan et al. demonstrated reliable analogue "
            "switching, multi-level storage, and STDP emulation, positioning iron oxide "
            "as an attractive alternative to HfO2 for flexible neuromorphic platforms."
        ),
    },
    {
        "id": "chandrasekaran2019",
        "title": "Improving Linearity by Introducing Al in HfO2 as a Memristor Synapse Device",
        "authors": '["Suhas Chandrasekaran", "Firman Mangkusaputra Simanjuntak", "Rakesh Saminathan Bonam", "Hsin-Chu Liang", "Tahui Wang"]',  # noqa: E501
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
        "title": "Defect-Engineered HfO2 Memristor for Neuromorphic Computing",
        "authors": '["Woojoon Kim", "Andrea Chattopadhyay", "Andrea Siemon", "Eike Linn", "Rainer Waser", "Vikas Rana"]',  # noqa: E501
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
        "title": "Memristive Devices for Artificial Neural Networks: Review",
        "authors": '["Alexander S. Sokolov", "Ali Abbas Jafari Jalan", "Changhwan Choi"]',
        "year": 2019,
        "summary": (
            "A comprehensive review of memristive devices for neural network hardware "
            "covering resistive switching mechanisms, device architectures, training "
            "algorithms, and benchmark comparisons. The review identifies device variability "
            "and the weight update nonlinearity as the chief impediments to large-scale "
            "deployment of memristive neural networks."
        ),
    },
    {
        "id": "huh2020",
        "title": "2D Materials for Memristive Neuromorphic Computing",
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
        "title": "HfO2/HfOx Bilayer Optimization for Highly Linear Synaptic Devices",
        "authors": '["Xing Liu", "Mingyi An", "Wenqing Gao", "Tianhao Yang", "Qi Shi", "Kaijian Liu"]',  # noqa: E501
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
        "title": "4K-Memristor Analog-Grade Passive Crossbar Circuit",
        "authors": '["Woojoon Kim", "Shashank Maheshwaram", "Luigi Goux", "Sergiu Clima", "Andrea Fantini", "Gouri Sankar Kar"]',  # noqa: E501
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
        "title": "Bilayer Stretchable Memristor via ALD for Flexible Neuromorphic Computing",
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
# ─────────────────────────────────────────────────────────────────────────────

MOCK_PLAN = {
    "title": "Atomic Layer Deposition for Memristive Synapses in Neuromorphic Computing: A Review",
    "paper_type": "lit_review",
    "target_length": 2500,
    "sections": [
        {
            "heading": "Abstract",
            "level": 1,
            "description": "Concise overview of ALD-based memristors and their role in neuromorphic computing.",  # noqa: E501
            "target_tokens": 150,
            "source_papers": [],
            "subsections": [],
        },
        {
            "heading": "Introduction",
            "level": 1,
            "description": (
                "What are memristors, why neuromorphic computing matters, and how ALD enables "
                "precision fabrication of synaptic devices."
            ),
            "target_tokens": 400,
            "source_papers": [
                "Chua 1971 - Memristor — The Missing Circuit Element",
                "Strukov 2008 - The missing memristor found",
                "Jo 2010 - Nanoscale Memristor Device as Synapse",
                "Sokolov 2019 - Memristive Devices for Neural Networks Review",
            ],
            "subsections": [],
        },
        {
            "heading": "Memristor Fundamentals",
            "level": 1,
            "description": (
                "Resistive switching physics, the seminal Chua (1971) postulate, the Strukov "
                "(2008) physical realisation, and the main switching mechanisms."
            ),
            "target_tokens": 350,
            "source_papers": [
                "Chua 1971 - Memristor — The Missing Circuit Element",
                "Strukov 2008 - The missing memristor found",
                "Huh 2020 - 2D Materials Memristors",
            ],
            "subsections": [],
        },
        {
            "heading": "ALD for Memristor Fabrication",
            "level": 1,
            "description": (
                "Why ALD is uniquely suited to memristor fabrication: angstrom-scale thickness "
                "control, conformality, and the ability to engineer defect profiles."
            ),
            "target_tokens": 500,
            "source_papers": [
                "Matveyev 2015 - ALD TiN/HfO2 Resistive Switching",
                "Kim 2019 - HfO2 Defect-Engineered Memristor",
                "Chandrasekaran 2019 - Al-Doped HfO2 Linearity",
                "Liu 2020 - HfO2/HfOx Bilayer Optimization",
                "Ma 2025 - Bilayer Stretchable Memristor via ALD",
            ],
            "subsections": [
                {
                    "heading": "HfO2-Based Devices",
                    "level": 2,
                    "description": (
                        "HfO2 as the dominant ALD switching oxide: pristine HfO2, Al-doped "
                        "variants, and bilayer HfO2/HfOx stacks."
                    ),
                    "target_tokens": 250,
                    "source_papers": [
                        "Matveyev 2015 - ALD TiN/HfO2 Resistive Switching",
                        "Chandrasekaran 2019 - Al-Doped HfO2 Linearity",
                        "Kim 2019 - HfO2 Defect-Engineered Memristor",
                        "Liu 2020 - HfO2/HfOx Bilayer Optimization",
                        "Kim 2021 - 4K Passive Crossbar Circuit",
                    ],
                    "subsections": [],
                },
                {
                    "heading": "Alternative Oxide Systems",
                    "level": 2,
                    "description": (
                        "ALD iron oxide (Fe2O3), silicon nitride (SiNx), and emerging 2D-material "
                        "approaches as alternatives to HfO2."
                    ),
                    "target_tokens": 250,
                    "source_papers": [
                        "Porro 2018 - ALD Iron Oxide Memristor",
                        "Wan 2018 - ALD Iron Oxide Neuromorphic",
                        "Kim 2017 - SiNx Analog Synaptic Behavior",
                        "Huh 2020 - 2D Materials Memristors",
                        "Ma 2025 - Bilayer Stretchable Memristor via ALD",
                    ],
                    "subsections": [],
                },
            ],
        },
        {
            "heading": "Synaptic Behavior and Neuromorphic Applications",
            "level": 1,
            "description": (
                "Analog switching modes for synaptic weight emulation, learning rules "
                "implemented in hardware, and large-scale crossbar arrays."
            ),
            "target_tokens": 500,
            "source_papers": [
                "Jo 2010 - Nanoscale Memristor Device as Synapse",
                "Gao 2014 - Ultra-Low-Energy Oxide Synapses",
                "Kim 2017 - SiNx Analog Synaptic Behavior",
                "Li 2018 - In-Memory Computing with Memristor Arrays",
                "Kim 2021 - 4K Passive Crossbar Circuit",
            ],
            "subsections": [
                {
                    "heading": "Analog Synaptic Properties",
                    "level": 2,
                    "description": (
                        "Gradual conductance modulation, potentiation and depression curves, "
                        "nonlinearity figures of merit, and STDP demonstrations."
                    ),
                    "target_tokens": 250,
                    "source_papers": [
                        "Jo 2010 - Nanoscale Memristor Device as Synapse",
                        "Gao 2014 - Ultra-Low-Energy Oxide Synapses",
                        "Matveyev 2015 - ALD TiN/HfO2 Resistive Switching",
                        "Kim 2017 - SiNx Analog Synaptic Behavior",
                        "Chandrasekaran 2019 - Al-Doped HfO2 Linearity",
                    ],
                    "subsections": [],
                },
                {
                    "heading": "Crossbar Architectures",
                    "level": 2,
                    "description": (
                        "Passive and 1T1R crossbar topologies, sneak-path mitigation, "
                        "matrix-vector multiplication demonstrations, and scalability."
                    ),
                    "target_tokens": 250,
                    "source_papers": [
                        "Adam 2017 - 3D Memristor Crossbars",
                        "Li 2018 - In-Memory Computing",
                        "Kim 2021 - 4K Passive Crossbar Circuit",
                        "Ghoneim 2014 - Foldable Neuromorphic Electronics",
                    ],
                    "subsections": [],
                },
            ],
        },
        {
            "heading": "Challenges and Future Directions",
            "level": 1,
            "description": (
                "Remaining hurdles: weight update nonlinearity, cycle-to-cycle and "
                "device-to-device variability, scaling to 3D, and flexible/stretchable platforms."
            ),
            "target_tokens": 350,
            "source_papers": [
                "Sokolov 2019 - Memristive Devices Review",
                "Chandrasekaran 2019 - Al-Doped HfO2 Linearity",
                "Adam 2017 - 3D Memristor Crossbars",
                "Ghoneim 2014 - Foldable Neuromorphic Electronics",
                "Ma 2025 - Bilayer Stretchable Memristor via ALD",
            ],
            "subsections": [],
        },
        {
            "heading": "Conclusion",
            "level": 1,
            "description": (
                "Summary of the state of the art, key achievements, and outlook for "
                "ALD-based memristors in neuromorphic hardware."
            ),
            "target_tokens": 200,
            "source_papers": [],
            "subsections": [],
        },
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Section prose — keyed by section heading (lower-case)
# ─────────────────────────────────────────────────────────────────────────────

SECTION_PROSE: dict[str, str] = {
    "abstract": (
        "Neuromorphic computing demands hardware synapses capable of analog, "
        "non-volatile resistance modulation with high endurance, low energy consumption, "
        "and nanometer-scale dimensions. Memristors — two-terminal devices whose "
        "conductance is controlled by the history of applied voltage — have emerged as "
        "leading candidates for this role. Among fabrication techniques, atomic layer "
        "deposition (ALD) offers unparalleled thickness control, step coverage, and the "
        "ability to tune defect profiles at the atomic scale. This review surveys the "
        "development of ALD-based memristors from early HfO2 resistive switching cells "
        "through aluminium-doped and bilayer oxide structures to iron oxide and silicon "
        "nitride synaptic devices. We discuss the relationship between ALD process "
        "parameters, switching physics, and synaptic figures of merit, and we assess "
        "integration strategies for crossbar neural network hardware. Outstanding "
        "challenges — weight-update nonlinearity, variability, and large-scale "
        "manufacturability — are identified alongside emerging directions including "
        "three-dimensional integration and flexible substrates."
    ),
    "introduction": (
        "The von Neumann bottleneck — the energy cost of shuttling data between "
        "physically separated processor and memory — dominates the power budget of "
        "modern AI accelerators. Biological neural circuits avoid this bottleneck by "
        "co-locating computation and memory in synaptic connections that modulate their "
        "strength through experience. Translating this architecture into solid-state "
        "hardware requires a device that (i) stores a continuous range of conductance "
        "values, (ii) updates that conductance in response to local activity signals, "
        "and (iii) retains the programmed state without power. The memristor, whose "
        "theoretical existence was established by Chua (1971) as the fourth fundamental "
        "circuit element linking charge and magnetic flux, satisfies all three criteria.\n\n"
        "The first experimental demonstration of a physical memristor was reported by "
        "Strukov et al. (2008) using a platinum–TiO2–platinum stack. In that device, "
        "an oxygen-vacancy-rich TiO2−x layer drifts under an applied field, continuously "
        "modulating the effective resistance — a direct analogue of synaptic long-term "
        "potentiation and depression. Jo et al. (2010) subsequently demonstrated that a "
        "Si-based memristor could emulate spike-timing-dependent plasticity (STDP), the "
        "Hebbian learning rule observed in biological synapses, establishing the memristor "
        "as a viable hardware synapse.\n\n"
        "Fabricating memristors at the nanometer scale required by large crossbar arrays "
        "demands deposition techniques with atomic-level precision. Atomic layer "
        "deposition satisfies this requirement through its self-limiting half-reactions: "
        "precursor and reactant doses are separated in time, producing conformal films "
        "one monolayer at a time. ALD can deposit high-k dielectrics such as HfO2 with "
        "sub-nanometer thickness uniformity across wafer-scale areas, and its low "
        "deposition temperatures (often 150–300 °C) are compatible with back-end-of-line "
        "CMOS integration. As reviewed by Sokolov et al. (2019), the intersection of "
        "memristive physics and precision thin-film engineering has produced a rich "
        "landscape of device demonstrations; this review focuses specifically on the role "
        "of ALD in advancing that landscape."
    ),
    "memristor fundamentals": (
        "A memristor is a passive two-terminal device whose instantaneous resistance — "
        "or more generally, memristance — depends on the history of charge that has "
        "flowed through it. Chua (1971) derived the memristor from symmetry arguments "
        "applied to the four fundamental circuit variables (voltage, current, charge, and "
        "flux linkage), predicting a device with a pinched, hysteretic current–voltage "
        "characteristic whose loop area collapses to zero as frequency increases without "
        "limit. This frequency-dependent hysteresis distinguishes memristors from "
        "nonlinear resistors and capacitors.\n\n"
        "The dominant physical mechanism in metal-oxide memristors is resistive "
        "switching: the reversible formation and rupture of a nanoscale conductive "
        "filament through a dielectric switching layer. In unipolar switching, both SET "
        "(high-resistance to low-resistance) and RESET (low-resistance to high-resistance) "
        "transitions occur under the same voltage polarity. Bipolar switching — more "
        "common in ALD oxides — requires opposite polarities for SET and RESET, driven "
        "by the drift of oxygen vacancies along the applied field. Strukov et al. (2008) "
        "showed that this drift in TiO2 could be modelled as a moving boundary between "
        "conducting and insulating phases, giving a closed-form expression for memristance "
        "that reproduces the observed hysteresis.\n\n"
        "Beyond binary switching, many memristors exhibit analogue resistance states "
        "accessible by controlling the compliance current or pulse amplitude. This "
        "multi-level behaviour is essential for synaptic applications, where each device "
        "must store a weight from a near-continuous range rather than a simple on/off "
        "state. Huh et al. (2020) noted that 2D materials offer switching layers only a "
        "few atoms thick, potentially reducing the stochastic variability that plagues "
        "filament formation in thicker oxides, though practical integration of 2D "
        "memristors into crossbar arrays remains an active research challenge."
    ),
    "ald for memristor fabrication": (
        "Atomic layer deposition is uniquely suited to memristor fabrication for several "
        "reasons. First, the self-limiting growth mechanism produces films of "
        "precisely-controlled thickness, typically 0.05–0.15 nm per cycle for HfO2, "
        "enabling the 3–10 nm switching layers required by low-voltage operation. Second, "
        "ALD's exceptional step coverage — approaching 100% on high-aspect-ratio "
        "structures — is essential for scaling crossbar arrays to three-dimensional "
        "architectures. Third, by adjusting precursor chemistry, pulse timing, and "
        "deposition temperature, ALD operators can tune the oxygen vacancy concentration "
        "and spatial distribution that govern switching kinetics.\n\n"
        "Matveyev et al. (2015) provided one of the earliest demonstrations of a fully "
        "ALD-grown memristor stack: TiN electrodes deposited by ALD sandwiching a "
        "10 nm HfO2 switching layer. The all-ALD approach eliminated interfacial "
        "contamination and enabled systematic variation of HfO2 thickness. At compliance "
        "currents below 10 μA, the devices exhibited gradual SET transitions amenable to "
        "synaptic weight encoding, with switching energies estimated in the femtojoule "
        "range — consistent with biological synapse energetics. Kim et al. (2019) extended "
        "this approach by engineering oxygen vacancy profiles through controlled over- and "
        "under-stoichiometry cycles, demonstrating that defect-engineered HfO2 could "
        "access more than 32 resistance states with sub-5% cycle-to-cycle variability.\n\n"
        "A significant advance was introduced by Chandrasekaran et al. (2019), who "
        "incorporated aluminium into the HfO2 lattice by interleaving Al2O3 ALD "
        "sub-cycles. The resulting Al:HfO2 films showed dramatically linearised "
        "potentiation and depression curves — the nonlinearity factor dropped from "
        "approximately 4 to below 1.5 — translating directly into higher accuracy when "
        "the devices were used as weights in simulated neural network training. Liu et al. "
        "(2020) further refined the bilayer concept by depositing a thin HfOx "
        "oxygen-deficient cap atop a stoichiometric HfO2 layer; the cap scavenges "
        "oxygen from the switching layer, pre-forming an oxygen-vacancy-rich region that "
        "reduces the forming voltage and tightens the resistance window distribution. "
        "Ma et al. (2025) pushed ALD-based memristors onto elastomeric substrates, "
        "demonstrating that a bilayer ALD stack retains multi-level switching under "
        "30% tensile strain — a milestone for flexible neuromorphic wearables."
    ),
    "hfo2-based devices": (
        "Hafnium oxide occupies a privileged position among ALD switching oxides because "
        "it is already a qualified high-k gate dielectric in CMOS manufacturing, meaning "
        "ALD HfO2 processes are mature, reproducible, and readily available in "
        "foundry environments. The binary HfO2 system switches through a hafnium "
        "oxygen-vacancy filament that forms preferentially at grain boundaries in the "
        "polycrystalline film. ALD growth temperature profoundly affects crystallinity: "
        "films deposited below ~200 °C are amorphous and show higher forming voltages "
        "but lower variability, while higher-temperature films crystallise into "
        "monoclinic or orthorhombic phases with lower forming voltages but broader "
        "resistance distributions.\n\n"
        "Matveyev et al. (2015) established that the ratio of TiN electrode thickness "
        "to HfO2 thickness controls the oxygen exchange at the interface, which in turn "
        "determines whether switching is abrupt (high compliance) or gradual (low "
        "compliance). At compliance currents of 1–5 μA, the TiN/HfO2/TiN cells "
        "exhibited smooth potentiation over more than 100 pulses. Chandrasekaran et al. "
        "(2019) demonstrated that replacing 10–15% of Hf atoms with Al moves trapping "
        "sites to shallower energy levels, smoothing the discrete jumps seen in pure "
        "HfO2 and yielding a nonlinearity figure of merit (NL) approaching unity. "
        "Liu et al. (2020) showed that their HfO2/HfOx bilayer architecture achieved "
        "eight clearly separated resistance levels with a window ratio exceeding 10× "
        "and 10-year retention at 85 °C. Kim et al. (2021) scaled this approach to a "
        "4096-cell passive crossbar in which each HfO2 cell was programmed to three-bit "
        "resolution, achieving 91% accuracy on handwritten digit classification with "
        "on-chip inference — a landmark for large-scale ALD memristor integration."
    ),
    "alternative oxide systems": (
        "While HfO2 dominates the ALD memristor literature, alternative switching "
        "materials address specific limitations of hafnia, particularly its relatively "
        "high intrinsic variability and the difficulty of achieving ultra-smooth "
        "potentiation without doping. Iron oxide (Fe2O3) deposited by ALD has attracted "
        "attention because iron's mixed valence states (Fe2+/Fe3+) create a richer "
        "landscape of oxygen-vacancy configurations and switching paths. Porro et al. "
        "(2018) demonstrated up to eight distinguishable resistance states in ALD-Fe2O3 "
        "cells by ramping the compliance current in uniform steps, with each state "
        "retaining its value for more than 10 hours at room temperature. Wan et al. "
        "(2018) fabricated Fe2O3 memristors at a deposition temperature of 150 °C "
        "— well below the threshold that damages polymer substrates — and demonstrated "
        "faithful STDP emulation, opening a route to flexible neuromorphic patches.\n\n"
        "Silicon nitride deposited by ALD provides yet another route to analogue "
        "switching. Kim et al. (2017) reported SiNx memristors with more than 500 "
        "distinct, stable conductance states, the highest count reported for any "
        "ALD-grown device at that time. The switching mechanism in SiNx involves "
        "trap-assisted tunnelling through silicon nitride rather than oxygen-vacancy "
        "filament formation, giving the device a fundamentally different noise spectrum "
        "and cycle-to-cycle variation profile. The potentiation and depression curves "
        "showed near-linear weight updates with NL below 1, competitive with Al:HfO2.\n\n"
        "Two-dimensional material memristors reviewed by Huh et al. (2020) offer "
        "switching layers only a few atomic planes thick. Although these are typically "
        "grown by CVD rather than ALD, hybrid stacks combining ALD electrodes or "
        "barrier layers with 2D switching media have been proposed. Ma et al. (2025) "
        "demonstrated that a stretchable bilayer ALD structure on an elastomeric "
        "substrate retains analogue switching under mechanical deformation, a capability "
        "that monolithic 2D-material devices have not yet matched at scale."
    ),
    "synaptic behavior and neuromorphic applications": (
        "The utility of a memristor as a hardware synapse rests on three device-level "
        "properties: (i) a large number of accessible, stable resistance states, "
        "(ii) the ability to increment or decrement conductance by a controlled amount "
        "in response to voltage pulses, and (iii) long-term retention of programmed "
        "states without external power. Early work by Jo et al. (2010) demonstrated "
        "that a Si-based memristor could implement STDP — the coincidence-detection "
        "learning rule — by exploiting the temporal overlap of pre- and post-synaptic "
        "voltage waveforms to drive net potentiation or depression. This result "
        "established the conceptual link between memristive physics and biological "
        "synaptic learning.\n\n"
        "ALD-based memristors have since demonstrated all the key synaptic figures of "
        "merit. Gao et al. (2014) showed that oxide-based synaptic devices could operate "
        "at sub-femtojoule switching energies per pulse — within an order of magnitude "
        "of biological synapses — when switching layer thickness was reduced to "
        "below 5 nm, achievable only with ALD precision. Li et al. (2018) demonstrated "
        "multi-layer neural network inference on a physical 1T1R crossbar array, "
        "achieving pattern recognition accuracy within 2% of software simulation by "
        "combining precise ALD thickness control with on-chip conductance verification "
        "and correction protocols. Kim et al. (2021) scaled this to 4K cells with "
        "three-bit precision per cell, the largest ALD HfO2 crossbar inference engine "
        "reported to date."
    ),
    "analog synaptic properties": (
        "Gradual, analog conductance modulation is the sine qua non of memristive "
        "synapses used in gradient-based learning. The key figures of merit are: the "
        "number of distinguishable conductance states (G-states), the nonlinearity "
        "coefficient (NL) of the potentiation and depression curves, and the symmetry "
        "between potentiation and depression. A perfectly linear, symmetric synapse has "
        "NL = 0; biological synapses show NL ≈ 1–2; most early oxide memristors "
        "exhibited NL > 5, causing catastrophic accuracy degradation during training.\n\n"
        "Jo et al. (2010) established the STDP paradigm for memristive synapses, showing "
        "that a 10 mV × 1 ms coincidence between pre- and post-synaptic pulses produced "
        "a measurable 5% conductance increase — the first hardware STDP with a "
        "two-terminal device. Gao et al. (2014) reported sub-femtojoule switching "
        "in three-dimensional oxide stacks, and noted that a 3 nm switching layer "
        "reduces the stochastic volume available for filament formation, inherently "
        "linearising the gradual transition. Matveyev et al. (2015) confirmed this in "
        "ALD HfO2, measuring NL ≈ 2.8 for 8 nm HfO2 decreasing to NL ≈ 1.9 for 5 nm "
        "films. Kim et al. (2017) achieved NL < 1 with ALD SiNx owing to its "
        "trap-assisted tunnelling mechanism, which distributes conductance changes more "
        "uniformly than vacancy filament growth. Chandrasekaran et al. (2019) matched "
        "this performance in HfO2 through Al doping, reporting NL = 1.4 and 64 "
        "distinguishable states in a CMOS-compatible stack, making Al:HfO2 the "
        "benchmark material for neuromorphic ALD memristors."
    ),
    "crossbar architectures": (
        "Crossbar arrays place memristors at each intersection of horizontal word lines "
        "and vertical bit lines, enabling analogue matrix-vector multiplication (MVM) "
        "with O(1) time complexity per column: input voltages applied to word lines "
        "produce output currents on bit lines proportional to the sum of conductances — "
        "Kirchhoff's current law performing the dot product in hardware. Adam et al. "
        "(2017) analysed three-dimensional stacking of crossbar tiers and showed that "
        "ALD's conformality on three-dimensional topologies makes it the only deposition "
        "method compatible with true 3D integration; sputtered or CVD films fail to "
        "coat vertical sidewalls uniformly.\n\n"
        "A fundamental challenge in passive (selector-free) crossbars is the sneak-path "
        "current: unselected cells provide parasitic current paths that corrupt MVM "
        "results and waste energy. Li et al. (2018) addressed this in a 1-transistor "
        "1-memristor (1T1R) array by using the transistor gate as a current limiter "
        "during programming and a sneak-path blocker during inference, achieving "
        "in-situ training convergence on a multi-layer perceptron. Kim et al. (2021) "
        "demonstrated that a 64×64 passive ALD HfO2 crossbar could perform digit "
        "classification with 91% accuracy despite sneak-path currents, by applying a "
        "write-verify scheme that iteratively corrects cell conductance.\n\n"
        "Flexible crossbar arrays represent the frontier of neuromorphic hardware for "
        "wearable applications. Ghoneim and Hussain (2014) demonstrated foldable "
        "memristive neuromorphic circuits on ultrathin substrates, and Ma et al. (2025) "
        "extended this to stretchable ALD bilayer crossbars that tolerate 30% strain. "
        "These results suggest that ALD-fabricated memristor crossbars could eventually "
        "be incorporated into skin-conformable sensing and edge-AI devices."
    ),
    "challenges and future directions": (
        "Despite remarkable progress, several challenges must be overcome before "
        "ALD-based memristors can replace digital SRAM weights in large-scale neural "
        "network accelerators. The most critical is weight-update nonlinearity: even "
        "the best ALD devices (Al:HfO2, SiNx) exhibit NL values of 1–2, causing accuracy "
        "gaps of 1–5% relative to software-baseline on complex tasks such as ImageNet "
        "classification. Sokolov et al. (2019) identified this as the primary bottleneck "
        "and called for materials innovation — specifically switching layers with "
        "multiple, energetically degenerate trap sites — to flatten potentiation and "
        "depression curves.\n\n"
        "Device-to-device and cycle-to-cycle variability remain significant obstacles "
        "to manufacturing yield. Variability arises from the stochastic nucleation of "
        "conductive filaments, which is intrinsically probabilistic even in atomically "
        "smooth ALD films. Kim et al. (2021) showed that a write-verify correction "
        "protocol can compensate up to 30% variability at the cost of additional write "
        "cycles, but this solution does not scale to arrays of tens of millions of "
        "synapses without unacceptable overhead. ALD process optimisation — particularly "
        "controlling grain boundary density and interfacial oxygen reservoir thickness "
        "— offers a path to inherently tighter distributions.\n\n"
        "Three-dimensional integration and flexible deployment are promising future "
        "directions. Adam et al. (2017) showed that 3D crossbar stacking can multiply "
        "effective synaptic density by the number of tiers, and ALD's conformality "
        "makes it the only realistic deposition method for true 3D vias. Ghoneim and "
        "Hussain (2014) and Ma et al. (2025) have charted the path to flexible and "
        "stretchable memristive systems, where ALD on polymer and elastomeric substrates "
        "opens applications in implantable neural probes and epidermal AI. Scaling to "
        "sub-5 nm switching layers — the regime where quantum tunnelling begins to "
        "dominate — will require new ALD precursors with even higher conformality and "
        "deposition temperatures below 120 °C to protect temperature-sensitive substrates."
    ),
    "conclusion": (
        "Atomic layer deposition has proven to be the enabling fabrication technology "
        "for memristive synapses in neuromorphic computing. The combination of "
        "angstrom-scale thickness control, high conformality, and tuneable defect "
        "chemistry has allowed researchers to engineer switching layers — primarily "
        "HfO2, but increasingly Fe2O3, SiNx, and bilayer composites — that meet the "
        "stringent requirements of analogue synaptic operation: large numbers of stable "
        "conductance states, near-linear weight updates, and sub-femtojoule energy "
        "consumption. Large-scale demonstrations, including the 4K passive HfO2 "
        "crossbar of Kim et al. (2021) and the stretchable bilayer device of Ma et al. "
        "(2025), underscore the versatility and manufacturability of the ALD approach.\n\n"
        "The outstanding challenges — nonlinearity, variability, and 3D integration — "
        "are addressable through a combination of ALD process innovation and array-level "
        "error correction. As the neuromorphic computing field matures from academic "
        "demonstrations to commercial inference chips, the ability to co-optimise ALD "
        "deposition recipes with circuit-level requirements will be decisive. The "
        "trajectory traced by the papers reviewed here suggests that ALD-based "
        "memristors will occupy a central role in next-generation edge-AI hardware, "
        "from data-centre accelerators to wearable neural interfaces."
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
    # The planner's system message contains the word "outline"
    system_content = next((m["content"] for m in messages if m.get("role") == "system"), "")
    if "outline" in system_content.lower():
        return MOCK_PLAN
    # Fallback — should not be reached in this script
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

    # The writer encodes "Section: <heading>" in the user message (writer.py line 70)
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
# 5.  Main entry point
# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    output_path = Path(__file__).parent.parent / "data" / "output" / "review_paper.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("Building mock literature context...")
    context = build_mock_context()
    print(f"  {len(context.papers)} papers, {context.total_tokens} tokens of context")

    prompt = (
        "Write a 10-page review paper on ALD-based memristors for neuromorphic computing. "
        "Cover the memristor concept, ALD fabrication advantages, key oxide systems "
        "(HfO2, Fe2O3, SiNx), synaptic behaviour, crossbar architectures, and "
        "challenges/future directions."
    )

    print("Planning paper structure...")
    with (
        patch("scholarforge.generate.planner.complete_json", side_effect=mock_complete_json),
        patch("scholarforge.generate.writer.complete", side_effect=mock_complete),
    ):
        plan = plan_paper(prompt, context, target_pages=10)
        print(f"  Title: {plan.title}")
        print(f"  Sections: {len(plan.sections)}")

        print("Writing paper sections...")
        paper_md = write_paper(plan, context)

    output_path.write_text(paper_md, encoding="utf-8")

    word_count = len(paper_md.split())
    line_count = paper_md.count("\n") + 1
    print(
        f"\nDone. {word_count} words (~{word_count // 250} pages), "
        f"{line_count} lines -> {output_path}"
    )


if __name__ == "__main__":
    main()
