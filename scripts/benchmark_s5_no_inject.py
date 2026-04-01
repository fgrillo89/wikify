"""Benchmark S5 (no concept-graph auto-injection): Gap-structured review.

Same strategy as S5 but with SCHOLARFORGE_INJECT_CONCEPT_GRAPH=false.
The concept graph is still built via record_paper_summary(concept_links=...),
and find_citation_for() / query_concept_graph() are called explicitly during
the writing phase to look up citations.

Output: data/output/benchmark_v2/s5_no_inject.md
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# ── Disable concept graph auto-injection BEFORE any imports ─────────────────
os.environ["SCHOLARFORGE_INJECT_CONCEPT_GRAPH"] = "false"

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

OUTPUT_DIR = Path("data/output/benchmark_v2")
OUTPUT_STEM = "s5_no_inject"
OUTPUT_MD = OUTPUT_DIR / f"{OUTPUT_STEM}.md"
LOG_BASENAME = "log_s5_no_inject"


def _extract_concept_links_llm(paper_name: str, text: str) -> list[dict]:
    """Use LLM to extract concept links from paper text.

    Returns list of {"from": str, "to": str, "relation": str, "evidence": str}.
    """
    import litellm

    from scholarforge.config import settings

    prompt = (
        f"Extract concept relationships from this paper text.\n\n"
        f"Paper: {paper_name}\n\n"
        f"Text:\n{text[:3000]}\n\n"
        f"Return a JSON object with two keys:\n"
        f'{{"summary": {{"key_findings": [...], "quantitative_data": [...], '
        f'"relevance": "one sentence", "gaps_noted": [...]}},\n'
        f'"concept_links": [\n'
        f'  {{"from": "concept_A", "to": "concept_B", '
        f'"relation": "achieves/enables/causes/limits/contradicts", '
        f'"evidence": "brief evidence"}},\n'
        f"  ...\n"
        f"]}}\n\n"
        f"Extract 5-10 concept links. Focus on:\n"
        f"- Material -> property (e.g., HfO2 -> resistive switching)\n"
        f"- Method -> outcome (e.g., ALD -> conformal coverage)\n"
        f"- Device -> performance (e.g., memristor -> 10^6 endurance)\n"
        f"- Phenomenon -> mechanism (e.g., filament formation -> oxygen vacancy)\n"
    )

    try:
        resp = litellm.completion(
            model=settings.llm_model,
            messages=[
                {
                    "role": "system",
                    "content": "Extract structured info and concept relationships "
                    "from academic papers. Return valid JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=1024,
        )
        content = resp.choices[0].message.content or "{}"
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0]
        data = json.loads(content)
        return data.get("concept_links", []), data.get("summary", {})
    except Exception as exc:
        print(f"    Warning: concept extraction failed: {exc}")
        return [], {}


def main() -> None:
    start = time.time()

    # Suppress noisy logging
    import logging

    logging.basicConfig(level=logging.WARNING)

    # Verify injection is disabled
    from scholarforge.config import settings

    assert not settings.inject_concept_graph, (
        "inject_concept_graph should be False (env var not picked up)"
    )

    # ── Step 0: Imports ──────────────────────────────────────────────────────
    from scholarforge.agent.concept_graph import get_concept_graph, reset_concept_graph
    from scholarforge.agent.core import ScholarForgeAgent
    from scholarforge.agent.defaults import get_default_hooks
    from scholarforge.agent.reading_log import reset_reading_log
    from scholarforge.agent.tools import (
        deep_read,
        find_citation_for,
        find_corpus_gaps,
        find_synthesis_opportunities,
        get_frontier_exploration_order,
        get_session_context,
        query_concept_graph,
        read_paper_digest,
        record_paper_summary,
        reset_paper_summaries,
        save_reading_log,
        search_papers,
    )
    from scholarforge.agent.workflows import export_paper
    from scholarforge.export.journal_profile import load_journal_profile
    from scholarforge.generate.persona import build_persona

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── Step 1: Reset everything ────────────────────────────────────────────
    print("=== S5 BENCHMARK (NO CONCEPT GRAPH INJECTION) ===")
    print(f"  inject_concept_graph = {settings.inject_concept_graph}")
    print()
    print("[1/10] Resetting reading log, paper summaries, concept graph...")
    reset_reading_log()
    reset_paper_summaries()
    reset_concept_graph()

    # ── Step 2: Call gaps FIRST ──────────────────────────────────────────────
    print("[2/10] Finding corpus gaps and synthesis opportunities...")
    t2 = time.time()
    gaps_text = find_corpus_gaps()
    synth_text = find_synthesis_opportunities()
    print(f"  Gaps found in {time.time() - t2:.1f}s")
    print(f"  Gaps length: {len(gaps_text)} chars")
    print(f"  Synthesis length: {len(synth_text)} chars")

    # ── Step 3: Get frontier order (12 papers) ───────────────────────────────
    print("[3/10] Computing frontier exploration order (12 papers)...")
    t3 = time.time()
    frontier_text = get_frontier_exploration_order(max_papers=12)
    print(f"  Frontier order computed in {time.time() - t3:.1f}s")

    # Parse frontier order to extract paper names and roles
    papers_to_read = []
    frontier_lines = frontier_text.split("\n")
    for idx, line in enumerate(frontier_lines):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("---"):
            continue
        if ". " in line and "**" in line:
            try:
                start_idx = line.index("**") + 2
                end_idx = line.index("**", start_idx)
                paper_name = line[start_idx:end_idx]
            except ValueError:
                continue
            depth = "full" if "[full]" in line else "digest"
            # Role is determined from the NEXT line (rationale) or the same line
            rationale = line.lower()
            if idx + 1 < len(frontier_lines):
                rationale += " " + frontier_lines[idx + 1].strip().lower()
            role = "standard"
            if "pagerank" in rationale or "greedy" in rationale:
                role = "hub"
            elif "serendipity" in rationale:
                role = "frontier"
            elif "frontier" in rationale:
                role = "frontier"
            elif "bridge" in rationale:
                role = "bridge"
            papers_to_read.append(
                {
                    "name": paper_name,
                    "depth": depth,
                    "role": role,
                }
            )

    print(f"  Papers to read: {len(papers_to_read)}")
    for p in papers_to_read:
        print(f"    - {p['name'][:60]} [{p['depth']}, {p['role']}]")

    # ── Step 4: Deep-read 3 seeds (role="hub") with concept_links ───────────
    print("[4/10] Deep-reading seed papers (hub) with concept link extraction...")
    seeds = [p for p in papers_to_read if p["role"] == "hub"][:3]
    if len(seeds) < 3:
        for p in papers_to_read:
            if p not in seeds and len(seeds) < 3:
                seeds.append(p)

    for i, seed in enumerate(seeds):
        print(f"  Seed {i + 1}/3: {seed['name'][:60]}...")
        t_read = time.time()
        # Extract title portion after "YYYY - " for better pattern matching
        pattern = seed["name"]
        if " - " in pattern:
            pattern = pattern.split(" - ", 1)[1][:40]
        result = deep_read(pattern, reason=f"Hub paper for S5 no-inject (seed {i + 1})")
        print(f"    Read in {time.time() - t_read:.1f}s, {len(result)} chars")

        try:
            data = json.loads(result)
            full_text = data.get("full_text", "")
        except (json.JSONDecodeError, TypeError):
            full_text = result

        # Extract concept links via LLM
        print("    Extracting concept links...")
        t_extract = time.time()
        concept_links, summary_data = _extract_concept_links_llm(seed["name"], full_text)
        print(f"    Extracted {len(concept_links)} links in {time.time() - t_extract:.1f}s")

        record_paper_summary(
            paper_name=seed["name"],
            key_findings=summary_data.get(
                "key_findings", [f"Deep-read completed, {len(result)} chars"]
            ),
            quantitative_data=summary_data.get("quantitative_data", []),
            relevance=summary_data.get("relevance", "Hub paper for ALD memristor review"),
            concept_links=concept_links,
            gaps_noted=summary_data.get("gaps_noted", []),
            read_depth="full",
            role="hub",
        )

    # ── Step 5: Deep-read 1 frontier with concept_links ─────────────────────
    print("[5/10] Deep-reading frontier paper with concept links...")
    frontiers = [p for p in papers_to_read if p["role"] == "frontier"][:1]
    if not frontiers:
        frontiers = [p for p in papers_to_read if p not in seeds][:1]

    for f_paper in frontiers:
        print(f"  Frontier: {f_paper['name'][:60]}...")
        t_read = time.time()
        pattern = f_paper["name"]
        if " - " in pattern:
            pattern = pattern.split(" - ", 1)[1][:40]
        result = deep_read(pattern, reason="Frontier paper for S5 no-inject")
        print(f"    Read in {time.time() - t_read:.1f}s, {len(result)} chars")

        try:
            data = json.loads(result)
            full_text = data.get("full_text", "")
        except (json.JSONDecodeError, TypeError):
            full_text = result

        print("    Extracting concept links...")
        t_extract = time.time()
        concept_links, summary_data = _extract_concept_links_llm(f_paper["name"], full_text)
        print(f"    Extracted {len(concept_links)} links in {time.time() - t_extract:.1f}s")

        record_paper_summary(
            paper_name=f_paper["name"],
            key_findings=summary_data.get(
                "key_findings", [f"Deep-read completed, {len(result)} chars"]
            ),
            quantitative_data=summary_data.get("quantitative_data", []),
            relevance=summary_data.get("relevance", "Frontier paper - emerging/niche topic"),
            concept_links=concept_links,
            gaps_noted=summary_data.get("gaps_noted", []),
            read_depth="full",
            role="frontier",
        )

    # ── Step 6: Digest bridges + remaining with concept_links ────────────────
    print("[6/10] Reading digests for bridges and remaining papers (with concept links)...")
    already_read = {s["name"] for s in seeds} | {f["name"] for f in frontiers}
    remaining = [p for p in papers_to_read if p["name"] not in already_read]

    for i, paper in enumerate(remaining):
        print(f"  Digest {i + 1}/{len(remaining)}: {paper['name'][:60]}...")
        t_read = time.time()
        digest_pattern = paper["name"]
        if " - " in digest_pattern:
            digest_pattern = digest_pattern.split(" - ", 1)[1][:40]
        result = read_paper_digest(
            digest_pattern,
            reason=f"{paper['role'].capitalize()} paper digest for S5 no-inject",
        )
        print(f"    Read in {time.time() - t_read:.1f}s, {len(result)} chars")

        # Extract concept links from digest text too
        print("    Extracting concept links...")
        t_extract = time.time()
        concept_links, summary_data = _extract_concept_links_llm(paper["name"], result)
        print(f"    Extracted {len(concept_links)} links in {time.time() - t_extract:.1f}s")

        record_paper_summary(
            paper_name=paper["name"],
            key_findings=summary_data.get("key_findings", [f"Digest read, {len(result)} chars"]),
            quantitative_data=summary_data.get("quantitative_data", []),
            relevance=summary_data.get("relevance", f"{paper['role'].capitalize()} paper"),
            concept_links=concept_links,
            gaps_noted=summary_data.get("gaps_noted", []),
            read_depth="digest",
            role=paper["role"],
        )

    # ── Step 7: TWO search_papers targeting gaps ─────────────────────────────
    print("[7/10] Searching for gap-targeted papers...")
    gap_queries = [
        "ALD memristor interface engineering defects switching mechanism",
        "ALD neuromorphic device synaptic plasticity array integration",
    ]
    if "void" in gaps_text.lower() or "gap" in gaps_text.lower():
        for line in gaps_text.split("\n"):
            if "between" in line.lower() and ("cluster" in line.lower() or "topic" in line.lower()):
                clean = line.strip("- *").strip()[:100]
                if len(clean) > 20:
                    gap_queries.append(clean)

    for i, query in enumerate(gap_queries[:2]):
        print(f"  Search {i + 1}/2: {query[:60]}...")
        t_search = time.time()
        result = search_papers(
            query, top_k=8, reason=f"Gap-targeted search {i + 1} for S5 no-inject"
        )
        print(f"    Found in {time.time() - t_search:.1f}s, {len(result)} chars")

    # ── Step 8: Explicit find_citation_for() calls ───────────────────────────
    print("[8/10] Running explicit find_citation_for() lookups...")
    citation_claims = [
        "HfO2 resistive switching memristor",
        "Al2O3 tunnel barrier ALD",
        "TaOx analog switching synaptic",
        "ZnO memristor ALD",
        "ALD conformal deposition high aspect ratio",
        "oxygen vacancy filament formation",
        "STDP synaptic plasticity memristor",
        "crossbar array neuromorphic computing",
        "ALD TiN electrode memristor",
        "endurance cycling memristor oxide",
        "2D materials memristor",
        "nitride memristor ALD",
        "forming-free memristor",
        "multilevel resistance states",
        "ALD temperature uniformity",
    ]

    citation_results = {}
    for claim in citation_claims:
        t_cite = time.time()
        cite_result = find_citation_for(claim)
        citation_results[claim] = cite_result
        # Brief output
        first_line = cite_result.split("\n")[0] if cite_result else "(empty)"
        print(f"  [{time.time() - t_cite:.1f}s] {claim[:50]} -> {first_line[:60]}")

    # Also query concept graph for key concepts
    print("\n  Querying concept graph for key concepts...")
    graph = get_concept_graph()
    print(f"  Concept graph: {len(graph.edges)} edges, {len(graph.concepts)} concepts")
    cg_queries = ["hfo2", "ald", "memristor", "endurance", "switching", "synaptic"]
    cg_results = {}
    for concept in cg_queries:
        cg_result = query_concept_graph(concept)
        cg_results[concept] = cg_result
        n_lines = len(cg_result.split("\n"))
        print(f"  query_concept_graph('{concept}'): {n_lines} lines")

    # ── Step 9: Get session context + write ──────────────────────────────────
    print("[9/10] Building session context and writing...")
    session_ctx = get_session_context()
    print(f"  Session context: {len(session_ctx)} chars")

    explore_time = time.time() - start
    print(f"\n  Exploration phase completed in {explore_time:.1f}s")

    # Build citation lookup summary for the writing prompt
    cite_summary_lines = ["## Citation Lookup Results (from find_citation_for)"]
    for claim, result in citation_results.items():
        cite_summary_lines.append(f"\n### {claim}")
        cite_summary_lines.append(result)
    cite_summary = "\n".join(cite_summary_lines)

    # Build concept graph summary
    cg_summary_lines = ["## Concept Graph Queries"]
    for concept, result in cg_results.items():
        cg_summary_lines.append(f"\n### {concept}")
        cg_summary_lines.append(result)
    cg_summary = "\n".join(cg_summary_lines)

    # Build system prompt
    journal_profile = load_journal_profile("adv_funct_mater")
    persona = build_persona(
        journal_profile=journal_profile,
        artifact_type_id="lit_review",
        user_prompt="ALD for memristors",
    )

    s5_system = (
        persona
        + """

## CRITICAL INSTRUCTION: Gap-Structured Review (Strategy S5, No Auto-Injection)

You are writing a review paper organized AROUND GAPS rather than conventional themes.
The concept graph is NOT auto-injected into your context. Instead, you have been
provided with explicit find_citation_for() results and query_concept_graph() results.
Use the [REF:DisplayName] markers from these results for your citations.

### Structure
1. **Title**: Specific, names ALD + memristors + the gap-focused angle
2. **Abstract**: 200-250 words, no citations, highlights gaps as the organizing principle
3. **Introduction**: ALD fundamentals and relevance to memristors (800-1000 words)
4. **Materials Landscape**: HfO2, Al2O3, TaOx, ZnO, nitrides, 2D materials (600-800 words)
5. **Switching Mechanisms**: Filamentary vs interface-type, role of defects (800-1000 words)
6. **Synaptic Functions**: STDP, LTP/LTD, analog switching (800-1000 words)
7. **Array Integration**: Crossbar, selectors, sneak path, scalability (600-800 words)
8. **Gaps and Future Directions**: Organized as established -> contradictions -> what's missing -> specific question (800-1200 words)
9. **Conclusion**: Forward-looking synthesis (400-500 words)

Each body section should flow: known findings -> contradictions -> gaps -> research questions.

### Requirements
- 5000-6000 words total
- 50+ unique references using [REF:DisplayName] markers
- 4 figure placeholders with detailed captions
- Every claim must have a citation from the provided lookup results
- Use the session context, gap analysis, and citation lookup results below

### Citation Format
Use [REF:DisplayName] where DisplayName is copied EXACTLY from the citation lookup
results or session context. Example: [REF:Smith 2020 - Title of Paper]

### Banned
- No em-dashes
- No bullet points in body sections
- No "delve/crucial/pivotal/groundbreaking/cutting-edge/novel/landscape/tapestry"
- No meta-commentary about methodology
- No "In recent years" as opener
"""
    )

    user_prompt = f"""Write a review paper on ALD for memristors targeting Advanced Functional Materials.
Use the gap-structured strategy (S5): organize around GAPS, not conventional themes.

## Corpus Gap Analysis

{gaps_text}

## Synthesis Opportunities

{synth_text}

## Frontier Exploration Order

{frontier_text}

## Session Context (Papers Read)

{session_ctx}

{cite_summary}

{cg_summary}

## Instructions

Now write the full review paper (5000-6000 words, 50+ refs, 4 figure placeholders).
Required sections: ALD fundamentals + relevance, materials (HfO2, Al2O3, TaOx, ZnO, nitrides, 2D),
switching mechanisms, synaptic functions, array integration, gaps/future directions, conclusion.
Each body section: known -> contradictions -> missing -> specific question.
The gaps/future section synthesizes all gaps into a research agenda.

Start with the title, then write each section. Use [REF:DisplayName] citation markers
from the citation lookup results above. Every major claim needs a [REF:...] marker.
"""

    hooks = get_default_hooks(token_budget=250_000)

    # Writer agent with search + digest + citation lookup for additional lookups
    sp = search_papers
    rpd = read_paper_digest
    fcf = find_citation_for

    agent = ScholarForgeAgent(
        model=None,
        tools=[sp, rpd, fcf],
        hooks=hooks,
        system_prompt=s5_system,
    )

    t_write = time.time()
    result = agent.run(user_prompt, max_turns=10)
    write_time = time.time() - t_write
    print(f"  Writing completed in {write_time:.1f}s")
    print(f"  Turns: {result.total_turns}")
    print(f"  Tool calls: {len(result.tool_calls)}")
    print(f"  Tokens: {result.total_input_tokens:,} in + {result.total_output_tokens:,} out")

    for hook in hooks:
        if hasattr(hook, "summary"):
            print(f"  Cost: {hook.summary()}")

    markdown = result.content
    if not markdown:
        print("ERROR: Agent returned empty content!")
        sys.exit(1)

    word_count = len(markdown.split())
    print(f"  Words: {word_count}")

    # ── Step 10: Export + save ───────────────────────────────────────────────
    print("[10/10] Exporting...")
    outputs = export_paper(
        markdown,
        str(OUTPUT_MD),
        journal="adv_funct_mater",
        docx=True,
        pdf=True,
    )
    for p in outputs:
        print(f"  Written: {p} ({p.stat().st_size:,} bytes)")

    # Save reading log
    print("\nSaving reading log...")
    log_result = save_reading_log(str(OUTPUT_DIR), LOG_BASENAME)
    print(f"  {log_result}")

    # Save concept graph
    print("Saving concept graph...")
    graph = get_concept_graph()
    graph_path = graph.save("data/output/benchmark_v2/concept_graph_no_inject.json")
    print(f"  Concept graph: {len(graph.edges)} edges, {len(graph.concepts)} concepts")
    print(f"  Saved to: {graph_path}")

    # ── Summary ──────────────────────────────────────────────────────────────
    total_time = time.time() - start
    print("\n" + "=" * 60)
    print(f"TOTAL TIME: {total_time:.1f}s ({total_time / 60:.1f} min)")
    print(f"  Exploration: {explore_time:.1f}s")
    print(f"  Writing: {write_time:.1f}s")
    print(f"  Words: {word_count}")
    print(f"  Concept graph: {len(graph.edges)} edges, {len(graph.concepts)} concepts")
    print(f"  Output: {OUTPUT_MD}")
    print("=" * 60)


if __name__ == "__main__":
    main()
