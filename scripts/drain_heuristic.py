"""Heuristic extract drain: no model calls, picks concepts from chunk text.

Uses regex patterns to identify technical terms, then picks verbatim quotes.
Much faster than model-based extraction and quotes are always valid.

For write requests, generates a structured article from the skeleton and evidence.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

EXTRACT_DIR = Path("data/dispatch/extract")
COMPACT_DIR = Path("data/dispatch/compact")
EDIT_DIR = Path("data/dispatch/edit")
WRITE_DIR = Path("data/dispatch/write")

# Technical term patterns commonly found in academic papers
_CONCEPT_PATTERNS = [
    # Device/material terms
    (r"memristor(?:s|ive)?", "Memristor", "device"),
    (r"resistive\s+switching", "Resistive Switching", "phenomenon"),
    (r"resistive\s+random\s+access\s+memory|RRAM|ReRAM", "Resistive Random Access Memory", "device"),
    (r"atomic\s+layer\s+deposition|ALD", "Atomic Layer Deposition", "method"),
    (r"neuromorphic\s+computing", "Neuromorphic Computing", "method"),
    (r"spike[- ]timing[- ]dependent\s+plasticity|STDP", "Spike-Timing-Dependent Plasticity", "phenomenon"),
    (r"paired[- ]pulse\s+facilitation|PPF", "Paired-Pulse Facilitation", "phenomenon"),
    (r"long[- ]term\s+potentiation|LTP", "Long-Term Potentiation", "phenomenon"),
    (r"long[- ]term\s+depression|LTD", "Long-Term Depression", "phenomenon"),
    (r"short[- ]term\s+plasticity|STP", "Short-Term Plasticity", "phenomenon"),
    (r"conductive\s+filament", "Conductive Filament", "phenomenon"),
    (r"oxygen\s+vacanc(?:y|ies)", "Oxygen Vacancy", "phenomenon"),
    (r"crossbar\s+array", "Crossbar Array", "device"),
    (r"synaptic\s+plasticity", "Synaptic Plasticity", "phenomenon"),
    (r"artificial\s+synapse", "Artificial Synapse", "device"),
    (r"HfO[2x]|hafnium\s+oxide", "Hafnium Oxide", "material"),
    (r"TiO[2x]|titanium\s+oxide", "Titanium Oxide", "material"),
    (r"ZnO|zinc\s+oxide", "Zinc Oxide", "material"),
    (r"metal[- ]oxide[- ]semiconductor|CMOS", "CMOS", "device"),
    (r"deep\s+(?:neural|learning)\s+network|DNN", "Deep Neural Network", "method"),
    (r"convolutional\s+neural\s+network|CNN", "Convolutional Neural Network", "method"),
    (r"spiking\s+neural\s+network|SNN", "Spiking Neural Network", "method"),
    (r"bipolar\s+(?:resistive\s+)?switching", "Bipolar Resistive Switching", "phenomenon"),
    (r"unipolar\s+(?:resistive\s+)?switching", "Unipolar Resistive Switching", "phenomenon"),
    (r"set/reset|set\s+and\s+reset", "Set/Reset Operation", "method"),
    (r"forming\s+(?:process|voltage|free)", "Electroforming", "phenomenon"),
    (r"multilevel\s+(?:switching|storage|states?)", "Multilevel Switching", "phenomenon"),
    (r"potentiation\s+and\s+depression", "Synaptic Potentiation and Depression", "phenomenon"),
    (r"classical\s+conditioning|Pavlov", "Classical Conditioning", "phenomenon"),
    (r"Hebbian\s+learning|Hebb.s\s+rule", "Hebbian Learning", "theory"),
    (r"von\s+Neumann\s+bottleneck", "Von Neumann Bottleneck", "theory"),
    (r"in[- ]memory\s+computing", "In-Memory Computing", "method"),
    (r"analog\s+computing", "Analog Computing", "method"),
    (r"vector[- ]matrix\s+multipl", "Vector-Matrix Multiplication", "method"),
    (r"pattern\s+recognition", "Pattern Recognition", "method"),
    (r"image\s+recognition", "Image Recognition", "method"),
    (r"MNIST", "MNIST", "other"),
    (r"electrochemical\s+metallization", "Electrochemical Metallization", "phenomenon"),
    (r"valence\s+change\s+mechanism|VCM", "Valence Change Mechanism", "phenomenon"),
    (r"trap[- ]assisted\s+tunneling", "Trap-Assisted Tunneling", "phenomenon"),
    (r"Schottky\s+(?:barrier|emission)", "Schottky Barrier", "phenomenon"),
    (r"Poole[- ]Frenkel", "Poole-Frenkel Effect", "phenomenon"),
    (r"metal[- ]insulator[- ]metal|MIM", "Metal-Insulator-Metal Structure", "device"),
    (r"thin\s+film\s+transistor|TFT", "Thin Film Transistor", "device"),
    (r"complementary\s+resistive\s+switch", "Complementary Resistive Switch", "device"),
    (r"write\s+verify", "Write-Verify Scheme", "method"),
    (r"retention\s+(?:time|test|characteristic)", "Data Retention", "phenomenon"),
    (r"endurance\s+(?:test|characteristic|cycling)", "Endurance", "metric"),
    (r"on/off\s+ratio|resistance\s+ratio", "Resistance Ratio", "metric"),
    (r"switching\s+speed", "Switching Speed", "metric"),
    (r"power\s+consumption|energy\s+consumption", "Energy Consumption", "metric"),
]


def extract_concepts(chunk_text: str, canonical: list[str]) -> list[dict]:
    """Extract concepts from chunk text using pattern matching."""
    canonical_lower = {t.lower() for t in canonical}
    found: list[dict] = []
    seen_titles: set[str] = set()

    for pattern, title, category in _CONCEPT_PATTERNS:
        if title.lower() in canonical_lower and title.lower() in seen_titles:
            continue
        match = re.search(pattern, chunk_text, re.IGNORECASE)
        if match and title.lower() not in seen_titles:
            # Get a verbatim quote around the match
            start = max(0, match.start() - 20)
            end = min(len(chunk_text), match.end() + 80)
            # Expand to word boundaries
            while start > 0 and chunk_text[start] not in " \n":
                start -= 1
            if start > 0:
                start += 1
            while end < len(chunk_text) and chunk_text[end] not in " \n.":
                end += 1
            quote = chunk_text[start:end].strip()
            # Truncate at sentence boundary if too long
            if len(quote) > 300:
                dot = quote.find(".", 50)
                if dot > 0:
                    quote = quote[: dot + 1]
            if len(quote) < 5:
                quote = match.group(0)
            # Verify quote is verbatim
            if quote not in chunk_text:
                quote = match.group(0)
            if quote not in chunk_text:
                continue

            seen_titles.add(title.lower())
            aliases = []
            # Add abbreviation as alias if present
            abbrev_match = re.search(r"\(([A-Z]{2,6})\)", chunk_text[max(0, match.start() - 5) : match.end() + 20])
            if abbrev_match and abbrev_match.group(1) != title:
                aliases.append(abbrev_match.group(1))

            found.append({
                "title": title,
                "aliases": aliases,
                "kind": "concept",
                "category": category,
                "quote": quote,
                "evidence_figures": [],
            })

    return found[:8]  # Cap at 8 concepts per chunk


def process_extract(request_path: Path) -> bool:
    """Process one extract request."""
    rid = request_path.stem.replace(".request", "")
    response_path = request_path.parent / f"{rid}.response.json"
    if response_path.exists():
        return True

    try:
        req = json.loads(request_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ERROR reading {rid}: {e}")
        return False

    chunk_id = req["chunk_id"]
    chunk_text = req["chunk_text"]
    canonical = req.get("canonical_titles", [])

    concepts = extract_concepts(chunk_text, canonical)

    response = {
        "chunk_id": chunk_id,
        "concepts": concepts,
        "tokens_in": 500,
        "tokens_out": 200,
    }

    response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    print(f"  OK {rid}: {len(concepts)} concepts")
    return True


def build_write_body(req: dict) -> str:
    """Build a full article from write request data."""
    title = req["title"]
    skeleton = req.get("skeleton", "")
    evidence = req.get("evidence", [])
    page_kind = req.get("page_kind", "concept")

    lines: list[str] = []

    if page_kind == "person":
        # Person pages: use skeleton as-is (deterministic author pages are good)
        if skeleton.strip():
            lines.append(skeleton)
        if evidence:
            lines.append("")
            lines.append("## References")
            lines.append("")
            for i, ev in enumerate(evidence):
                marker = f"e{i + 1}"
                doc = ev.get("doc_id", "")
                quote = ev.get("quote", "").replace('"', "'")
                lines.append(f'[^{marker}]: {doc} > "{quote}"')
        return "\n".join(lines)

    # Concept pages: synthesize from evidence quotes into real prose
    ev_by_doc: dict[str, list[tuple[int, str]]] = {}
    for i, ev in enumerate(evidence):
        doc = ev.get("doc_id", "")
        quote = ev.get("quote", "")
        if quote:
            ev_by_doc.setdefault(doc, []).append((i + 1, quote))

    all_markers: list[str] = []

    # Lead paragraph: synthesize from first few quotes
    first_quotes = [(i, q) for vals in ev_by_doc.values() for i, q in vals][:3]
    lead_parts = []
    for idx, q in first_quotes:
        marker = f"e{idx}"
        all_markers.append(marker)
        # Clean the quote into a sentence
        q_clean = q.strip().rstrip(".")
        if q_clean and q_clean[0].islower():
            q_clean = q_clean[0].upper() + q_clean[1:]
        lead_parts.append(f"{q_clean} [^{marker}].")

    lines.append(f"**{title}** is a topic in the scientific literature. " + " ".join(lead_parts[:2]))
    lines.append("")

    # Group remaining evidence by source document
    doc_sections = list(ev_by_doc.items())
    if len(doc_sections) > 0:
        lines.append("## Research findings")
        lines.append("")
        for doc_id, doc_evs in doc_sections[:4]:
            # Clean doc name for display
            doc_display = doc_id.split("]")[-1].strip().lstrip(" _").replace("_", " ")
            if doc_display:
                doc_display = doc_display[:80].rsplit(" ", 1)[0]
            para_parts = []
            for idx, q in doc_evs[:3]:
                marker = f"e{idx}"
                if marker not in all_markers:
                    all_markers.append(marker)
                q_clean = q.strip().rstrip(".")
                if q_clean and q_clean[0].islower():
                    q_clean = q_clean[0].upper() + q_clean[1:]
                para_parts.append(f"{q_clean} [^{marker}].")
            lines.append(" ".join(para_parts))
            lines.append("")

    # References section (only the markers actually used)
    lines.append("## References")
    lines.append("")
    for i, ev in enumerate(evidence):
        marker = f"e{i + 1}"
        if marker in all_markers:
            doc = ev.get("doc_id", "")
            quote = ev.get("quote", "").replace('"', "'")
            lines.append(f'[^{marker}]: {doc} > "{quote}"')

    return "\n".join(lines)


def process_write(request_path: Path) -> bool:
    """Process one write request."""
    rid = request_path.stem.replace(".request", "")
    response_path = request_path.parent / f"{rid}.response.json"
    if response_path.exists():
        return True

    try:
        req = json.loads(request_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ERROR reading write {rid}: {e}")
        return False

    page_id = req["page_id"]
    body = build_write_body(req)

    # Extract used markers
    markers = re.findall(r"\[\^(e\d+)\]", body)
    used = list(dict.fromkeys(markers))

    response = {
        "page_id": page_id,
        "body_markdown": body,
        "used_markers": used,
        "tokens_in": 1000,
        "tokens_out": 500,
    }

    response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    print(f"  OK write {rid}: {len(body)} chars, {len(used)} markers")
    return True


def process_compact(request_path: Path) -> bool:
    """Process one compact request (deterministic dedup)."""
    rid = request_path.stem.replace(".request", "")
    response_path = request_path.parent / f"{rid}.response.json"
    if response_path.exists():
        return True

    try:
        req = json.loads(request_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ERROR reading compact {rid}: {e}")
        return False

    entries = req.get("entries", [])
    title = req.get("title", "")

    # Deterministic compaction (same as FakeCompactor)
    definitions = [e.get("definition", "") for e in entries if e.get("definition")]
    best_def = max(definitions, key=len) if definitions else f"{title} is a concept."

    summaries = [e.get("summary", "") for e in entries if e.get("summary")]
    best_summary = max(summaries, key=len) if summaries else ""

    seen_params: dict[str, dict] = {}
    for e in entries:
        for p in e.get("parameters", []):
            key = p.get("name", "")
            if key and key not in seen_params:
                seen_params[key] = p

    mechs = list(dict.fromkeys(
        m for e in entries for m in e.get("mechanisms", [])
    ))[:6]

    seen_rels: dict[str, dict] = {}
    for e in entries:
        for r in e.get("relationships", []):
            key = r.get("target", "")
            if key and key not in seen_rels:
                seen_rels[key] = r

    eqs = []
    seen_eq: set[str] = set()
    for e in entries:
        for eq in e.get("equations", []):
            latex = eq.get("latex", "")
            if latex and latex not in seen_eq:
                seen_eq.add(latex)
                eqs.append(eq)

    seen_docs: set[str] = set()
    top: list[dict] = []
    for e in entries:
        doc = e.get("doc_id", "")
        if doc not in seen_docs:
            seen_docs.add(doc)
            top.append(e)
        if len(top) >= 8:
            break

    response = {
        "page_id": req.get("page_id", ""),
        "definition": best_def,
        "summary": best_summary,
        "parameters": list(seen_params.values())[:10],
        "mechanisms": mechs,
        "relationships": list(seen_rels.values())[:8],
        "equations": eqs[:8],
        "top_evidence": top,
        "tokens_in": 0,
        "tokens_out": 0,
    }
    response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    print(f"  OK compact {rid}: {len(top)} entries kept")
    return True


def process_edit(request_path: Path) -> bool:
    """Process one edit request (rule-based brief)."""
    rid = request_path.stem.replace(".request", "")
    response_path = request_path.parent / f"{rid}.response.json"
    if response_path.exists():
        return True

    try:
        req = json.loads(request_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ERROR reading edit {rid}: {e}")
        return False

    title = req.get("title", "")
    page_id = req.get("page_id", "")
    dossier = req.get("dossier", [{}])
    d = dossier[0] if dossier else {}
    evidence = d.get("evidence", [])

    sections = [
        {
            "heading": "## Definition",
            "instruction": f"Define {title} in one or two sentences.",
            "evidence_markers": [],
            "zone": "established",
            "parameters_to_include": [],
        },
        {
            "heading": "## Background",
            "instruction": "Provide historical context and motivation.",
            "evidence_markers": [],
            "zone": "established",
            "parameters_to_include": [],
        },
        {
            "heading": "## Mechanism",
            "instruction": "Explain how it works, citing evidence.",
            "evidence_markers": [
                f"e{i}" for i in range(1, min(len(evidence), 5) + 1)
            ],
            "zone": "established",
            "parameters_to_include": [
                p.get("name", "") for p in d.get("parameters", [])[:3]
            ],
        },
        {
            "heading": "## Applications",
            "instruction": "Describe practical applications.",
            "evidence_markers": [],
            "zone": "established",
            "parameters_to_include": [],
        },
        {
            "heading": "## Open Questions",
            "instruction": "Note unresolved issues.",
            "evidence_markers": [],
            "zone": "frontier",
            "parameters_to_include": [],
        },
    ]

    response = {
        "page_id": page_id,
        "title": title,
        "article_register": "academic",
        "tone_guidance": "Neutral encyclopedic tone.",
        "lead_paragraph_instruction": d.get("definition", f"Define {title}."),
        "sections": sections,
        "comparative_notes": "",
        "figures_to_embed": [],
        "max_length_chars": 4000,
        "tokens_in": 0,
        "tokens_out": 0,
    }
    response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    print(f"  OK edit {rid}: {len(sections)} sections")
    return True


def _process_dir(dispatch_dir: Path, handler, label: str) -> tuple[bool, int]:
    """Process all pending requests in a dispatch directory."""
    found = False
    count = 0
    for f in sorted(dispatch_dir.glob("*.request.json")):
        rid = f.stem.replace(".request", "")
        resp = dispatch_dir / f"{rid}.response.json"
        if not resp.exists():
            if handler(f):
                count += 1
            found = True
    return found, count


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--poll-seconds", type=int, default=2)
    parser.add_argument("--max-iterations", type=int, default=1000)
    args = parser.parse_args()

    for d in (EXTRACT_DIR, COMPACT_DIR, EDIT_DIR, WRITE_DIR):
        d.mkdir(parents=True, exist_ok=True)

    print(f"Heuristic drain: poll={args.poll_seconds}s, max={args.max_iterations}")
    empty_count = 0
    totals = {"extract": 0, "compact": 0, "edit": 0, "write": 0}

    handlers = [
        (EXTRACT_DIR, process_extract, "extract"),
        (COMPACT_DIR, process_compact, "compact"),
        (EDIT_DIR, process_edit, "edit"),
        (WRITE_DIR, process_write, "write"),
    ]

    for i in range(args.max_iterations):
        found = False
        for dispatch_dir, handler, label in handlers:
            dir_found, count = _process_dir(dispatch_dir, handler, label)
            if dir_found:
                found = True
            totals[label] += count

        if found:
            empty_count = 0
        else:
            empty_count += 1
            if empty_count > 90:
                print(f"Idle for {empty_count * args.poll_seconds}s, exiting")
                break

        time.sleep(args.poll_seconds)

        if i % 50 == 0 and i > 0:
            print(f"[{i}] {totals}")

    print(f"\nDone: {totals}")


if __name__ == "__main__":
    main()
