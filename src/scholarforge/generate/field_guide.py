"""Field detection and writing guide loading for domain-specific generation."""

from __future__ import annotations

from pathlib import Path

# Keyword sets for each field.  Order matters: first match wins when scores tie.
_FIELD_KEYWORDS: dict[str, list[str]] = {
    "materials_science": [
        "thin film",
        "ALD",
        "atomic layer deposition",
        "CVD",
        "chemical vapor deposition",
        "PVD",
        "sputtering",
        "nanoparticle",
        "nanomaterial",
        "nanostructure",
        "graphene",
        "2D material",
        "polymer",
        "composite",
        "alloy",
        "ceramic",
        "semiconductor",
        "dielectric",
        "battery",
        "lithium-ion",
        "perovskite",
        "solar cell",
        "photovoltaic",
        "XRD",
        "SEM",
        "TEM",
        "XPS",
        "AFM",
        "Raman",
        "thin-film",
        "coating",
        "deposition",
        "substrate",
        "annealing",
        "crystallinity",
        "microstructure",
        "corrosion",
        "fatigue",
        "fracture",
        "memristor",
        "ferroelectric",
        "piezoelectric",
        "superconductor",
        "bandgap",
        "band gap",
        "lattice",
        "epitaxy",
        "MBE",
    ],
    "computer_science": [
        "neural network",
        "deep learning",
        "machine learning",
        "transformer",
        "attention mechanism",
        "convolutional",
        "CNN",
        "RNN",
        "LSTM",
        "GAN",
        "generative adversarial",
        "reinforcement learning",
        "classification",
        "regression",
        "natural language processing",
        "NLP",
        "computer vision",
        "image recognition",
        "object detection",
        "BERT",
        "GPT",
        "language model",
        "pre-training",
        "fine-tuning",
        "backpropagation",
        "gradient descent",
        "optimizer",
        "benchmark",
        "ImageNet",
        "algorithm",
        "distributed system",
        "software engineering",
        "database",
        "compiler",
        "operating system",
        "cloud computing",
        "robotics",
        "autonomous",
    ],
    "biology": [
        "gene",
        "genome",
        "CRISPR",
        "protein",
        "enzyme",
        "DNA",
        "RNA",
        "mRNA",
        "transcription",
        "translation",
        "expression",
        "gene expression",
        "cell",
        "cellular",
        "mitochondria",
        "ribosome",
        "membrane",
        "signaling pathway",
        "apoptosis",
        "differentiation",
        "stem cell",
        "PCR",
        "sequencing",
        "phylogenetic",
        "evolution",
        "ecology",
        "biodiversity",
        "organism",
        "species",
        "mutation",
        "phenotype",
        "genotype",
        "epigenetic",
        "chromatin",
        "histone",
        "plasmid",
        "vector",
        "transfection",
        "knockout",
        "transgenic",
        "metabolomics",
        "proteomics",
        "bioinformatics",
    ],
    "medicine": [
        "clinical trial",
        "randomized controlled",
        "RCT",
        "patient",
        "diagnosis",
        "treatment",
        "therapy",
        "drug",
        "pharmaceutical",
        "dosage",
        "adverse event",
        "side effect",
        "mortality",
        "morbidity",
        "survival",
        "Kaplan-Meier",
        "hazard ratio",
        "odds ratio",
        "cohort",
        "case-control",
        "epidemiology",
        "prevalence",
        "incidence",
        "screening",
        "biomarker",
        "prognosis",
        "surgical",
        "placebo",
        "double-blind",
        "CONSORT",
        "meta-analysis",
        "systematic review",
        "Cochrane",
        "intention-to-treat",
        "primary endpoint",
        "hospital",
        "ICU",
        "pathology",
        "radiology",
        "oncology",
        "cardiology",
    ],
    "mathematics": [
        "theorem",
        "proof",
        "lemma",
        "conjecture",
        "topology",
        "algebra",
        "manifold",
        "group theory",
        "ring",
        "field theory",
        "number theory",
        "combinatorics",
        "graph theory",
        "differential equation",
        "partial differential",
        "PDE",
        "ODE",
        "Hilbert space",
        "Banach space",
        "measure theory",
        "probability",
        "stochastic",
        "Bayesian",
        "frequentist",
        "estimator",
        "maximum likelihood",
        "regression",
        "hypothesis testing",
        "confidence interval",
        "bootstrap",
        "Monte Carlo",
        "Markov chain",
        "ergodic",
        "convex optimization",
        "linear programming",
        "eigenvalue",
        "matrix decomposition",
        "Fourier",
        "wavelet",
        "information theory",
    ],
    "physics": [
        "quantum",
        "relativity",
        "thermodynamics",
        "entropy",
        "Hamiltonian",
        "Lagrangian",
        "field theory",
        "particle physics",
        "Standard Model",
        "Higgs",
        "boson",
        "fermion",
        "photon",
        "neutron",
        "gravitational",
        "cosmology",
        "dark matter",
        "dark energy",
        "black hole",
        "string theory",
        "condensed matter",
        "superconductivity",
        "magnetism",
        "spin",
        "optics",
        "laser",
        "plasma",
        "fluid dynamics",
        "turbulence",
        "astrophysics",
        "stellar",
        "galaxy",
        "spectroscopy",
        "scattering",
        "diffraction",
        "interferometer",
        "LIGO",
        "Feynman diagram",
        "renormalization",
        "phase transition",
    ],
    "social_sciences": [
        "behavioral economics",
        "prospect theory",
        "cognitive bias",
        "heuristic",
        "decision making",
        "survey",
        "questionnaire",
        "interview",
        "ethnography",
        "qualitative",
        "grounded theory",
        "social capital",
        "inequality",
        "poverty",
        "education",
        "policy",
        "governance",
        "institution",
        "democracy",
        "voting",
        "public opinion",
        "sociology",
        "psychology",
        "self-efficacy",
        "motivation",
        "personality",
        "cognition",
        "perception",
        "attitude",
        "stereotype",
        "discrimination",
        "gender",
        "race",
        "class",
        "stratification",
        "regression discontinuity",
        "difference-in-differences",
        "instrumental variable",
        "panel data",
        "fixed effects",
    ],
}


def _find_fields_dir() -> Path | None:
    """Locate the fields/ directory from multiple candidate paths."""
    candidates = [
        Path(__file__).parent.parent / "prompts" / "fields",
        Path.cwd() / "src" / "scholarforge" / "prompts" / "fields",
    ]
    for p in candidates:
        if p.is_dir():
            return p
    return None


def detect_field(prompt: str, topics: list[str]) -> str:
    """Detect the most relevant field from a writing prompt and corpus topics.

    Returns a field ID like ``'materials_science'``, ``'computer_science'``, etc.
    Falls back to ``'generic'`` when no clear match is found.
    """
    combined = (prompt + " " + " ".join(topics)).lower()

    scores: dict[str, int] = {}
    for field_id, keywords in _FIELD_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.lower() in combined)
        if score > 0:
            scores[field_id] = score

    if not scores:
        return "generic"

    best_field = max(scores, key=lambda f: scores[f])
    # Require at least 2 keyword hits to avoid spurious matches on a single word.
    if scores[best_field] < 2:
        return "generic"
    return best_field


def load_field_guide(field_id: str) -> str:
    """Load the field-specific writing guide markdown file.

    Returns an empty string if the guide file is not found.
    """
    fields_dir = _find_fields_dir()
    if fields_dir is None:
        return ""

    guide_path = fields_dir / f"{field_id}.md"
    if guide_path.exists():
        return guide_path.read_text(encoding="utf-8")
    return ""


def get_field_instructions(prompt: str, topics: list[str]) -> str:
    """Detect field and return field-specific instructions.

    If a specific field is detected, returns only that field's guide
    (the base style guide already covers cross-field best practices).
    If no field is detected, returns the generic guide as fallback.
    """
    field_id = detect_field(prompt, topics)

    if field_id != "generic":
        specific = load_field_guide(field_id)
        if specific:
            return f"\n--- Field Guide: {field_id} ---\n{specific}"

    # Fallback: generic cross-field guide (only when no specific field matched)
    generic = load_field_guide("generic")
    if generic:
        return f"\n--- Cross-Field Best Practices ---\n{generic}"
    return ""
