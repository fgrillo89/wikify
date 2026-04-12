# wikify — Test-run playbook

A reproducible procedure for running and reviewing a wikify test campaign. Use this any time you run the pipeline end-to-end to assess quality. The previous test runs missed concrete failure modes (unsegmented article bodies, meta-comment people pages, broken bullet lists, skeleton pages polluting the index) because the review relied on spot-checking one page's markdown. This playbook exists so the same class of miss does not happen again.

## When to run

- After any change to: the writer prompt stack, the validator, the extract or write handlers, the renderer, the sampler, the policy, or the cost meter.
- After a dependency bump that touches `python-markdown`, `jinja2`, `sentence-transformers`, or any schema library.
- Before publishing a pipeline release or a benchmark report.
- Whenever a prior review has turned up "it looks fine" without the reviewer having opened the rendered HTML.

---

## Part 1 — Setup

### 1.1 Corpus

Use `data/corpora/mvp20_v7` for fast iteration (20 materials science papers, ~770 chunks, 117 caption-only image binaries, 112 equations, 84 figure_refs, 989 citation entries, 65 resolved cross-paper edges). If it is stale or missing, rebuild it:

```bash
WIKIFY_EMBEDDER=fastembed \
uv run python -m wikify.cli ingest \
  data/papers/mvp20 \
  --out data/corpora/mvp20_v7
```

Ingest is parallelized over 60% of CPU cores by default; pass `--workers N` to override (use `--workers 1` for serial debugging).

After ingest, verify:

```bash
ls data/corpora/mvp20_v7/docs/ | wc -l   # expect 20
cat data/corpora/mvp20_v7/vectors.meta.json
# expect: {"backend":"fastembed","dim":384,"model":"sentence-transformers/all-MiniLM-L6-v2"}
```

### 1.2 Clean bundle directories

```bash
rm -rf data/test_runs/scripted data/test_runs/campaign
rm -rf data/dispatch/*/*.request.json data/dispatch/*/*.response.json data/dispatch/*/*.error.json
mkdir -p data/dispatch/{extract,write,compact,edit,orchestrate,query}
```

### 1.3 Environment

```bash
export WIKIFY_EMBEDDER=fastembed
export WIKIFY_DISPATCH_DIR=data/dispatch   # default
```

---

## Part 2 — Scripted run (scripted + file_dispatch)

3 iterations, 50k heq budget per iteration, strategy M. The scripted mode tests the deterministic sampler + model-backed dispatch path.

### 2.1 Commands (one per iteration)

**Iteration 1 — create**

```bash
uv run python -m wikify.cli distill \
  --strategy M --mode scripted \
  --budget 50000 --extract-tier S --write-tier M \
  --exploit-fraction 0.65 --seed 0 --iteration create \
  --corpus data/corpora/mvp20_v7 \
  --bundle data/test_runs/scripted
```

Note: `--bundle` (not `--out`) pins every iteration to the same path. Without it, `create` writes to a timestamped subdir and `refine` writes to the parent — a known footgun.

**Iterations 2 and 3 — refine** (same budget, incremented seed)

```bash
uv run python -m wikify.cli distill \
  --strategy M --mode scripted \
  --budget 50000 --extract-tier S --write-tier M \
  --exploit-fraction 0.65 --seed 1 --iteration refine \
  --corpus data/corpora/mvp20_v7 \
  --bundle data/test_runs/scripted
# then seed 2 for iteration 3
```

### 2.2 Servicing dispatches

Each distill invocation blocks on `data/dispatch/<role>/<rid>.request.json` files. A parallel Claude Code session running `/wikify/runtime/serve-dispatch` is the production way. Within a single conversation, service each request by reading the file, spawning a tier-appropriate Task subagent with the handler-skill prompt, and writing the response. Watch `_run.json::write_rejections` after each iteration — a non-empty list means the writer produced bodies that failed the validator, which is a structural issue worth investigating before continuing.

---

## Part 3 — LLM campaign run (guided + file_dispatch)

1-3 iterations depending on budget (200k heq / iter is the realistic floor per Phase 5B of the structural-improvements plan; 30k-50k is the smoke-test range). The orchestrator decides sampling, tiers, and allocation.

### 3.1 Command

```bash
uv run python -m wikify.cli distill \
  --strategy M --mode guided \
  --budget 200000 --seed 0 --iteration create \
  --corpus data/corpora/mvp20_v7 \
  --bundle data/test_runs/campaign
```

The orchestrator's initial decisions are expected to be `jump_uniform` or `jump_pagerank`. Budget overshoot of up to ~30 k heq per decision is expected (orchestrator at tier L). Watch for the LLM policy cache: after Phase 1/5 of the structural plan lands, each active action persists for 8 batches before the next orchestrator call.

### 3.2 Servicing dispatches (guided specific)

Same as §2.2 but expect `orchestrate/*.request.json` files too. The orchestrator handler needs more context than the extract handler — the current payload is minimal but will grow when Phase 3 of the plan (sampler_snapshot) lands.

---

## Part 4 — Render and eval

After each run (scripted and campaign), produce the HTML and the metrics:

```bash
BUNDLE=data/test_runs/scripted  # or campaign
uv run python -m wikify.cli html  --bundle $BUNDLE
uv run python -m wikify.cli eval  --bundle $BUNDLE --corpus data/corpora/mvp20_v7
```

Time both commands. HTML rendering on mvp20_v7 should be ~2 s and eval ~15 s after the Phase-4-of-previous-plan author filter. If either is >30 s, something regressed.

---

## Part 5 — Quality review checklist

**This is the part the previous review failed.** Do not skip any step. Every step produces evidence you enumerate in the review report.

### 5.1 Pipeline-level sanity (3 minutes)

```bash
cat $BUNDLE/_run.json | python -m json.tool | head -80
```

Check:
- `wall_seconds`: baseline for future regressions
- `budget_used_haiku_eq`: should be ≤ 105 % of target after Phase 5C lands
- `by_role.*`: non-zero calls for every role you expect (extractor, writer; editor and compactor only on refine)
- `policy_actions`: every entry has `action`, `n_chunks`, `stop`
- `write_rejections`: should be empty. If not, dig into each entry before proceeding.
- `n_cached_skipped` + `n_new_extracted`: cache hit ratio is expected to be ~0 for coverage_gap refine iterations

### 5.2 Bundle-level file counts (1 minute)

```bash
ls $BUNDLE/concepts/ | wc -l   # or articles/ after 6D lands
ls $BUNDLE/people/ | wc -l
ls $BUNDLE/_write_requests/ 2>/dev/null | wc -l
ls $BUNDLE/_html/concepts/ 2>/dev/null | wc -l  # or articles/
ls $BUNDLE/_html/people/ | wc -l
```

For mvp20 with 50k heq × 3 iterations, realistic numbers:
- concepts/articles: 60-100 (most are skeletons today; after 6C lands, only the real ones are rendered)
- people: 50-300 (after 6B lands, only the ones the model actually wrote)
- write_requests: same order as concepts
- _html/articles should match the non-skeleton concept count (after 6C)
- _html/people should match the non-skeleton people count

A 10× mismatch between the concepts directory count and the _html/articles count means 6C is filtering skeletons — good. A mismatch in the other direction (HTML has more than concepts) is a bug.

### 5.3 Metrics (1 minute)

```bash
cat $BUNDLE/_metrics.json
```

Sanity values:
- `M1_coverage_residual`: 0.35-0.55 (lower is better)
- `M3_g_evidence.modularity`: ≥ 0.5 (crystalline wiki)
- `M3_g_evidence.n_nodes`: same as non-skeleton page count
- `M5_hit_rate`: > 0 (after we have real evidence resolution)
- `M6_grounding.passes`: `true` (g1 ≥ 0.9, g2 ≥ 0.99)
- After Phase 4: `image_coverage_residual`, `figure_reference_rate`, `n_figures_referenced_in_bodies` are present

Any metric that is `0.0` when it should be positive is a red flag. Chase it before you look at the HTML — but don't stop here either.

### 5.4 Rendered HTML review (10 minutes — the critical part)

Open `file:///$BUNDLE/_html/index.html` in a browser. Do not look at markdown.

**Index page checks:**
- [ ] Does the index list only real pages (no skeletons)?
- [ ] Are labels "Articles" and "People" (after 6D), or "Concepts" and "People" (before 6D)?
- [ ] Do navigation links resolve?
- [ ] Is the total page count close to the expected non-skeleton count?

**Article (concept) page samples** — open **at least 3** spanning:

1. The largest article by file size
2. The smallest non-skeleton article
3. A middle-sized one, ideally cross-linked to other articles

For each, check:
- [ ] Does the body have at least 2 in-body H2 sections before `## References`? (Not just `## References` alone — that was the Memristor bug.)
- [ ] Section headings have meaningful labels (e.g. Definition, Background, Mechanism), not generic placeholders
- [ ] Prose reads like a Wikipedia article: neutral third person, connected paragraphs, no em-dashes as parenthetical separators
- [ ] No `[[wikilinks]]` appear in the body (they should be silently resolved or removed)
- [ ] Every `[^eN]` marker in the prose resolves to an evidence entry — click a few footnote links
- [ ] No meta-commentary like "this article appears in the corpus"
- [ ] Images, if present, are referenced in the preceding prose and display correctly
- [ ] The page title matches the first H1 (`# Title`)

**People page samples** — open **at least 3** spanning:

1. A well-known researcher (e.g. Leon Chua in the mvp20 corpus)
2. A mid-tier author with a few papers
3. A person mentioned in text but not an author (cited only)

For each, check:
- [ ] Does the page describe the person, NOT their appearance in "this corpus"? (The Bhaswar Chakrabarti "appears only through citations" phrasing was the bug.)
- [ ] The lead paragraph starts with the person's name in bold followed by biographical context
- [ ] Publications and collaborators, if listed, render as real HTML `<ul>` elements, not run-on text or `- item` fragments
- [ ] No stray `1.` or `2.` artifacts (the Chia-Yu Chang bug)
- [ ] Publication titles that match existing article pages are hyperlinked
- [ ] No duplicate "Cited in X" bullets
- [ ] References section present with resolvable `[^eN]` markers

**Broken-list stress test**: open the "Publications in this corpus" section on a page that has 5+ publications. Verify they render as distinct list items with vertical whitespace, not concatenated prose.

### 5.5 Dispatch error files

```bash
find data/dispatch -name "*.error.json" | head -20
```

Read each one. The errors are Pydantic validation failures. Typical causes:
- Title with trailing punctuation ("X (memristor)")
- Quote not a verbatim substring of chunk_text
- Missing required fields

If you see >5 errors in a single role, there is a handler-prompt quality issue — the subagent is producing the same class of invalid output repeatedly. Document it.

### 5.6 Campaign-specific checks

For the LLM campaign run, additionally:
- [ ] Read `_run.json::policy_actions`. How many `orchestrate` dispatches happened? How many were control actions (`set_tier`, `set_allocation`) vs active sampling?
- [ ] Did the orchestrator ever pick `done`, or did the budget abort first?
- [ ] Are the `pick_chunks` decisions (after Phase 3 lands) grounded in sensible choices?
- [ ] Compare against the scripted run's concept count: the LLM campaign should not be dramatically worse, and if it is, the orchestrator is wasting budget.

### 5.7 Grep for known failure patterns

```bash
# Meta-commentary that should have been rewritten
grep -rn "appears in this corpus" $BUNDLE/concepts/ $BUNDLE/people/ 2>/dev/null

# Wikilinks leaking into bodies
grep -rn '\[\[' $BUNDLE/concepts/ $BUNDLE/people/ 2>/dev/null

# Empty pages
find $BUNDLE/concepts $BUNDLE/people -name "*.md" -size -300c 2>/dev/null | head

# Skeleton pages in _html
find $BUNDLE/_html -name "*.html" -size -500c 2>/dev/null | head
```

All four greps should return empty (or near-empty) after Phase 6 lands. Today they return many matches — use them as progress indicators.

---

## Part 6 — Reporting

Write a review that enumerates:

1. **Timing**: wall time per iteration, per role. Use `_run.json::wall_seconds` and `by_role.*.wall_seconds`.
2. **Metric deltas** from the previous run (baseline).
3. **Concrete HTML issues** — every single problem from §5.4, with a sample page path.
4. **Dispatch errors** — count by role, representative messages, root-cause hypothesis.
5. **Cache behavior** — hit rate, novelty rate.
6. **Recommendations** — what to fix before the next run.

**Do not write "the output looks good" without enumerating the above.** That's the failure mode this playbook prevents.

---

## Part 7 — Diagnostic test run: full input/output tracking

This section is REQUIRED when investigating quality regressions or validating pipeline changes that touch the extract or write paths. Its purpose is to trace every produced wiki page back through the full I/O lineage that generated it.

### 7.1 Lineage files location

After every distill run, the pipeline writes per-run lineage under:

```
<bundle>/_meta/io_lineage/<run_id>/
  chunks_read.json          # every chunk the sampler sent to the extractor
  extract_candidates.json   # every concept the extractor emitted
  dossier_entries.json      # every dossier entry with substantive flag
```

The run summary at `<bundle>/_run.json` also carries a `dossier_summary` object:

```json
{
  "dossier_summary": {
    "n_total": 180,
    "n_substantive": 142,
    "n_empty": 38,
    "n_dossiers": 47
  }
}
```

A stderr warning is emitted automatically when `n_empty / n_total > 0.2` (20% threshold).

### 7.2 Dossier health check (required before HTML review)

```bash
cat $BUNDLE/_run.json | python -m json.tool | grep -A6 dossier_summary
```

- `n_empty / n_total` should be < 0.2 after the references-section filter and prompt tightening.
- If the ratio is high, check `io_lineage/<run_id>/chunks_read.json` for `section_type == "references"` entries — these should be absent after the fix.
- If references chunks are absent but `n_empty` is still high, check `extract_candidates.json`: look at `definition_words` and `summary_words`. If most are 0, the extractor subagent is not following the content rules — re-read and re-run the extract handler prompt.

### 7.3 Per-page lineage trace (sample 5 random pages)

For each of 5 randomly selected wiki pages:

1. **Identify the page_id** from `<bundle>/concepts/<page_id>.md` or `<bundle>/people/<page_id>.md`.
2. **Find its dossier** at `<bundle>/_dossiers/<slug>.json`. Confirm it has at least one substantive entry (non-empty `definition` or `summary`).
3. **Find its write request** at `<bundle>/_write_requests/<page_id>.request.json`. Check `dossier_context_yaml` — it should be non-empty YAML with at least `definition` or `summary` populated.
4. **Trace back to lineage**: open `io_lineage/<run_id>/dossier_entries.json` and filter by `page_id`. Confirm `is_substantive: true` for at least one entry.
5. **Trace back to chunks**: filter `chunks_read.json` by the `chunk_id`s from step 4. Confirm `section_type` is NOT `references/acknowledgments/appendix`.
6. **Open the rendered HTML** for the page. Confirm the body is encyclopedic and references the dossier material (not a stub or skeleton).

If ANY step in the chain breaks — missing dossier, empty YAML, all entries `is_substantive: false`, references-section chunk_ids, stub HTML — record it as a failure and investigate before declaring the run good.

### 7.4 Write request YAML check

```bash
python - <<'EOF'
import json, pathlib, sys
bundle = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path("data/test_runs/scripted")
wr_dir = bundle / "_write_requests"
empty = []
for f in sorted(wr_dir.glob("*.request.json")):
    req = json.loads(f.read_text())
    if not req.get("dossier_context_yaml", "").strip():
        empty.append(f.name)
print(f"Write requests with empty dossier_context_yaml: {len(empty)}/{len(list(wr_dir.glob('*.request.json')))}")
for name in empty[:10]:
    print(" ", name)
EOF
```

Expect 0 or near-0 write requests with empty `dossier_context_yaml`. A high count means the dossier store is not populated before `build_write_request` runs — investigate the ordering in `pipeline.py`.

---

## Part 8 — Verbalization-driven refinement (ad-hoc runs)

Distill and campaign accept `--verbalize` (off by default). When set, every extract, write, and orchestrator call includes a 1-3 sentence `reasoning` field on its response and the pipeline appends each to `<bundle>/_meta/verbalize.jsonl`:

```json
{"run_id": "...", "when": "2026-04-11T15:23:11+00:00", "role": "write", "rid": "Atomic Layer Deposition", "reasoning": "..."}
```

### 8.1 When to use it

- **Ad-hoc diagnostic runs only.** Verbalization adds ~30-60 tokens per call. Do NOT leave it on for production campaigns.
- **Before refining a handler skill.** If a handler is producing thin output, run a 1-iteration verbalized scripted run on mvp20_v7 and read the reasoning lines. The model's own explanation of its choices is the fastest signal that the skill prompt is ambiguous, under-specified, or contradicting itself.
- **After changing the sampler, policy, or budget allocation.** The orchestrator reasoning lines show whether the policy is picking actions for the reasons you expected.

### 8.2 How to read the log

```bash
BUNDLE=data/test_runs/scripted
jq -r '[.role, .rid, (.reasoning | .[:80])] | @tsv' $BUNDLE/_meta/verbalize.jsonl | head -30
```

Group by `role`. Look for:
- **Repeated excuses** ("evidence was too thin", "chunk had no structure") — tighten the upstream stage (usually sampler or extract prompt).
- **Disagreement between writer reasoning and produced body** — the skill prompt is ambiguous; rewrite the relevant section of `handlers/write.md`.
- **Orchestrator picking actions for unrelated reasons** — the sampler_snapshot isn't giving the orchestrator what it needs; revisit Phase 3.

### 8.3 Feeding findings back into skills

After every verbalized run: write a 5-bullet summary of what the reasoning revealed AND a concrete edit to a handler skill (usually 1-3 lines). Commit the edit with a message referencing the specific verbalize entries that motivated it. This turns the playbook into a closed loop: run → read reasoning → edit skill → re-run.

---

## Part 9 — Editor review of dossiers (prompting / budget refinement)

The dossier layer (`<bundle>/_dossiers/<slug>.json`) is the authoritative staging ground for the writer. If dossiers are thin, the writer cannot produce a Wikipedia-quality article no matter how good the skill prompt is. Budget spent on extract that does not land in a substantive dossier is wasted.

### 9.1 The editor check (read every dossier for one page like an editor)

For each of 3 sampled pages (one large, one medium, one thin-evidence):

1. Open `<bundle>/_dossiers/<slug>.json`.
2. For EACH entry, answer:
   - **Does the `definition` explain what the concept IS in 50-200 words?** An empty or single-sentence definition is a failure.
   - **Does the `summary` explain how THIS chunk's content contributes to the concept in 80-200 words?** Should be specific to the chunk (e.g. "this chunk shows that HfO2 ALD growth rate saturates at 1.1 Å/cycle at 200 °C"), not generic.
   - **Are parameters, mechanisms, and relationships populated where the chunk supports them?** Empty is OK only if the chunk genuinely has no quantitative or mechanistic content.
   - **Is the chunk's `section_type` one of `abstract | methods | results | discussion | introduction`?** Anything in `references/acknowledgments/appendix` should not be here — if present, the sampler filter is broken.
3. Count: entries that pass all four / entries that fail.
4. If >20% of entries fail, the write budget is being spent on empty context. Two levers:
   - **Tighten the extract handler prompt** (already requires 50-200 word definitions and 80-200 word summaries — but the model may still ignore). Add 2-3 concrete BAD vs GOOD examples from the actual failing entries.
   - **Shift budget** from extract to write (reduce `exploit_fraction`) or from write to extract (raise it) depending on which phase is starving.

### 9.2 Dossier evolution across iterations

In a multi-iteration campaign, dossiers should GROW in substantive content, not just in entry count. Compare `_meta/io_lineage/<run_id>/dossier_entries.json` across iterations:

```bash
for f in $BUNDLE/_meta/io_lineage/*/dossier_entries.json; do
  python -c "import json,sys; d=json.load(open('$f')); subs=sum(1 for e in d if e.get('is_substantive')); print('$f', len(d), subs)"
done
```

Substantive fraction should rise across iterations. If it stays flat, refine loops are repeating the same extract mistakes.

---

## Part 10 — Wikipedia-exemplar quality review

### 10.1 Reference pages (mandatory reads before review)

For the mvp20_v7 corpus (memristor + ALD + HfOx + neuromorphic), the two reference Wikipedia pages to compare against are:

- **<https://en.wikipedia.org/wiki/Memristor>** — directly overlaps corpus content. Canonical example of an article covering a device concept with history, physics, theoretical debate, and applications, all in neutral voice with inline citations.
- **<https://en.wikipedia.org/wiki/Atomic_layer_deposition>** — materials-science article covering the process, mechanism, precursor chemistry, and device applications. Canonical example of a process/method article grounded in quantitative detail.

Before reviewing ANY generated page, read both reference pages end-to-end. Then when reviewing a generated page, ask: "If a reader landed on this page expecting Wikipedia-quality content, would they feel they got it?"

### 10.2 The wiki reviewer role (prompt template)

When you need an independent quality pass, spawn a reviewer subagent with this role prompt:

```
You are a scientific encyclopedia editor with expertise in solid-state
electronics and thin-film materials. Your job: review a generated wiki
page against the Wikipedia Manual of Style and against two exemplar
reference pages (Memristor, Atomic layer deposition).

For each page I give you, produce:

1. **Fidelity to evidence**: does every claim in the page have a
   matching [^eN] marker whose evidence entry actually supports the
   claim? Sample 3 random claims and verify.

2. **Structure vs exemplar**: does the page's section layout match the
   information-density pattern of the reference pages (lead,
   mechanism/process sections, applications, open questions,
   references)? Flag missing or redundant sections.

3. **Prose quality**: is the prose neutral, declarative, connected?
   Are there em-dashes as parenthetical separators, meta-commentary
   about "the corpus", hedged superlatives, or rule-of-three reflexes?

4. **Specificity**: does the page cite specific numbers, techniques,
   and conditions, or does it generalize vaguely? Wikipedia articles
   on materials name specific films, temperatures, and precursors.

5. **One concrete structural improvement** I could make by editing
   the handler skill or the artifact template.

6. **One concrete structural improvement** I could make by changing
   the extract prompt or the sampler.

Produce your output as a scored rubric (1-5 per dimension) plus the
two improvement suggestions. Do not hedge.
```

The reviewer runs at tier L (editor-level). It is NOT part of the distill loop — it is invoked explicitly during playbook review.

### 10.3 Detailed quality checklist (applied per page)

For each sampled page, walk this list and mark each item PASS/FAIL/PARTIAL. The playbook declares the run "good" only when >=90% of items PASS across the sample.

**Lead section**:
- [ ] First sentence bolds the page title.
- [ ] First sentence is a single-clause "X is a Y" definition, <=20 words.
- [ ] Lead paragraph is 3-5 sentences, self-contained, no jargon that isn't defined or resolvable from context.
- [ ] Lead paragraph cites at least one `[^eN]` marker.
- [ ] No meta-commentary ("this article appears in the corpus", "we examine").

**Body structure**:
- [ ] At least 2 non-appendix `## H2` sections before `## References`.
- [ ] Section labels are meaningful (Background, Mechanism, Applications, etc.), not placeholder.
- [ ] Sections are in a reader-friendly order: context -> mechanism -> applications -> open questions.
- [ ] No section has fewer than 2 paragraphs.
- [ ] No section exceeds 8 paragraphs without sub-headings.

**Prose quality**:
- [ ] Zero em-dashes as parenthetical separators.
- [ ] Zero `[[wikilinks]]` leaking into prose.
- [ ] Active voice in results/discussion, passive in methods.
- [ ] One concept per sentence; one relative clause max.
- [ ] Specific numbers, not "several" / "some" / "many".
- [ ] No rule-of-three reflexes ("A, B, and C" triplets every sentence).

**Evidence and citations**:
- [ ] Every in-prose `[^eN]` marker has a matching `[^eN]:` definition.
- [ ] Every `[^eN]:` definition includes a real `chunk_id` (not a placeholder).
- [ ] Claims are evenly cited (no 10-sentence paragraph with one citation at the end).
- [ ] Quantitative claims cite the specific evidence that contains the number.

**Figures**:
- [ ] Every embedded `![Figure N](path)` has a preceding sentence mentioning "Figure N".
- [ ] Figures are placed next to the relevant section, not grouped at the top.
- [ ] No broken figure paths.

**Cross-links**:
- [ ] `## See also` (if present) lists only pages that actually exist in the bundle.
- [ ] The page is reachable from the index.
- [ ] The page's own links (from frontmatter) resolve.

**Comparison vs Wikipedia exemplar**:
- [ ] The page has the same TYPE of sections as the exemplar (process article vs device article vs person article).
- [ ] Information density is comparable — no paragraphs that are a single sentence with four citations.
- [ ] The page would NOT embarrass a subject-matter expert if published unedited.

### 10.4 Walkthrough procedure

1. Pick 5 generated pages: 1 large article, 1 small article, 1 people page with `author_context`, 1 people page WITHOUT `author_context`, 1 page in the middle of a cross-link cluster.
2. Open each in the rendered HTML (`_html/articles/<id>.html` / `_html/people/<id>.html`), NOT the markdown.
3. Walk the checklist. Mark every item.
4. For any FAIL, note the file and line range AND a hypothesis about which upstream stage caused it (extract prompt, sampler, writer prompt, artifact template, validator).
5. Produce a per-page summary and an aggregate pass rate.

---

## Part 11 — Autoresearch-inspired improvement loop

Inspired by Karpathy's autoresearch: run, evaluate, diff, refine, re-run. Stop when a quality target is met or budget is exhausted. The loop is designed to improve BOTH page quality and cost/wall-time.

### 11.1 Target metrics (decided before the loop starts)

Pick 3 metrics and set explicit target values. Example targets for mvp20_v7 scripted runs:

| Metric | Baseline (current) | Target | Hard floor |
|---|---|---|---|
| `M1_coverage_residual` | 0.45 | <= 0.35 | 0.50 |
| Reviewer checklist pass rate | ~60% | >= 85% | 50% |
| `wall_seconds` per iteration | 280 | <= 180 | 360 |
| `budget_used_haiku_eq` | 49000 | <= 45000 | 52500 |
| `dossier_summary.n_empty / n_total` | 0.25 | <= 0.10 | 0.30 |

"Hard floor" means "stop the loop and escalate if a run falls BELOW this." The loop is not allowed to trade one metric for another past the floors.

### 11.2 Loop steps

Each iteration of the autoresearch loop:

1. **Run**: `wikify campaign --strategy M --iterations 3 --budget 50000 --bundle data/test_runs/loop_<iter> --verbalize --mode scripted` (and again with `guided`).
2. **Render + eval**: `wikify html --bundle <bundle>` and `wikify eval --bundle <bundle> --corpus <corpus>`.
3. **Playbook review**: walk Parts 5, 7, 8, 9, 10 in order. Produce the reviewer checklist pass rate (Part 10.3).
4. **Diff**: compare the 5 metrics to the previous iteration's values. Identify the biggest regression and the biggest gap-vs-target.
5. **Refine**: make ONE targeted edit to the skills/code that addresses the biggest gap. Candidates:
   - Handler skill prompt (tighten a rule based on verbalize.jsonl findings).
   - Extract handler BAD/GOOD examples (based on dossier editor review).
   - Sampler filter or policy action (e.g. add a new `jump_*` op).
   - Budget split (`--exploit-fraction`).
   - Artifact template (`wiki_article.md`, `wiki_person.md`).
6. **Commit** the refine with a message referencing the specific metric and reviewer finding that motivated it.
7. **Re-run** from step 1.

### 11.3 Termination

The loop stops when:
- ALL 3 picked metrics hit their targets (success), OR
- Any metric drops below its hard floor (escalate to the user), OR
- The same refine step fails to move the metrics twice in a row (local minimum; escalate).

### 11.4 What to track across the loop

Keep a running `autoresearch.md` file at the repo root (not in the bundle — this is the loop's logbook):

```
## Iteration N (2026-04-11)
- Run: campaign_M_3i_50k_scripted
- Metrics: M1=0.42 (-0.03), reviewer=72% (+12), wall=210s (-70)
- Verbalize findings: writer keeps repeating "extends existing page" without a match
- Refine: added 2 concrete BAD/GOOD extend examples to handlers/write.md
- Commit: abc123
- Hypothesis: extend judgment will improve next iter
```

This file is NEVER auto-generated. It's written by the human loop driver (or a dedicated autoresearch agent) after each iteration, because the whole point is to force the driver to articulate the hypothesis.

---

## Part 12 — What the previous run missed

For calibration: this playbook was written after a review that declared the scripted run "high quality" based solely on the markdown of one well-written page. The actual rendered output had five concrete failures that a 10-minute HTML walkthrough would have caught:

- Article body had zero in-body section headings (rule: §5.4 "at least 2 H2 before References")
- People pages led with "appears in this corpus only through citations" (rule: §5.4 "describe the person, not their appearance in this corpus")
- Bullet lists rendered as run-on prose (rule: §5.4 "render as real HTML `<ul>` elements")
- Skeleton pages appeared in the index (rule: §5.2 "index list only real pages")
- Campaign run had all empty concepts (rule: §5.2 "10× mismatch is a bug")

Those five failure modes are now explicit checkboxes above. When the next reviewer works through this list, they will catch them all.
