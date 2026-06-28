export const meta = {
  name: 'wikify-public-readiness',
  description: 'Take wikify to public-OSS quality: rename/reorg skills, de-verbose them, rewrite docs + CLAUDE.md + README, sanitize personal info & commit history (proposals), inventory dead code, package as a Claude plugin, add CI/badges. Adversarial codex review in the critical loops. Mutates the repo on the current branch; destructive ops (deletes, history rewrite) are emitted as proposals, not executed.',
  whenToUse: 'Run once when preparing the wikify repo for public release. Review each phase output under tasks/public-readiness/ before merging.',
  phases: [
    { title: 'Research', detail: 'confirm OSS + Claude-plugin + skill-authoring best practices' },
    { title: 'Audit', detail: 'parallel read-only inventories: skills, dead code, meta, secrets, commits, tests, docs' },
    { title: 'Reorg design', detail: 'design skill rename + folder reorg; codex-vetted before execution' },
    { title: 'Reorg execute', detail: 'git mv (lossless), add ingest skill, fix all cross-references' },
    { title: 'Skill rewrite', detail: 'prescriptive, de-meta, progressive disclosure; codex per skill' },
    { title: 'Docs', detail: 'hierarchical docs, CLAUDE.md, README; codex-reviewed' },
    { title: 'Sanitize', detail: 'strip meta + personal info; emit git-history rewrite proposal' },
    { title: 'Dead code', detail: 'finalize removal proposal (no deletion)' },
    { title: 'Packaging', detail: 'plugin manifest, LICENSE/CONTRIBUTING/SECURITY, CI, badges' },
    { title: 'Final review', detail: 'adversarial codex full review + completeness critic' },
  ],
}

// ---------------------------------------------------------------------------
// Goal: the four first-class, user-facing skills are the only top-level ones —
//   query   (ask a question; triage corpus vs wiki search, wiki-first),
//   arxiv   (harvest an arXiv category into a corpus),
//   wikify  (the main entry point; currently named wikify-investigate),
//   ingest  (NEW; turn a folder of pdfs/docs into a wikifiable corpus).
// Everything else is a subskill nested under the first-class skill that uses
// it. "baseline" is the simplified conventional-RAG bundle builder (NOT the
// main path) and must be renamed + demoted so that is obvious.
//
// Research baked in (so the run is self-contained):
//  - Claude plugin = .claude-plugin/plugin.json + skills/ commands/ agents/
//    hooks/ .mcp.json + README; a marketplace is a git repo with
//    .claude-plugin/marketplace.json. Skill frontmatter "name" controls
//    invocation. (code.claude.com/docs/en/plugins-reference)
//  - Skills: progressive disclosure, SKILL.md < ~500 lines, split detail into
//    reference files. (platform.claude.com agent-skills/best-practices)
//  - README/badges via shields.io; OSS readiness needs LICENSE, CONTRIBUTING,
//    CI (GitHub Actions), codecov coverage badge.
//
// Note on quoting: prompts use single-quoted strings concatenated with the
// OUT/REPO constants, so inline tool names can be written plainly without
// breaking the string.
// ---------------------------------------------------------------------------

const OUT = 'tasks/public-readiness'   // all inventories/proposals land here
const REPO = 'C:/dev/scholarforge'

const FINDINGS = {
  type: 'object', additionalProperties: false,
  required: ['summary', 'items', 'output_file'],
  properties: {
    summary: { type: 'string' },
    items: { type: 'array', items: {
      type: 'object', additionalProperties: false,
      required: ['path', 'finding', 'severity', 'recommendation'],
      properties: {
        path: { type: 'string' },
        finding: { type: 'string' },
        severity: { enum: ['high', 'medium', 'low'] },
        recommendation: { type: 'string' },
      },
    } },
    output_file: { type: 'string', description: 'path under tasks/public-readiness it wrote' },
  },
}

const VERDICT = {
  type: 'object', additionalProperties: false,
  required: ['approved', 'blocking', 'notes'],
  properties: {
    approved: { type: 'boolean' },
    blocking: { type: 'array', items: { type: 'string' } },
    notes: { type: 'string' },
  },
}

const DONE = {
  type: 'object', additionalProperties: false,
  required: ['changed', 'summary'],
  properties: {
    changed: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
    follow_ups: { type: 'array', items: { type: 'string' } },
  },
}

// Adversarial codex review of a unit of work. Returns VERDICT.
function review(what, focus) {
  return agent(
    'Adversarially review ' + what + '. Be skeptical and specific. Focus: ' + focus + '. ' +
    'Read the actual files with shell tools (the repo is at ' + REPO + '). ' +
    'Fail the review (approved=false) if ANY of: instructions/behavior were lost or changed, ' +
    'cross-references broke, meta-commentary remains, personal info remains, a file was deleted ' +
    '(deletions are proposal-only), or a claim is unverified. List concrete blocking items.',
    { agentType: 'codex:codex-rescue', label: 'codex:' + what, phase: 'review', schema: VERDICT, effort: 'high' },
  )
}

// ===========================================================================
phase('Research')
log('Confirming current best practices (plugin packaging, skills, OSS badges).')

const research = await parallel([
  () => agent(
    'Research Claude Code PLUGIN packaging as of 2026: the exact .claude-plugin/plugin.json schema, ' +
    'marketplace.json, where skills/commands/agents live, and how an existing repo with a .claude/skills/ ' +
    'tree becomes an installable plugin with minimal disruption. Cite official docs. Return a concrete ' +
    'target layout for wikify and write it to ' + OUT + '/research-plugin.md.',
    { label: 'research:plugin', phase: 'Research', schema: FINDINGS },
  ),
  () => agent(
    'Research SKILL authoring best practices (Anthropic): progressive disclosure, SKILL.md size, ' +
    'frontmatter, splitting detail into reference files, prescriptive (not narrative) voice. ' +
    'Write a concise checklist to ' + OUT + '/research-skills.md.',
    { label: 'research:skills', phase: 'Research', schema: FINDINGS },
  ),
  () => agent(
    'Research top-tier OSS README + repo hygiene (2026): README sections, shields.io badges ' +
    '(license, CI, coverage/codecov, version, python), LICENSE choice for a research tool, ' +
    'CONTRIBUTING/SECURITY/CODE_OF_CONDUCT, issue/PR templates, GitHub Actions CI for a uv/pytest ' +
    'project. Write to ' + OUT + '/research-oss.md.',
    { label: 'research:oss', phase: 'Research', schema: FINDINGS },
  ),
])

// ===========================================================================
phase('Audit')
log('Read-only inventories. Nothing is changed in this phase.')

const AUDITS = [
  { key: 'skills', prompt:
    'Inventory every skill under .claude/skills/. For each: name, one-line purpose, who calls it ' +
    '(grep cross-references), and whether it is a first-class entry point or a subskill. Flag opaque ' +
    'names (especially "baseline" = simplified conventional-RAG bundle builder, and "wikify-investigate" = ' +
    'the true main entry point). Propose clearer names. Write ' + OUT + '/audit-skills.md.' },
  { key: 'deadcode', prompt:
    'Find dead/unused code: scripts, notebooks (*.ipynb), modules with no importers, orphaned helpers, ' +
    'superseded files. Use grep for import graphs and entry points (pyproject [project.scripts]). Do NOT ' +
    'delete anything. Produce a removal PROPOSAL with per-file justification + a confidence level. ' +
    'Write ' + OUT + '/inventory-deadcode.md.' },
  { key: 'meta', prompt:
    'Scan source docstrings, comments, skills, and docs for META-COMMENTARY: references to how the code ' +
    'was developed, "per tasks/foo.md", "in this phase", session/prompt acknowledgements, "recently merged", ' +
    'narrative about decisions. These must become prescriptive descriptions of what the code IS/DOES. ' +
    'List every hit with file:line. Write ' + OUT + '/audit-meta.md.' },
  { key: 'secrets', prompt:
    'Scan the ENTIRE repo AND git history for personal/sensitive info: real emails, names tied to the owner, ' +
    'passwords, API keys/tokens, absolute personal paths (C:/Users/...), private URLs. Use git log -p and grep. ' +
    'For reference/contact emails that must exist, propose a neutral placeholder (maintainers@example.com). ' +
    'Classify each hit (working-tree vs history-only). Write ' + OUT + '/audit-secrets.md. Do NOT rewrite history.' },
  { key: 'commits', prompt:
    'Audit ALL commit messages (git log) for: personal info, meta-commentary, references to other repos or ' +
    'private sessions, and the Claude-Session/Co-Authored-By trailers. Decide which should be sanitized for a ' +
    'public history. Write ' + OUT + '/audit-commits.md with a before/after table. Do NOT rewrite history yet.' },
  { key: 'tests', prompt:
    'Audit tests/ for: tests of dead code, tests asserting superseded behavior/patterns, duplicates, and ' +
    'low-value tests. Cross-check against the dead-code inventory. Recommend keep/refactor/remove per test. ' +
    'Write ' + OUT + '/audit-tests.md.' },
  { key: 'docs', prompt:
    'Audit docs/ + CLAUDE.md + AGENTS.md + README (if any) for staleness and gaps vs the real code. List what ' +
    'is missing for a newcomer: overview, the wikify (investigate) logic, ingestion/parsing, references mgmt, ' +
    'wiki HTML rendering/structure, metrics (M1-M6), corpus/wiki databases, vector search. Write ' + OUT + '/audit-docs.md.' },
]

const audits = await parallel(
  AUDITS.map(a => () => agent(a.prompt, { label: 'audit:' + a.key, phase: 'Audit', schema: FINDINGS })),
)
log('Audit complete: ' + audits.filter(Boolean).length + '/' + AUDITS.length + ' inventories written to ' + OUT + '/.')

// ===========================================================================
phase('Reorg design')
// CRITICAL loop: design then codex-vet before any file moves.
let reorgPlan = await agent(
  'Design the skill reorganization. Target: ONLY four first-class skills at the top level of the plugin skills/ ' +
  'tree — query, arxiv, wikify (rename of wikify-investigate), ingest (NEW). Every other skill is a subskill ' +
  'nested under the first-class skill that uses it (write-page, organize-wiki, refine, bundle, ' +
  'gather-evidence-cluster, extract-data, consolidate-data, investigate-explore, search-* and the shared ' +
  '"wikify" reference hub). Rename "baseline" to make clear it is the simplified conventional-RAG builder and NOT ' +
  'the main path. Resolve the name collision: the existing .claude/skills/wikify/ reference hub vs the new ' +
  'top-level wikify. Produce an EXACT move map (old path -> new path) using git mv only (lossless; no deletes), ' +
  'the list of every cross-reference that must be updated (skills reference each other by relative path; ' +
  'code/docs/tests may reference skill names), and the new ingest skill spec (wrap the existing docling/parser ' +
  'CLI; the docling guidance currently in CLAUDE.md moves here). Write ' + OUT + '/reorg-plan.md.',
  { label: 'design:reorg', phase: 'Reorg design', schema: DONE, effort: 'high' },
)
let reorgVerdict = await review(
  'the reorg plan in ' + OUT + '/reorg-plan.md',
  'name clarity, collision handling, completeness of the move map + cross-reference list, losslessness (git mv, zero deletes)',
)
if (!reorgVerdict || !reorgVerdict.approved) {
  log('Reorg plan rejected by codex; revising once.')
  reorgPlan = await agent(
    'Revise ' + OUT + '/reorg-plan.md to resolve every blocking item: ' +
    JSON.stringify((reorgVerdict && reorgVerdict.blocking) || []) + '. Keep it lossless (git mv only).',
    { label: 'design:reorg:revise', phase: 'Reorg design', schema: DONE, effort: 'high' },
  )
  reorgVerdict = await review('the revised reorg plan', 'all prior blocking items resolved')
}

// ===========================================================================
phase('Reorg execute')
const reorgExec = await agent(
  'Execute ' + OUT + '/reorg-plan.md EXACTLY. Use git mv for every move (lossless). Create the new ingest skill. ' +
  'Then update EVERY cross-reference the plan lists: relative paths inside SKILL.md files, skill-name mentions in ' +
  'code/docs/tests/CLAUDE.md. After moving, run the skill cross-reference integrity check (grep for now-broken ' +
  'relative links) and: uv run pytest tests/wikify/test_skill_allowed_tools.py -q. Report every moved path and ' +
  'every reference updated. Do NOT delete files.',
  { label: 'exec:reorg', phase: 'Reorg execute', schema: DONE, effort: 'high' },
)
const reorgExecVerdict = await review(
  'the executed reorg (git mv moves + reference updates)',
  'no broken relative links, no lost files, allowed-tools test green, names match the approved plan',
)

// ===========================================================================
phase('Skill rewrite')
// Each skill is rewritten then codex-reviewed for fidelity, pipelined.
const SKILLS_TO_REWRITE = [
  'query', 'arxiv', 'wikify', 'ingest', 'baseline-renamed',
  'write-page', 'organize-wiki', 'refine', 'bundle', 'gather-evidence-cluster',
  'extract-data', 'consolidate-data', 'investigate-explore', 'search-corpus',
  'search-wiki', 'wikify-reference-hub',
]
const rewritten = await pipeline(
  SKILLS_TO_REWRITE,
  (name) => agent(
    'Rewrite the SKILL.md (and reference files) for the "' + name + '" skill at its NEW post-reorg location. ' +
    'Make it strictly prescriptive: the instructions an agent needs to perform the task, nothing about how the ' +
    'skill was developed, no session/prompt acknowledgements, no "recently"/"in this phase"/plan references. ' +
    'Apply progressive disclosure: keep SKILL.md focused (aim < 500 lines), push deep detail into reference ' +
    'files. Preserve EVERY real instruction and contract — de-verbosing must not drop behavior. Match the repo ' +
    'style guide.',
    { label: 'rewrite:' + name, phase: 'Skill rewrite', schema: DONE },
  ),
  (done, name) => agent(
    'Adversarially diff the "' + name + '" skill before vs after rewrite (git diff). Confirm: zero lost ' +
    'instructions/contracts, no remaining meta-commentary, prescriptive voice, valid cross-references. ' +
    'Read the files at ' + REPO + '.',
    { agentType: 'codex:codex-rescue', label: 'codex:rewrite:' + name, phase: 'review', schema: VERDICT },
  ).then(v => ({ name, done, verdict: v })),
)
const rewriteFails = rewritten.filter(Boolean).filter(r => !r.verdict || !r.verdict.approved)
if (rewriteFails.length) log('Skill rewrites needing fixes: ' + rewriteFails.map(r => r.name).join(', '))

// ===========================================================================
phase('Docs')
// Hierarchical docs: overview first, then component deep-dives, then CLAUDE.md + README.
const overview = await agent(
  'Write docs/README.md (docs index) + docs/overview.md: what wikify IS, the core concepts and terminology ' +
  '(corpus, bundle, wiki, concept/dossier, evidence, maturity, data artifact), and the end-to-end process flow ' +
  'for the main "wikify" path (the investigate logic: SENSE, DECIDE, DISPATCH, CONSOLIDATE, REASSESS, CURATE, ' +
  'EMIT, STOP, seeding, coverage, dedup, person pages). Simple language, define every wikify term on first use, ' +
  'no meta-commentary, no unexplained jargon. Hierarchical: this is the top of the tree; component docs hang off ' +
  'it. Return the section outline the component docs must fill.',
  { label: 'docs:overview', phase: 'Docs', schema: DONE, effort: 'high' },
)
const COMPONENT_DOCS = [
  ['ingestion-and-parsing', 'folder-to-corpus ingestion, the Docling default parser plus marker/lite fallbacks, chunking, the corpus build'],
  ['references', 'citation extraction, bib resolution, CS1 rendering, source linking (DOI/PDF)'],
  ['wiki-rendering', 'the static HTML site: structure, navigation, article/person/data pages, figures, search, self-contained packaging'],
  ['metrics', 'eval metrics M1/M3/M5/M6 and maturity scoring — what each measures and how'],
  ['databases', 'the corpus SQLite store and the wiki.db committed layer: schema, handles, derived projections'],
  ['vector-search', 'embeddings, semantic/bm25/hybrid search, the corpus/wiki vector indexes'],
]
const componentDocs = await parallel(
  COMPONENT_DOCS.map(pair => () => agent(
    'Write docs/' + pair[0] + '.md covering: ' + pair[1] + '. Ground every claim in the actual code ' +
    '(read src/wikify). Simple, accessible language; introduce wikify terms; no meta-commentary. Fit under the ' +
    'overview outline.',
    { label: 'docs:' + pair[0], phase: 'Docs', schema: DONE },
  )),
)
const claudeMd = await agent(
  'Rewrite CLAUDE.md for the final public state: a tight description of what wikify is + the key references ' +
  '(docs/, the four first-class skills). KEEP the standing engineering rules (style guide, conciseness, uv ' +
  'tooling, surgical-changes, simplicity-first) but drop session/history framing and the Current Focus/lessons ' +
  'scaffolding that assumed active development. MOVE the Docling/parser-backend guidance out to the ingest skill. ' +
  'Keep it surgical and minimal; adhere to current CLAUDE.md conventions. Also evaluate removing AGENTS.md (this ' +
  'is a Claude-first repo) and, if redundant, git rm it ONLY after confirming nothing references it.',
  { label: 'docs:claudemd', phase: 'Docs', schema: DONE, effort: 'high' },
)
const readme = await agent(
  'Write a top-tier README.md: one-line value prop, a short hero (what wikify produces — a navigable wiki from a ' +
  'corpus), shields.io badges (CI, coverage/codecov, license, python version, ruff), quickstart (uv), the four ' +
  'first-class skills, a short worked example (ingest -> wikify -> render), a link to the rendered demo if present, ' +
  'the docs index, and contributing/license pointers. No marketing fluff, no meta-commentary.',
  { label: 'docs:readme', phase: 'Docs', schema: DONE, effort: 'high' },
)
await review(
  'the rewritten docs, CLAUDE.md, and README',
  'accuracy vs code, accessible language, every term introduced, no meta-commentary, hierarchical structure, surgical CLAUDE.md',
)

// ===========================================================================
phase('Sanitize')
// Safe edits execute; git-history rewrite is proposal-only.
const sanitize = await agent(
  'Using ' + OUT + '/audit-meta.md and ' + OUT + '/audit-secrets.md, EXECUTE the safe sanitizations in the ' +
  'working tree: remove remaining meta-commentary from code/docstrings/skills/docs; replace any real personal ' +
  'email with a neutral placeholder (maintainers@example.com) where a contact is genuinely needed, else remove; ' +
  'strip absolute personal paths. Do NOT touch git history here. Report every edit.',
  { label: 'sanitize:worktree', phase: 'Sanitize', schema: DONE, effort: 'high' },
)
const historyProposal = await agent(
  'Using ' + OUT + '/audit-commits.md and ' + OUT + '/audit-secrets.md (history-only hits), write ' + OUT +
  '/history-rewrite-proposal.md: a reviewable git filter-repo (or git filter-branch) plan + a commit-message ' +
  'mapping that removes personal info, meta-commentary, other-repo references, and decides on the ' +
  'Claude-Session/Co-Authored-By trailers. Include the exact commands but DO NOT run them — history rewrite is ' +
  'destructive and owner-approved only.',
  { label: 'sanitize:history-proposal', phase: 'Sanitize', schema: DONE },
)
await review(
  'the worktree sanitization + history-rewrite proposal',
  'no personal info left in the working tree, no over-deletion, history proposal complete and not executed',
)

// ===========================================================================
phase('Dead code')
// Proposal only — never delete (owner reviews).
const deadcodeProposal = await agent(
  'Finalize ' + OUT + '/inventory-deadcode.md into a ready-to-apply removal proposal: grouped by confidence, each ' +
  'with the exact git rm command and a one-line justification, plus the test-audit cross-check from ' + OUT +
  '/audit-tests.md (which tests would go with which dead code). DO NOT delete anything. State clearly this needs ' +
  'owner sign-off.',
  { label: 'deadcode:proposal', phase: 'Dead code', schema: DONE },
)

// ===========================================================================
phase('Packaging')
const packaging = await agent(
  'Make the repo an installable Claude plugin and an OSS-ready project, per ' + OUT + '/research-plugin.md and ' +
  OUT + '/research-oss.md. Add: .claude-plugin/plugin.json (name, description, version, the skills tree) and a ' +
  '.claude-plugin/marketplace.json; a LICENSE (recommend one in a header comment if unsure); CONTRIBUTING.md, ' +
  'SECURITY.md, CODE_OF_CONDUCT.md; .github/ISSUE_TEMPLATE + a PR template; a GitHub Actions CI workflow ' +
  '(.github/workflows/ci.yml) running ruff + uv run pytest with coverage uploaded to codecov; a pyproject ' +
  'coverage config. Audit .gitignore so data/, secrets, and caches are excluded. Wire the README badges to the ' +
  'new CI/coverage. Report every file added.',
  { label: 'pkg:plugin-ci', phase: 'Packaging', schema: DONE, effort: 'high' },
)
await review(
  'the plugin manifest + CI + OSS meta-files',
  'plugin.json validity, CI actually runs ruff+pytest+coverage, badges resolve, .gitignore excludes data/secrets, no placeholder left unfilled',
)

// ===========================================================================
phase('Final review')
const finalReview = await agent(
  'Adversarial FULL review for public release. Verify against the goals: (1) skill rename/reorg + ingest skill + ' +
  'only 4 first-class skills; (2) prescriptive de-meta skills; (3) dead-code proposal; (4) hierarchical docs + ' +
  'CLAUDE.md + AGENTS.md decision + README; (5) no meta-commentary anywhere; (6) no personal info in the working ' +
  'tree; (7) commit-history sanitization proposal; (8) badges; (9) test cleanup; (10) plugin packaging; ' +
  '(11) gaps. Run: uv run ruff check, and uv run pytest tests/wikify -q. List anything unmet or regressed.',
  { agentType: 'codex:codex-rescue', label: 'codex:final', phase: 'Final review', schema: VERDICT, effort: 'high' },
)
const completeness = await agent(
  'Completeness critic. Independently of the goal list, name what a discerning open-source maintainer would still ' +
  'find missing or weak for a public wikify release (versioning/CHANGELOG, examples/demo corpus, screenshots/GIF, ' +
  'type checking, dependency pinning/security, reproducibility, accessibility of the rendered site, onboarding). ' +
  'Write the final ' + OUT + '/REPORT.md summarizing every phase output, the codex verdicts, and the outstanding ' +
  'owner-approval items (dead-code removal, git-history rewrite, LICENSE choice, AGENTS.md removal).',
  { label: 'final:completeness', phase: 'Final review', schema: DONE, effort: 'high' },
)

return {
  research: research.filter(Boolean).length,
  audits: audits.filter(Boolean).length,
  reorg_approved: reorgVerdict ? reorgVerdict.approved : null,
  skills_rewritten: rewritten.filter(Boolean).length,
  skill_rewrite_fails: rewriteFails.map(r => r.name),
  final_approved: finalReview ? finalReview.approved : null,
  report: OUT + '/REPORT.md',
  owner_approval_needed: [
    OUT + '/inventory-deadcode.md (deletions)',
    OUT + '/history-rewrite-proposal.md (git history)',
    'LICENSE choice', 'AGENTS.md removal',
  ],
}
