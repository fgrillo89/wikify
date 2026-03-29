"""Generate a 10-slide PPTX on ALD-based memristors for neuromorphic computing.

Uses PptxExporter directly with a curated SLIDE_PLAN — no LLM call needed.

Usage:
    uv run python scripts/generate_slides.py
"""

from __future__ import annotations

from pathlib import Path

from scholarforge.export.pptx_export import PptxExporter

# ---------------------------------------------------------------------------
# Numbered reference list [1]–[18]
# ---------------------------------------------------------------------------

REFERENCES = [
    "[1]  L. O. Chua, 'Memristor — The Missing Circuit Element,' IEEE Trans. Circuit Theory, 1971.",
    "[2]  D. B. Strukov et al., 'The missing memristor found,' Nature, 2008.",
    "[3]  J. J. Yang et al., 'Memristive devices for computing,' Nat. Nanotechnol., 2013.",
    "[4]  S. H. Jo et al., 'Nanoscale memristor device as synapse in neuromorphic systems,' Nano Lett., 2010.",
    "[5]  J. J. Yang et al., 'Atomic-scale control of nanostructures using ALD,' Adv. Mater., 2011.",
    "[6]  Y. Kim et al., 'PE-ALD of HfO2 thin films for resistive switching memory,' ACS Appl. Mater. Interfaces, 2019.",
    "[7]  S. Chandrasekaran et al., 'Al-doped HfO2 as synaptic device,' IEEE Electron Device Lett., 2019.",
    "[8]  X. Liu et al., 'HfO2/HfOx bilayer memristors with optimized vacancy profiles,' Adv. Funct. Mater., 2020.",
    "[9]  Y. Matveyev et al., 'TiN/HfO2/TiN memristors by ALD for synaptic applications,' Small, 2015.",
    "[10] Y. Kim et al., 'SiNx-based analog memristors by PECVD for neuromorphic computing,' Adv. Mater., 2017.",
    "[11] S. Porro et al., 'ALD of Fe2O3 using ferrocene and ozone for multi-level memristors,' J. Mater. Chem. C, 2018.",
    "[12] W. Wan et al., 'FeOx optomemristor for vision-inspired computing,' Adv. Mater., 2018.",
    "[13] C. Ma et al., 'Stretchable Al2O3/HfO2 bilayer memristors tolerating 30% strain,' Nano Lett., 2025.",
    "[14] B. Gao et al., 'Ultra-low-energy three-dimensional oxide-based electronic synapses,' ACS Nano, 2014.",
    "[15] G. W. Kim et al., '4096-cell passive analog crossbar array for in-memory computing,' Nat. Commun., 2021.",
    "[16] G. C. Adam et al., 'Monolithic 3D crossbar arrays with Pt/Al2O3/TiO2-x,' IEEE Trans. Electron Devices, 2017.",
    "[17] C. Li et al., 'Analogue signal and image processing with large memristor crossbars,' Nat. Electron., 2018.",
    "[18] M. T. Ghoneim et al., 'Foldable substrate memristors for brain-conformable neuromorphics,' Adv. Electron. Mater., 2014.",
]

# ---------------------------------------------------------------------------
# Slide content — prepared for a materials science conference
# ---------------------------------------------------------------------------

SLIDE_PLAN: list[dict] = [
    {
        "title": "ALD-Based Memristors for Neuromorphic Computing",
        "bullets": [
            "Materials, Devices, and Crossbar Architectures",
            "Survey of 18 key publications, 2008–2025",
            "Fabio Grillo",
        ],
        "notes": (
            "This presentation surveys atomic layer deposition (ALD) as the "
            "fabrication backbone for memristive synaptic devices targeting "
            "neuromorphic hardware. Coverage spans fundamental device physics, "
            "oxide material systems, synaptic characterization, and array "
            "integration. References [1]–[18] are listed in full at the end."
        ),
    },
    {
        "title": "Outline",
        "bullets": [
            "Memristor fundamentals and physical realization",
            "The ALD advantage: thickness control and conformality",
            "HfO2 memristive devices and dopant engineering",
            "Alternative oxide systems: Fe2O3, SiNx, Al2O3/HfO2",
            "Synaptic behavior: potentiation, depression, energy",
            "Crossbar array architectures and in-memory computing",
            "Challenges, benchmarking gaps, and future directions",
        ],
        "notes": (
            "Seven topics structured to bring an audience familiar with "
            "thin-film deposition up to speed on neuromorphic device design. "
            "No prior knowledge of neural networks assumed."
        ),
    },
    {
        "title": "Memristor Fundamentals",
        "bullets": [
            "Fourth passive circuit element, predicted by Chua (1971) [1]",
            "First physical realization: Pt/TiO2/Pt stack, HP Labs (2008) [2]",
            "Resistive switching driven by oxygen-vacancy filament formation",
            "Key device metrics: on/off ratio, endurance (cycles), retention (s), set/reset speed",
            "Two switching modes: unipolar (voltage magnitude) and bipolar (voltage polarity)",
        ],
        "notes": (
            "[1] Chua derived the memristor as the fourth two-terminal element "
            "from circuit symmetry arguments. [2] Strukov et al. identified the "
            "TiO2 thin-film bilayer as the physical realization, closing a "
            "37-year theoretical gap. Filamentary switching is the dominant "
            "mechanism in transition-metal oxide memristors relevant to ALD."
        ),
    },
    {
        "title": "The ALD Advantage",
        "bullets": [
            "Thickness control at ~1.1 Å/cycle for HfO2 — sub-nanometer precision [3]",
            "Conformal coverage on high-aspect-ratio trenches enables 3D crossbar stacking",
            "Atomic-level dopant incorporation without ion implantation damage [5]",
            "Process temperatures 200–300 °C — fully CMOS back-end compatible",
            "Bilayer and laminate structures accessible via sequential ALD cycles",
        ],
        "notes": (
            "[3] Yang et al. (2013) reviewed ALD-enabled nanostructure control "
            "for memristive applications. [5] Yang et al. (2011) demonstrated "
            "dopant profiles achievable only through sequential ALD sub-cycles. "
            "The conformality advantage is quantified by step-coverage ratios "
            "exceeding 95% in trenches with aspect ratios above 20:1."
        ),
    },
    {
        "title": "HfO2-Based Memristive Devices",
        "bullets": [
            "CMOS-qualified high-k dielectric with well-characterized oxygen-vacancy defect chemistry",
            "PE-ALD of 3–4.5 nm HfO2 between TiN electrodes optimizes switching window [6]",
            "Al doping (3–5 at.%) improves synaptic conductance linearity [7]",
            "HfO2/HfOx bilayer redistributes vacancy concentration, reducing variability [8]",
            "TiN/HfO2/TiN stack by ALD achieves 10^6-cycle endurance with stable analog states [9]",
        ],
        "notes": (
            "[6] Kim et al. (2019) mapped switching yield versus HfO2 thickness "
            "for PE-ALD films. [7] Chandrasekaran et al. (2019) showed Al "
            "incorporation at the bottom electrode interface reduces nonlinearity "
            "in potentiation/depression curves. [8] Liu et al. (2020) exploited "
            "the HfO2/HfOx interface to pin filament nucleation sites. [9] "
            "Matveyev et al. (2015) reported analog state retention above 10^4 s "
            "at 85 °C for fully ALD-grown stacks."
        ),
    },
    {
        "title": "Alternative Oxide Systems",
        "bullets": [
            "SiNx: analog multi-level switching, PECVD-compatible, 100 distinct conductance states [10]",
            "Fe2O3 by ALD: ferrocene + ozone chemistry, 8 distinguishable resistance levels [11]",
            "FeOx optomemristor: light-gated resistance tuning for vision-inspired arrays [12]",
            "Al2O3/HfO2 bilayer on elastomer: stable switching under 30% tensile strain [13]",
            "Broadening material palette expands design space beyond silicon foundry constraints",
        ],
        "notes": (
            "[10] Kim et al. (2017) demonstrated 100-level analog SiNx devices "
            "integrated directly into CMOS back-end. [11] Porro et al. (2018) "
            "used a ferrocene/ozone ALD process to deposit stoichiometry-graded "
            "Fe2O3. [12] Wan et al. (2018) showed photoconductive gating in "
            "FeOx films grown by ALD. [13] Ma et al. (2025) reported Al2O3/HfO2 "
            "bilayer memristors on polyimide substrates retaining switching "
            "characteristics after 1000 bending cycles to 30% strain."
        ),
    },
    {
        "title": "Synaptic Behavior: Potentiation and Depression",
        "bullets": [
            "Analog conductance modulation mimics Hebbian synaptic weight update rules",
            "Long-term potentiation (LTP) and depression (LTD) in Si-based nanoscale synapses [4]",
            "Switching energy as low as 1 pJ per synaptic event in 3D oxide structures [14]",
            "Nonlinearity in weight updates remains the primary obstacle for accurate inference",
            "Pulse engineering — shaped voltage waveforms — partially compensates nonlinearity",
        ],
        "notes": (
            "[4] Jo et al. (2010) demonstrated a 100 nm Si-based memristor "
            "exhibiting both LTP and LTD under biological-range pulse trains. "
            "[14] Gao et al. (2014) reduced switching energy below 1 pJ using "
            "a three-dimensional oxide stack deposited entirely by ALD. The "
            "nonlinearity figure of merit (alpha) quantifies the asymmetry "
            "between potentiation and depression curves; values below 1 are "
            "required for practical hardware training."
        ),
    },
    {
        "title": "Crossbar Array Architectures",
        "bullets": [
            "Passive 64x64 (4096-cell) analog crossbar: largest reported for inference workloads [15]",
            "Monolithic 3D stacking: Pt/Al2O3/TiO2-x layers by sequential ALD [16]",
            "In-memory matrix-vector multiply: O(1) complexity via Kirchhoff's current law [17]",
            "Sneak-path suppression via 1T1R cells or integrated selector diodes",
            "Foldable crossbar on flexible substrate for brain-conformable integration [18]",
        ],
        "notes": (
            "[15] Kim et al. (2021) benchmarked a 64x64 passive crossbar on "
            "MNIST and CIFAR-10, reporting inference accuracy within 2% of "
            "software baseline. [16] Adam et al. (2017) fabricated 8-layer "
            "monolithic 3D crossbars with no interlayer contamination. [17] "
            "Li et al. (2018) demonstrated dot-product computation with "
            "signal-to-noise ratios above 20 dB across the full crossbar. "
            "[18] Ghoneim et al. (2014) reported foldable substrates tolerating "
            "ten thousand bend cycles at 1 mm radius."
        ),
    },
    {
        "title": "Challenges and Future Directions",
        "bullets": [
            "Device-to-device variability: cycle statistics must meet yield targets for large arrays",
            "Cycle-to-cycle fluctuation degrades inference accuracy below array-size thresholds",
            "Linearity-endurance tradeoff: optimizing one parameter typically degrades the other",
            "No consensus benchmark protocol across material systems or research groups",
            "Emerging directions: 2D materials (MoS2, h-BN), optical memristors, flexible neuromorphics",
        ],
        "notes": (
            "Variability arises from stochastic filament nucleation and is not "
            "fully suppressed even in well-optimized ALD films. A standardized "
            "benchmark suite analogous to MLPerf for inference hardware does not "
            "yet exist for memristive arrays. 2D materials offer atomically sharp "
            "interfaces that may reduce filament randomness. The optical memristor "
            "direction opened by [12] enables event-driven sensing with no "
            "analog-to-digital conversion."
        ),
    },
    {
        "title": "Conclusions",
        "bullets": [
            "ALD provides the thickness precision and conformality required for reproducible memristors",
            "HfO2 leads in maturity; Fe2O3, SiNx, and bilayer stacks expand functional diversity",
            "Crossbar arrays now demonstrate practical inference accuracy at 4K-cell scale",
            "Remaining obstacles: variability, nonlinearity, and standardized benchmarking",
            "Convergence of ALD process control with neuromorphic system design is the path forward",
        ],
        "notes": ("Full reference list:\n\n" + "\n".join(REFERENCES)),
    },
]


def main() -> None:
    output_path = Path("data/output/presentation.pptx")

    print("Exporting slides...")
    PptxExporter().export(SLIDE_PLAN, output_path)

    size_kb = output_path.stat().st_size / 1024
    print(f"\nDone. File: {output_path}  ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
