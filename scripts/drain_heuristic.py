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
    neighbors = req.get("neighbor_titles", [])

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

    # Concept pages: build from evidence quotes
    # Lead paragraph
    quotes = [ev.get("quote", "") for ev in evidence if ev.get("quote")]
    doc_ids = [ev.get("doc_id", "") for ev in evidence]

    lines.append(f"**{title}** is a concept studied in the scientific literature.")
    if quotes:
        lines.append(f"Research has shown that {quotes[0].lower().rstrip('.')} [^e1].")
    lines.append("")

    # Overview section
    lines.append("## Overview")
    lines.append("")
    used_markers = ["e1"] if quotes else []
    for i, (q, doc) in enumerate(zip(quotes[1:4], doc_ids[1:4]), start=2):
        marker = f"e{i}"
        used_markers.append(marker)
        lines.append(f"{q.rstrip('.')} [^{marker}].")
        lines.append("")

    # Mechanism/Applications section
    if len(quotes) > 4:
        lines.append("## Applications and Significance")
        lines.append("")
        for i, (q, doc) in enumerate(zip(quotes[4:7], doc_ids[4:7]), start=len(used_markers) + 1):
            marker = f"e{i}"
            used_markers.append(marker)
            lines.append(f"{q.rstrip('.')} [^{marker}].")
            lines.append("")

    # See also
    if neighbors:
        lines.append("## See also")
        lines.append("")
        for n in neighbors[:5]:
            lines.append(f"- {n}")
        lines.append("")

    # References
    lines.append("## References")
    lines.append("")
    for i, ev in enumerate(evidence):
        marker = f"e{i + 1}"
        doc = ev.get("doc_id", "")
        quote = ev.get("quote", "").replace('"', "'")
        loc = ev.get("locator", "")
        loc_str = f", {loc}" if loc else ""
        lines.append(f'[^{marker}]: {doc}{loc_str} > "{quote}"')

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


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--poll-seconds", type=int, default=2)
    parser.add_argument("--max-iterations", type=int, default=1000)
    args = parser.parse_args()

    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    WRITE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Heuristic drain: poll={args.poll_seconds}s, max={args.max_iterations}")
    empty_count = 0
    total_extract = 0
    total_write = 0

    for i in range(args.max_iterations):
        found = False

        for f in sorted(EXTRACT_DIR.glob("*.request.json")):
            rid = f.stem.replace(".request", "")
            resp = EXTRACT_DIR / f"{rid}.response.json"
            if not resp.exists():
                if process_extract(f):
                    total_extract += 1
                found = True

        for f in sorted(WRITE_DIR.glob("*.request.json")):
            rid = f.stem.replace(".request", "")
            resp = WRITE_DIR / f"{rid}.response.json"
            if not resp.exists():
                if process_write(f):
                    total_write += 1
                found = True

        if found:
            empty_count = 0
        else:
            empty_count += 1
            if empty_count > 90:  # 3 minutes idle
                print(f"Idle for {empty_count * args.poll_seconds}s, exiting")
                break

        time.sleep(args.poll_seconds)

        if i % 50 == 0 and i > 0:
            print(f"[{i}] extract={total_extract} write={total_write}")

    print(f"\nDone: {total_extract} extracts, {total_write} writes")


if __name__ == "__main__":
    main()
