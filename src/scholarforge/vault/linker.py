"""Incremental link detection and creation between vault notes."""

from __future__ import annotations

import re
from collections import defaultdict

from scholarforge.store.models import Paper
from scholarforge.vault.templates import method_note, topic_note
from scholarforge.vault.writer import _paper_display_name, _sanitize_filename, vault_dir

# Keywords that indicate topics/methods in memristor/ALD literature
# These get expanded as we see more papers
TOPIC_KEYWORDS: dict[str, list[str]] = {
    "Memristor": [
        "memristor",
        "memristive",
        "rram",
        "resistive switching",
        "resistive random access",
    ],
    "Neuromorphic Computing": ["neuromorphic", "brain-inspired", "neural network hardware"],
    "Atomic Layer Deposition": ["atomic layer deposition", "ald", "ald-grown"],
    "Synapse": ["synapse", "synaptic", "artificial synapse"],
    "Crossbar Array": ["crossbar", "crossbar array"],
    "HfO2": ["hfo2", "hafnium oxide", "hafnium dioxide", "hfox"],
    "TiO2": ["tio2", "titanium oxide", "titanium dioxide", "tiox"],
    "Al2O3": ["al2o3", "aluminum oxide", "alumina", "alox"],
    "Filamentary Switching": ["filamentary", "conductive filament", "filament formation"],
    "Interface Switching": ["interface type", "interface switching", "non-filamentary"],
    "Ferroelectric": ["ferroelectric", "ferroelectric tunnel junction"],
    "2D Materials": ["2d material", "mos2", "graphene", "van der waals", "mxene"],
    "Flexible Electronics": ["flexible", "stretchable", "wearable"],
    "In-Memory Computing": ["in-memory computing", "compute-in-memory", "processing-in-memory"],
    "Multilevel Storage": ["multilevel", "multi-level", "multibit", "multi-bit"],
    "Oxygen Vacancy": ["oxygen vacancy", "oxygen vacancies", "vo"],
    "Reservoir Computing": ["reservoir computing"],
    "Optoelectronic": ["optoelectronic", "photoelectric", "photosensitive", "light-modulated"],
    "CMOS Compatible": ["cmos compatible", "cmos-compatible", "back-end-of-line", "beol"],
    "Spiking Neural Network": ["spiking", "spike-timing", "stdp"],
}

METHOD_KEYWORDS: dict[str, list[str]] = {
    "Atomic Layer Deposition": ["atomic layer deposition", "ald"],
    "Sputtering": ["sputtering", "sputter"],
    "Pulse Programming": ["pulse programming", "pulse scheme", "pulse width"],
    "DC Sweep": ["dc sweep", "i-v curve", "current-voltage"],
    "Conductance Quantization": ["conductance quantization", "quantized conductance"],
    "Endurance Testing": ["endurance", "cycling", "write/erase cycles"],
    "Retention Testing": ["retention", "data retention"],
    "STDP": ["stdp", "spike-timing-dependent plasticity"],
    "Potentiation/Depression": ["potentiation", "depression", "ltp", "ltd"],
}


def detect_topics(text: str) -> list[str]:
    """Detect topics from text using keyword matching."""
    text_lower = text.lower()
    found = []
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            found.append(topic)
    return found


def detect_methods(text: str) -> list[str]:
    """Detect methods from text using keyword matching."""
    text_lower = text.lower()
    found = []
    for method, keywords in METHOD_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            found.append(method)
    return found


def link_paper(paper: Paper, all_text: str) -> dict[str, list[str]]:
    """Detect topics and methods for a paper. Returns dict of link types."""
    # Combine title + abstract + text for matching
    search_text = f"{paper.title or ''} {paper.abstract or ''} {all_text}"

    topics = detect_topics(search_text)
    methods = detect_methods(search_text)

    return {
        "topics": topics,
        "methods": methods,
    }


def write_topic_notes(topic_papers: dict[str, list[str]]) -> int:
    """Write/update topic notes. Returns count written."""
    vd = vault_dir()
    (vd / "topics").mkdir(parents=True, exist_ok=True)

    count = 0
    for topic_name, papers in topic_papers.items():
        safe_name = _sanitize_filename(topic_name)
        note_path = vd / "topics" / f"{safe_name}.md"

        existing_papers: list[str] = []
        if note_path.exists():
            content = note_path.read_text(encoding="utf-8")
            for line in content.split("\n"):
                m = re.match(r"- \[\[papers/(.+?)\]\]", line)
                if m:
                    existing_papers.append(m.group(1))

        all_papers = list(dict.fromkeys(existing_papers + papers))
        note_content = topic_note(topic_name, all_papers)
        note_path.write_text(note_content, encoding="utf-8")
        count += 1

    return count


def write_method_notes(method_papers: dict[str, list[str]]) -> int:
    """Write/update method notes. Returns count written."""
    vd = vault_dir()
    (vd / "methods").mkdir(parents=True, exist_ok=True)

    count = 0
    for method_name, papers in method_papers.items():
        safe_name = _sanitize_filename(method_name)
        note_path = vd / "methods" / f"{safe_name}.md"

        existing_papers: list[str] = []
        if note_path.exists():
            content = note_path.read_text(encoding="utf-8")
            for line in content.split("\n"):
                m = re.match(r"- \[\[papers/(.+?)\]\]", line)
                if m:
                    existing_papers.append(m.group(1))

        all_papers = list(dict.fromkeys(existing_papers + papers))
        note_content = method_note(method_name, all_papers)
        note_path.write_text(note_content, encoding="utf-8")
        count += 1

    return count


def update_paper_note_links(paper: Paper, topics: list[str], methods: list[str]) -> None:
    """Update a paper note's frontmatter with detected topics and methods."""
    display_name = _paper_display_name(paper)
    safe_name = _sanitize_filename(display_name)
    note_path = vault_dir() / "papers" / f"{safe_name}.md"

    if not note_path.exists():
        return

    content = note_path.read_text(encoding="utf-8")

    # Parse frontmatter
    if not content.startswith("---"):
        return

    parts = content.split("---", 2)
    if len(parts) < 3:
        return

    fm_text = parts[1]
    body = parts[2]

    # Add topic and method links to frontmatter
    additions = ""
    if topics:
        additions += "hasTopic:\n"
        for t in topics:
            additions += f"- '[[topics/{t}]]'\n"
    if methods:
        additions += "uses_method:\n"
        for m in methods:
            additions += f"- '[[methods/{m}]]'\n"

    if additions:
        new_content = f"---\n{fm_text.rstrip()}\n{additions}---{body}"
        note_path.write_text(new_content, encoding="utf-8")


def compute_all_links(
    papers_with_text: list[tuple[Paper, str]],
) -> dict[str, dict[str, list[str]]]:
    """Compute topics and methods for all papers without writing anything.

    Returns {paper_id: {"topics": [...], "methods": [...]}}
    """
    result: dict[str, dict[str, list[str]]] = {}
    for paper, text in papers_with_text:
        links = link_paper(paper, text)
        result[paper.id] = links
    return result


def link_all_papers(papers_with_text: list[tuple[Paper, str]]) -> dict[str, int]:
    """Run linking for all papers. Returns stats."""
    topic_papers: dict[str, list[str]] = defaultdict(list)
    method_papers: dict[str, list[str]] = defaultdict(list)

    per_paper = compute_all_links(papers_with_text)

    for paper, _text in papers_with_text:
        links = per_paper[paper.id]
        display_name = _paper_display_name(paper)

        for topic in links["topics"]:
            topic_papers[topic].append(display_name)
        for method in links["methods"]:
            method_papers[method].append(display_name)

        # Update paper note frontmatter
        update_paper_note_links(paper, links["topics"], links["methods"])

    topics_written = write_topic_notes(topic_papers)
    methods_written = write_method_notes(method_papers)

    return {
        "topics": topics_written,
        "methods": methods_written,
        "papers_linked": len(papers_with_text),
    }
