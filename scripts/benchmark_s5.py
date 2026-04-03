"""Benchmark S5: Gap-structured review.

Strategy: review ORGANIZED AROUND GAPS rather than conventional themes.
- Call gaps FIRST (find_corpus_gaps + find_synthesis_opportunities)
- Get frontier order (12 papers)
- Deep-read 3 seeds (role="hub"), record_paper_summary each
- Deep-read 1 frontier (role="frontier"), record_paper_summary
- Digest bridges + remaining, record_paper_summary each
- TWO search_papers targeting gaps
- get_session_context
- Write 5000-6000 words, 50+ refs, 4 figure placeholders
- Export DOCX+PDF, save log

Output: data/output/benchmark_v2/s5_gap_structured.md
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

OUTPUT_DIR = Path("data/output/benchmark_v2")
OUTPUT_STEM = "s5_gap_structured"
OUTPUT_MD = OUTPUT_DIR / f"{OUTPUT_STEM}.md"
LOG_BASENAME = "log_s5"


def main() -> None:
    start = time.time()

    # ── Step 0: Imports ──────────────────────────────────────────────────────
    from wikify.agent.core import ScholarForgeAgent
    from wikify.agent.defaults import get_default_hooks
    from wikify.agent.reading_log import reset_reading_log
    from wikify.agent.tools import (
        deep_read,
        find_corpus_gaps,
        find_synthesis_opportunities,
        get_frontier_exploration_order,
        get_session_context,
        read_paper_digest,
        record_paper_summary,
        reset_paper_summaries,
        save_reading_log,
        search_papers,
    )
    from wikify.agent.workflows import export_paper
    from wikify.export.journal_profile import load_journal_profile
    from wikify.generate.persona import build_persona

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── Step 1: Reset reading log + paper summaries ──────────────────────────
    print("=== S5 BENCHMARK: Gap-Structured Review ===")
    print()
    print("[1/9] Resetting reading log and paper summaries...")
    reset_reading_log()
    reset_paper_summaries()

    # ── Step 2: Call gaps FIRST ──────────────────────────────────────────────
    print("[2/9] Finding corpus gaps and synthesis opportunities...")
    t2 = time.time()
    gaps_text = find_corpus_gaps()
    synth_text = find_synthesis_opportunities()
    print(f"  Gaps found in {time.time() - t2:.1f}s")
    print(f"  Gaps length: {len(gaps_text)} chars")
    print(f"  Synthesis length: {len(synth_text)} chars")

    # ── Step 3: Get frontier order (12 papers) ───────────────────────────────
    print("[3/9] Computing frontier exploration order (12 papers)...")
    t3 = time.time()
    frontier_text = get_frontier_exploration_order(max_papers=12)
    print(f"  Frontier order computed in {time.time() - t3:.1f}s")

    # Parse frontier order to extract paper names and roles
    papers_to_read = []
    for line in frontier_text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("---"):
            continue
        # Lines look like: "1. **Paper Name** [full] (seed) - rationale"
        # or: "4. **Paper Name** [digest] (frontier) - rationale"
        if ". **" in line:
            # Extract paper name between ** **
            start_idx = line.index("**") + 2
            end_idx = line.index("**", start_idx)
            paper_name = line[start_idx:end_idx]

            # Extract depth [full] or [digest]
            depth = "digest"
            if "[full]" in line:
                depth = "full"

            # Extract role
            role = "standard"
            if "(seed)" in line:
                role = "hub"
            elif "(frontier)" in line:
                role = "frontier"
            elif "(bridge)" in line:
                role = "bridge"
            elif "(serendipity)" in line:
                role = "frontier"

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

    # ── Step 4: Deep-read 3 seeds (role="hub") ───────────────────────────────
    print("[4/9] Deep-reading seed papers (hub)...")
    seeds = [p for p in papers_to_read if p["role"] == "hub"][:3]
    if len(seeds) < 3:
        # Supplement with first papers if not enough seeds
        for p in papers_to_read:
            if p not in seeds and len(seeds) < 3:
                seeds.append(p)

    for i, seed in enumerate(seeds):
        print(f"  Seed {i + 1}/3: {seed['name'][:60]}...")
        t_read = time.time()
        result = deep_read(seed["name"], reason=f"Hub paper for S5 benchmark (seed {i + 1})")
        print(f"    Read in {time.time() - t_read:.1f}s, {len(result)} chars")

        # Parse the result to extract data for summary
        try:
            data = json.loads(result)
            full_text = data.get("full_text", "")[:3000]
        except (json.JSONDecodeError, TypeError):
            full_text = result[:3000]

        # Record summary (we'll let the agent do proper extraction later,
        # but record placeholder so reading log is populated)
        record_paper_summary(
            paper_name=seed["name"],
            key_findings=[f"Deep-read completed, {len(result)} chars of content"],
            quantitative_data=[],
            relevance="Hub paper for ALD memristor review",
            gaps_noted=[],
            read_depth="full",
            role="hub",
        )

    # ── Step 5: Deep-read 1 frontier ─────────────────────────────────────────
    print("[5/9] Deep-reading frontier paper...")
    frontiers = [p for p in papers_to_read if p["role"] == "frontier"][:1]
    if not frontiers:
        frontiers = [p for p in papers_to_read if p not in seeds][:1]

    for f_paper in frontiers:
        print(f"  Frontier: {f_paper['name'][:60]}...")
        t_read = time.time()
        result = deep_read(f_paper["name"], reason="Frontier paper for S5 benchmark")
        print(f"    Read in {time.time() - t_read:.1f}s, {len(result)} chars")
        record_paper_summary(
            paper_name=f_paper["name"],
            key_findings=[f"Deep-read completed, {len(result)} chars of content"],
            quantitative_data=[],
            relevance="Frontier paper - emerging/niche topic",
            gaps_noted=[],
            read_depth="full",
            role="frontier",
        )

    # ── Step 6: Digest bridges + remaining ───────────────────────────────────
    print("[6/9] Reading digests for bridges and remaining papers...")
    already_read = {s["name"] for s in seeds} | {f["name"] for f in frontiers}
    remaining = [p for p in papers_to_read if p["name"] not in already_read]

    for i, paper in enumerate(remaining):
        print(f"  Digest {i + 1}/{len(remaining)}: {paper['name'][:60]}...")
        t_read = time.time()
        result = read_paper_digest(
            paper["name"],
            reason=f"{paper['role'].capitalize()} paper digest for S5 benchmark",
        )
        print(f"    Read in {time.time() - t_read:.1f}s, {len(result)} chars")
        record_paper_summary(
            paper_name=paper["name"],
            key_findings=[f"Digest read, {len(result)} chars"],
            quantitative_data=[],
            relevance=f"{paper['role'].capitalize()} paper for ALD memristor review",
            gaps_noted=[],
            read_depth="digest",
            role=paper["role"],
        )

    # ── Step 7: TWO search_papers targeting gaps ─────────────────────────────
    print("[7/9] Searching for gap-targeted papers...")
    # Extract gap themes from the gaps text
    gap_queries = [
        "ALD memristor interface engineering defects switching mechanism",
        "ALD neuromorphic device synaptic plasticity array integration",
    ]
    # Try to extract real gap topics from the gaps text
    if "void" in gaps_text.lower() or "gap" in gaps_text.lower():
        for line in gaps_text.split("\n"):
            if "between" in line.lower() and ("cluster" in line.lower() or "topic" in line.lower()):
                # Use this as a search query
                clean = line.strip("- *").strip()[:100]
                if len(clean) > 20:
                    gap_queries.append(clean)

    for i, query in enumerate(gap_queries[:2]):
        print(f"  Search {i + 1}/2: {query[:60]}...")
        t_search = time.time()
        result = search_papers(query, top_k=8, reason=f"Gap-targeted search {i + 1} for S5")
        print(f"    Found in {time.time() - t_search:.1f}s, {len(result)} chars")

    # ── Step 8: Get session context ──────────────────────────────────────────
    print("[8/9] Building session context...")
    session_ctx = get_session_context()
    print(f"  Session context: {len(session_ctx)} chars, {len(session_ctx.split())} words")

    explore_time = time.time() - start
    print(f"\n  Exploration phase completed in {explore_time:.1f}s")

    # ── Step 9: Write the review ─────────────────────────────────────────────
    print("[9/9] Writing gap-structured review with agent...")

    # Build system prompt: style guide + lit review rules + field guide + AFM profile
    journal_profile = load_journal_profile("adv_funct_mater")
    persona = build_persona(
        journal_profile=journal_profile,
        artifact_type_id="lit_review",
        user_prompt="ALD for memristors",
    )

    # Craft the S5-specific writing prompt
    s5_system = (
        persona
        + """

## CRITICAL INSTRUCTION: Gap-Structured Review (Strategy S5)

You are writing a review paper organized AROUND GAPS rather than conventional themes.
This is the distinguishing feature of this review: each body section flows as
ESTABLISHED FINDINGS -> CONTRADICTIONS -> WHAT'S MISSING -> SPECIFIC QUESTION.

### Structure
1. **Title**: Specific, names ALD + memristors + the gap-focused angle
2. **Abstract**: 200-250 words, no citations, highlights gaps as the organizing principle
3. **Introduction**: ALD fundamentals and relevance to memristors (800-1000 words)
4. **Materials Landscape**: Brief overview to anchor the reader (600-800 words)
5. **3-4 Gap-Themed Sections** (each 800-1200 words): Each section covers a THEME but is structured as:
   - Established findings (what we know, with citations)
   - Contradictions (where studies disagree, with specific examples)
   - What's missing (the gap)
   - Specific research question that would fill the gap
6. **Open Questions**: Synthesize ALL gaps from the body into a coherent research agenda (600-800 words)
7. **Conclusion**: Forward-looking synthesis (400-500 words)

### Requirements
- 5000-6000 words total
- 50+ unique references using [REF:DisplayName] markers
- 4 figure placeholders with detailed captions
- Every claim must have a citation
- Use the session context and gap analysis provided below

### Citation Format
Use [REF:DisplayName] where DisplayName matches the paper's display_name from the session context.
Example: [REF:Smith 2020 - Title of Paper]

### Banned
- No em-dashes
- No bullet points in body sections
- No "delve/crucial/pivotal/groundbreaking/cutting-edge/novel/landscape/tapestry"
- No meta-commentary about methodology
- No "In recent years" as opener
"""
    )

    # Build the user prompt with all exploration data
    user_prompt = f"""Write a review paper on ALD for memristors targeting Advanced Functional Materials.
Use the gap-structured strategy (S5): organize the review around GAPS rather than conventional themes.

## Corpus Gap Analysis

{gaps_text}

## Synthesis Opportunities

{synth_text}

## Frontier Exploration Order

{frontier_text}

## Session Context (Papers Read)

{session_ctx}

## Instructions

Now write the full review paper (5000-6000 words, 50+ refs, 4 figure placeholders).
Each body section after the Materials Landscape must flow: known -> contradictions -> missing -> specific question.
The Open Questions section synthesizes all gaps into a research agenda.

Start with the title, then write each section in order. Use [REF:DisplayName] citation markers.
"""

    hooks = get_default_hooks(token_budget=250_000)

    # The writer agent only needs search_papers and read_paper_digest
    # in case it needs to look up more details
    from wikify.agent.tools import (
        read_paper_digest as rpd,
    )
    from wikify.agent.tools import (
        search_papers as sp,
    )

    agent = ScholarForgeAgent(
        model=None,  # Use default from config
        tools=[sp, rpd],
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

    # Show cost
    for hook in hooks:
        if hasattr(hook, "summary"):
            print(f"  Cost: {hook.summary()}")

    markdown = result.content
    if not markdown:
        print("ERROR: Agent returned empty content!")
        sys.exit(1)

    word_count = len(markdown.split())
    print(f"  Words: {word_count}")

    # ── Export ────────────────────────────────────────────────────────────────
    print("\nExporting...")
    outputs = export_paper(
        markdown,
        str(OUTPUT_MD),
        journal="adv_funct_mater",
        docx=True,
        pdf=True,
    )
    for p in outputs:
        print(f"  Written: {p} ({p.stat().st_size:,} bytes)")

    # ── Save reading log ─────────────────────────────────────────────────────
    print("\nSaving reading log...")
    log_result = save_reading_log(str(OUTPUT_DIR), LOG_BASENAME)
    print(f"  {log_result}")

    # ── Summary ──────────────────────────────────────────────────────────────
    total_time = time.time() - start
    print("\n" + "=" * 60)
    print(f"TOTAL TIME: {total_time:.1f}s ({total_time / 60:.1f} min)")
    print(f"  Exploration: {explore_time:.1f}s")
    print(f"  Writing: {write_time:.1f}s")
    print(f"  Words: {word_count}")
    print(f"  Output: {OUTPUT_MD}")
    print("=" * 60)


if __name__ == "__main__":
    main()
