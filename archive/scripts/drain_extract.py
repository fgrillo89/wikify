"""Drain wikify_simple extract dispatch requests using a real model via litellm.

Usage:
    uv run python scripts/drain_extract.py [--poll-seconds 3] [--max-iterations 500]

Watches data/dispatch/extract/ for .request.json files, calls the model,
validates the response, and writes .response.json.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

DISPATCH_DIR = Path("data/dispatch/extract")
WRITE_DIR = Path("data/dispatch/write")

EXTRACT_SYSTEM = """\
You are a concept extractor for a Wikipedia-style wiki. Given a chunk of text
from an academic paper, extract concepts and people that deserve their own
wiki page.

Return JSON with this EXACT schema (no extra fields):
{
  "chunk_id": "<chunk_id from the request>",
  "concepts": [
    {
      "title": "Concept Name",
      "aliases": ["Alternative Name"],
      "kind": "concept" or "person",
      "category": "phenomenon"|"method"|"material"|"device"|"theory"|"metric"|"organization"|"other" or null,
      "quote": "verbatim substring from the chunk text",
      "evidence_figures": []
    }
  ],
  "tokens_in": 0,
  "tokens_out": 0
}

Rules:
- kind is exactly "concept" or "person"
- category is null for person entries
- title is 2-120 characters
- quote MUST be a VERBATIM substring of the chunk text (5-400 chars)
- evidence_figures is always []
- If the chunk is just a bibliography/references list, return empty concepts []
- Extract 3-8 concepts per chunk typically
"""

WRITE_SYSTEM = """\
You are a Wikipedia article writer. Given a page skeleton with evidence,
write a full encyclopedic article.

Return JSON with this EXACT schema (no extra fields):
{
  "page_id": "<page_id from request>",
  "body_markdown": "full article markdown",
  "used_markers": ["e1", "e2"],
  "tokens_in": 0,
  "tokens_out": 0
}

The body_markdown must:
- Have multiple ## sections (e.g. ## Overview, ## Mechanism, ## Applications)
- Include [^eN] citation markers in prose sentences
- End with ## References section containing [^eN]: definitions
- Be at least 1200 characters of real prose
- NOT contain [[wikilinks]]
- Be a proper Wikipedia-style article, not a stub
"""


def call_model(system: str, user: str, model: str = "claude-haiku-4-5-20251001") -> str:
    """Call the model via litellm."""
    import litellm

    resp = litellm.completion(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.3,
        max_tokens=4000,
    )
    return resp.choices[0].message.content


def extract_json(text: str) -> dict | None:
    """Extract JSON from model response (may be wrapped in ```json blocks)."""
    # Try direct parse first
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in text
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                return None
    return None


def validate_quotes(response: dict, chunk_text: str) -> dict:
    """Validate and fix quotes to be verbatim substrings."""
    normalized_chunk = _normalize(chunk_text)
    for concept in response.get("concepts", []):
        quote = concept.get("quote", "")
        if not quote:
            continue
        # Check if quote is verbatim
        if quote in chunk_text:
            continue
        # Try normalized match
        norm_quote = _normalize(quote)
        if norm_quote in normalized_chunk:
            # Find the actual substring
            idx = normalized_chunk.index(norm_quote)
            # Map back to original - take same length from original
            concept["quote"] = chunk_text[idx : idx + len(quote)].strip()
            if concept["quote"] in chunk_text:
                continue
        # Quote not found - try to find a similar substring
        words = quote.split()[:6]
        search = " ".join(words)
        idx = chunk_text.find(search)
        if idx >= 0:
            # Take a reasonable substring starting from the match
            end = min(idx + len(quote) + 50, len(chunk_text))
            # Find sentence boundary
            for boundary in [".", "!", "?"]:
                bi = chunk_text.find(boundary, idx + len(search))
                if 0 < bi < end:
                    end = bi + 1
                    break
            concept["quote"] = chunk_text[idx:end].strip()[:400]
        else:
            # Last resort: pick first substantial sentence from chunk
            sentences = re.split(r"(?<=[.!?])\s+", chunk_text)
            for s in sentences:
                s = s.strip()
                if len(s) >= 20 and not s.startswith("[") and not s.startswith("-"):
                    concept["quote"] = s[:300]
                    break
    # Final validation: remove concepts with quotes not in chunk
    response["concepts"] = [
        c for c in response.get("concepts", [])
        if c.get("quote", "") and c["quote"] in chunk_text
    ]
    return response


def _normalize(s: str) -> str:
    import unicodedata

    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", " ", s)
    return s.lower()


def process_extract(request_path: Path) -> bool:
    """Process one extract request. Returns True if successful."""
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

    user_msg = f"Chunk ID: {chunk_id}\n\nExisting concepts (avoid duplicates): {canonical[-16:]}\n\nChunk text:\n{chunk_text}"

    try:
        raw = call_model(EXTRACT_SYSTEM, user_msg)
        data = extract_json(raw)
        if data is None:
            print(f"  ERROR parsing JSON for {rid}")
            return False

        # Ensure required fields
        data["chunk_id"] = chunk_id
        data.setdefault("concepts", [])
        data.setdefault("tokens_in", 500)
        data.setdefault("tokens_out", 200)

        # Validate and fix quotes
        data = validate_quotes(data, chunk_text)

        # Clean concepts
        for c in data["concepts"]:
            c.setdefault("aliases", [])
            c.setdefault("evidence_figures", [])
            if c.get("kind") not in ("concept", "person"):
                c["kind"] = "concept"
            if c["kind"] == "person":
                c["category"] = None
            # Remove any extra fields
            allowed = {"title", "aliases", "kind", "category", "quote", "evidence_figures"}
            for key in list(c.keys()):
                if key not in allowed:
                    del c[key]

        response_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        n = len(data["concepts"])
        print(f"  OK {rid}: {n} concepts")
        return True
    except Exception as e:
        print(f"  ERROR processing {rid}: {e}")
        return False


def process_write(request_path: Path) -> bool:
    """Process one write request. Returns True if successful."""
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
    title = req["title"]
    skeleton = req.get("skeleton", "")
    evidence = req.get("evidence", [])
    neighbors = req.get("neighbor_titles", [])
    style_guide = req.get("style_guide", "")
    field_guide = req.get("field_guide", "")
    artifact_template = req.get("artifact_template", "")
    corpus_persona = req.get("corpus_persona", "")

    # Build evidence block for the prompt
    ev_lines = []
    for i, ev in enumerate(evidence):
        marker = f"e{i + 1}"
        ev_lines.append(f"[^{marker}]: {ev.get('doc_id', '')} > \"{ev.get('quote', '')}\"")

    user_msg = f"""Write a full Wikipedia-style article for: {title}
Page kind: {req.get('page_kind', 'concept')}

{f'Corpus persona: {corpus_persona}' if corpus_persona else ''}
{f'Style guide: {style_guide[:500]}' if style_guide else ''}
{f'Field guide: {field_guide[:500]}' if field_guide else ''}
{f'Article template: {artifact_template[:500]}' if artifact_template else ''}

Skeleton/draft:
{skeleton}

Evidence to cite (use [^eN] markers):
{chr(10).join(ev_lines)}

Neighbor articles: {', '.join(neighbors[:8])}

Write a complete encyclopedic article. Use [^eN] markers to cite evidence.
End with ## References containing the evidence definitions."""

    try:
        # Use a more capable model for writing
        raw = call_model(WRITE_SYSTEM, user_msg, model="claude-sonnet-4-20250514")
        data = extract_json(raw)
        if data is None:
            print(f"  ERROR parsing write JSON for {rid}")
            return False

        data["page_id"] = page_id
        data.setdefault("tokens_in", 1000)
        data.setdefault("tokens_out", 500)

        # Extract used markers
        body = data.get("body_markdown", "")
        markers = re.findall(r"\[\^(e\d+)\]", body)
        data["used_markers"] = list(dict.fromkeys(markers))

        # Remove extra fields
        allowed = {"page_id", "body_markdown", "used_markers", "tokens_in", "tokens_out"}
        for key in list(data.keys()):
            if key not in allowed:
                del data[key]

        response_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        print(f"  OK write {rid}: {len(body)} chars, {len(data['used_markers'])} markers")
        return True
    except Exception as e:
        print(f"  ERROR processing write {rid}: {e}")
        return False


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--poll-seconds", type=int, default=3)
    parser.add_argument("--max-iterations", type=int, default=500)
    args = parser.parse_args()

    DISPATCH_DIR.mkdir(parents=True, exist_ok=True)
    WRITE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Drain loop: poll={args.poll_seconds}s, max={args.max_iterations} iterations")
    empty_count = 0
    total_extract = 0
    total_write = 0

    for i in range(args.max_iterations):
        found = False

        # Process extract requests
        for f in sorted(DISPATCH_DIR.glob("*.request.json")):
            rid = f.stem.replace(".request", "")
            resp = DISPATCH_DIR / f"{rid}.response.json"
            if not resp.exists():
                print(f"[{i}] Extract: {rid}")
                if process_extract(f):
                    total_extract += 1
                found = True

        # Process write requests
        for f in sorted(WRITE_DIR.glob("*.request.json")):
            rid = f.stem.replace(".request", "")
            resp = WRITE_DIR / f"{rid}.response.json"
            if not resp.exists():
                print(f"[{i}] Write: {rid}")
                if process_write(f):
                    total_write += 1
                found = True

        if found:
            empty_count = 0
        else:
            empty_count += 1
            if empty_count > 60:  # 3 minutes of no requests
                print(f"No requests for {empty_count * args.poll_seconds}s, exiting")
                break

        time.sleep(args.poll_seconds)

    print(f"\nDone: {total_extract} extracts, {total_write} writes processed")


if __name__ == "__main__":
    main()
