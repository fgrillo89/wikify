# wikify -- Distillation playbook

A reproducible procedure for running, reviewing, and iteratively improving the wikify distillation pipeline on any corpus. Designed as a closed loop: run, evaluate, diagnose, refine, re-run.

## When to run

- After any change to: handler skills, validator, extract/write prompts, renderer, explorer, strategy, or cost meter.
- After a dependency bump (pymupdf, jinja2, fastembed, pydantic).
- Before publishing a benchmark report.
- After scaling to a new corpus or corpus size.

---

## Part 1 -- Setup

### 1.1 Corpus

Ingest a corpus (any size; start small and scale up):

```bash
uv run python -m wikify.cli ingest <input_dir> --out data/corpora/<name>
```

Ingest parallelizes over 60% of CPU cores; use `--workers 1` for serial debugging.

After ingest, verify:

```bash
ls data/corpora/<name>/docs/ | wc -l
cat data/corpora/<name>/vectors.meta.json
# expect: {"backend":"fastembed","dim":384,"model":"sentence-transformers/all-MiniLM-L6-v2"}
```

### 1.2 Clean run directories

```bash
rm -rf data/test_runs/<run_name>
rm -rf data/dispatch/*/*.{request,response,error}.json
mkdir -p data/dispatch/{extract,write,compact,edit,orchestrate,query}
```

### 1.3 Variables

```bash
CORPUS=data/corpora/<name>
BUNDLE=data/test_runs/<run_name>
```

---

## Part 2 -- Scripted run

The scripted mode tests the deterministic explorer + model-backed dispatch.

```bash
uv run python -m wikify.cli distill \
  --strategy M --mode scripted \
  --budget <heq> --seed 0 --iteration create \
  --corpus $CORPUS --bundle $BUNDLE
```

For multi-iteration runs, use `campaign`:

```bash
uv run python -m wikify.cli campaign \
  --strategy M --mode scripted \
  --budget <heq> --iterations 3 --seed 0 \
  --corpus $CORPUS --bundle $BUNDLE
```

### Servicing dispatches

Each distill invocation blocks on `data/dispatch/<role>/<rid>.request.json`. A parallel Claude Code session running the `/wikify/runtime/serve-dispatch` skill services them. Watch `_run.json::write_rejections` after each iteration -- a non-empty list means structural issues in writer output.

---

## Part 3 -- Guided run

The guided mode lets the orchestrator decide sampling, tiers, and allocation.

```bash
uv run python -m wikify.cli distill \
  --strategy M --mode guided \
  --budget <heq> --seed 0 --iteration create \
  --corpus $CORPUS --bundle $BUNDLE
```

Expect `orchestrate/*.request.json` files in addition to extract/write. The orchestrator runs at tier L; each decision costs ~30k heq. Active sampling actions are cached for 8 batches before re-querying.

---

## Part 4 -- Render and eval

```bash
uv run python -m wikify.cli html  --bundle $BUNDLE
uv run python -m wikify.cli eval  --bundle $BUNDLE --corpus $CORPUS
```

---

## Part 5 -- Quality review checklist

**Do not skip any step.** Every step produces evidence for the review report.

### 5.1 Pipeline sanity

```bash
cat $BUNDLE/_run.json | python -m json.tool | head -80
```

Check:
- `wall_seconds`: baseline for regressions
- `budget_used_haiku_eq`: should be <= 105% of target
- `by_role.*`: non-zero calls for every expected role
- `policy_actions`: every entry has `action`, `n_chunks`, `stop`
- `write_rejections`: should be empty
- Cache ratio: `n_cached_skipped` vs `n_new_extracted`

### 5.2 File counts

```bash
ls $BUNDLE/articles/ | wc -l
ls $BUNDLE/people/ | wc -l
ls $BUNDLE/_html/articles/ 2>/dev/null | wc -l
ls $BUNDLE/_html/people/ 2>/dev/null | wc -l
```

HTML counts should match non-skeleton page counts. A large mismatch means skeleton filtering is active (expected) or a bug (investigate).

### 5.3 Metrics

```bash
cat $BUNDLE/_metrics.json
```

Sanity checks:
- `M1_coverage_residual`: lower is better (0.35-0.55 typical for small corpora)
- `M3_g_evidence.modularity`: >= 0.3 for a crystalline wiki
- `M6_grounding.passes`: `true` (g1 >= 0.9, g2 >= 0.99)
- Any metric at `0.0` when it should be positive is a red flag

### 5.4 Rendered HTML review (the critical part)

Open `$BUNDLE/_html/index.html` in a browser. **Do not review markdown.**

**Index page:**
- [ ] Lists only real pages (no skeletons)
- [ ] Navigation links resolve
- [ ] Page count matches expectation

**Article samples** -- open at least 3 (largest, smallest non-skeleton, middle):
- [ ] At least 2 H2 sections before `## References`
- [ ] Meaningful section labels (not placeholders)
- [ ] Reads like a Wikipedia article: neutral, connected paragraphs
- [ ] No `[[wikilinks]]` in body
- [ ] `[^eN]` markers resolve to evidence entries
- [ ] No meta-commentary ("this article appears in the corpus")
- [ ] Images referenced in preceding prose

**People samples** -- open at least 3:
- [ ] Describes the person, NOT "their appearance in this corpus"
- [ ] Lead paragraph: bold name + biographical context
- [ ] Publications render as proper HTML lists
- [ ] References section with resolvable markers

### 5.5 Dispatch errors

```bash
find data/dispatch -name "*.error.json" | head -20
```

More than 5 errors in a single role signals a handler-prompt issue.

### 5.6 Known failure patterns

```bash
grep -rn "appears in this corpus" $BUNDLE/articles/ $BUNDLE/people/ 2>/dev/null
grep -rn '\[\[' $BUNDLE/articles/ $BUNDLE/people/ 2>/dev/null
find $BUNDLE/articles $BUNDLE/people -name "*.md" -size -300c 2>/dev/null | head
```

All greps should return empty.

---

## Part 6 -- Diagnostic lineage trace

Required when investigating quality regressions or validating pipeline changes.

### 6.1 Dossier health

```bash
cat $BUNDLE/_run.json | python -m json.tool | grep -A6 dossier_summary
```

`n_empty / n_total` should be < 0.2. If high:
- Check `io_lineage/<run_id>/chunks_read.json` for references-section chunks (should be absent)
- Check `extract_candidates.json` for empty `definition`/`summary` fields

### 6.2 Per-page lineage (sample 5 pages)

For each page:
1. Open `_dossiers/<slug>.json` -- at least one substantive entry
2. Open `_write_requests/<page_id>.request.json` -- `dossier_context_yaml` non-empty
3. Trace back through `io_lineage` to confirm chunk `section_type` is valid
4. Open rendered HTML -- confirm encyclopedic body, not a stub

### 6.3 Write request check

```bash
python - <<'EOF'
import json, pathlib, sys
bundle = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path("$BUNDLE")
wr_dir = bundle / "_write_requests"
empty = [f.name for f in sorted(wr_dir.glob("*.request.json"))
         if not json.loads(f.read_text()).get("dossier_context_yaml", "").strip()]
total = len(list(wr_dir.glob("*.request.json")))
print(f"Empty dossier_context_yaml: {len(empty)}/{total}")
for name in empty[:10]: print(" ", name)
EOF
```

Expect 0 or near-0 empty.

---

## Part 7 -- Verbalization

Run with `--verbalize` for diagnostic insight (adds ~30-60 tokens per call):

```bash
uv run python -m wikify.cli distill \
  --strategy M --mode scripted --verbalize \
  --budget <heq> --seed 0 --iteration create \
  --corpus $CORPUS --bundle $BUNDLE
```

Read the log:

```bash
jq -r '[.role, .rid, (.reasoning | .[:80])] | @tsv' $BUNDLE/_meta/verbalize.jsonl | head -30
```

Look for: repeated excuses (tighten upstream), reasoning/output disagreement (ambiguous prompt), orchestrator picking actions for wrong reasons (bad snapshot).

Feed findings back: edit the handler skill, commit with a reference to the specific verbalize entries.

---

## Part 8 -- Dossier editor review

The dossier is the staging ground for the writer. Thin dossiers produce thin articles.

For 3 sampled pages (large, medium, thin-evidence):
1. Open `_dossiers/<slug>.json`
2. Per entry: does `definition` explain the concept (50-200 words)? Does `summary` explain this chunk's contribution (80-200 words)? Are parameters/mechanisms populated where the chunk supports them?
3. Count pass/fail. If >20% fail, the extract budget is wasted. Tighten the extract prompt or shift budget allocation.

Across iterations, the substantive fraction should rise:

```bash
for f in $BUNDLE/_meta/io_lineage/*/dossier_entries.json; do
  python -c "import json; d=json.load(open('$f')); s=sum(1 for e in d if e.get('is_substantive')); print(f'{f}: {len(d)} total, {s} substantive')"
done
```

---

## Part 9 -- Quality review against Wikipedia

Before reviewing generated pages, read 2 real Wikipedia articles in the corpus's domain as reference. The standard is: "would a reader landing on this page expect Wikipedia-quality content?"

### Reviewer role (for subagent review)

Spawn a tier-L reviewer with this prompt:

```
You are a scientific encyclopedia editor. Review each generated wiki
page against the Wikipedia Manual of Style and 2 reference Wikipedia
articles in the same domain.

For each page produce:
1. Fidelity to evidence: sample 3 claims, verify [^eN] markers
2. Structure vs exemplar: section layout comparison
3. Prose quality: neutral, declarative, connected? Meta-commentary?
4. Specificity: specific numbers/techniques or vague generalizations?
5. One improvement to the handler skill or artifact template
6. One improvement to the extract prompt or explorer

Score 1-5 per dimension. Do not hedge.
```

### Per-page checklist

**Lead**: bold title, single-clause definition, 3-5 sentence lead, cited, no meta-commentary.

**Body**: >= 2 non-appendix H2 sections, meaningful labels, reader-friendly order, >= 2 paragraphs per section.

**Prose**: zero em-dashes, zero wikilinks, active voice for results, one concept per sentence, specific numbers.

**Evidence**: every `[^eN]` resolves, evenly cited, quantitative claims cite specific evidence.

**Figures**: every embed has preceding mention, placed near relevant section.

**Cross-links**: see-also items exist in bundle, page reachable from index.

Declare the run "good" when >= 90% of checklist items PASS across the sample.

---

## Part 10 -- Autoresearch improvement loop

Inspired by Karpathy's autoresearch: run, evaluate, diff, refine, re-run.

### 10.1 Set targets before starting

Pick 3-5 metrics with explicit target values and hard floors:

| Metric | Target | Hard floor |
|--------|--------|------------|
| `M1_coverage_residual` | <= X | Y |
| Reviewer checklist pass rate | >= 85% | 50% |
| `wall_seconds` per iteration | <= T | 2T |
| `dossier_summary.n_empty / n_total` | <= 0.10 | 0.30 |

Hard floor = stop and escalate if a run falls below this. The loop must not trade one metric for another past the floors.

### 10.2 Loop steps

Each iteration:

1. **Run**: campaign with `--verbalize`
2. **Render + eval**
3. **Review**: walk Parts 5, 6, 7, 8, 9
4. **Diff**: compare metrics to previous iteration
5. **Refine**: make ONE targeted edit addressing the biggest gap. Candidates:
   - Handler skill prompt (based on verbalize findings)
   - Extract BAD/GOOD examples (based on dossier review)
   - Explorer filter or action vocabulary
   - Budget split (`--exploit-fraction`)
   - Artifact template
6. **Commit** the edit referencing the specific metric/finding
7. **Re-run** from step 1

### 10.3 Termination

Stop when:
- All targets met (success), OR
- Any metric below hard floor (escalate), OR
- Same refine fails to move metrics twice (local minimum; escalate)

### 10.4 Logbook

Keep a running log (not auto-generated):

```
## Iteration N (date)
- Corpus: <name> (<n> docs, <n> chunks)
- Metrics: M1=X (delta), reviewer=Y% (delta), wall=Zs (delta)
- Verbalize findings: <what the reasoning revealed>
- Refine: <what was changed and why>
- Commit: <hash>
- Hypothesis: <what we expect next iteration>
```

### 10.5 Scaling progression

The loop is designed to run on incrementally larger corpora:

1. **Small** (10-20 docs): fast iteration, find prompt/skill bugs
2. **Medium** (50-100 docs): strategy differentiation becomes meaningful, test budget allocation
3. **Large** (200-1000 docs): scaling regressions, real metric curves, meaningful M3 communities

At each scale transition, re-run the full playbook from Part 1. Metrics from smaller corpora are baselines, not targets -- absolute values change with corpus size.

---

## Appendix -- What past reviews missed

This playbook was written after a review that declared output "high quality" based on one page's markdown. The rendered HTML had:

- Article bodies with zero in-body section headings
- People pages leading with "appears in this corpus only through citations"
- Bullet lists rendered as run-on prose
- Skeleton pages in the index
- Campaign run with all empty concepts

Those failure modes are now explicit checkboxes in Part 5. The playbook exists so the same class of miss does not recur.
