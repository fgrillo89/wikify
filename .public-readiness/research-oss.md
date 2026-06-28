# OSS README + Repo Hygiene Research (2026)

Scope: best practices for taking a Python research tool (`wikify`, a uv/pytest
project, Python >=3.12, CLI via Typer, MCP server) to top-tier open-source
quality. Tailored to the current repo state: empty `README.md`, no `LICENSE`,
no `.github/` templates, no CI.

---

## 1. README structure (top-tier OSS, 2026)

Order matters: a reader decides "is this for me?" in the first screen. Lead with
identity and a copy-pasteable quickstart; defer deep docs to `docs/`.

Recommended section order:

1. **Project name + one-line tagline** — what it is in <=15 words.
2. **Badge row** — license, CI, coverage, PyPI/version, Python versions (see §2).
3. **Hero / value paragraph** — 2-4 sentences: the problem, who it's for, why it's
   different. For a research tool, name the research question it answers.
4. **Demo** — a GIF/screenshot, an `asciinema` cast, or a short transcript. A
   visual or a 6-line example beats paragraphs. For a CLI, show real `$ wikify ...`
   invocations and their output.
5. **Install** — the canonical path first. For uv:
   ```bash
   uv tool install wikify        # or: uvx wikify ...
   # from source:
   git clone … && cd wikify && uv sync
   ```
   Note Python version requirement (`>=3.12`) and any heavy first-run downloads
   (e.g. the Docling Granite model ~258 MB) so users aren't surprised.
6. **Quickstart / Usage** — the shortest end-to-end path that produces value
   (ingest -> build -> render). One fenced block, runnable verbatim.
7. **How it works / Concepts** — corpus -> wiki graph -> render; the
   scripted-vs-guided strategy distinction; link to `docs/`.
8. **Configuration** — env vars (API keys via `litellm`), config file, model
   selection. State which secrets are needed and that none are committed.
9. **Project status / maturity** — explicit "alpha / research preview" banner.
   Honesty here is a hygiene signal; sets expectations on stability and support.
10. **Contributing** — one line + link to `CONTRIBUTING.md`.
11. **License** — one line + link to `LICENSE`.
12. **Citation** — `CITATION.cff` callout (see §8); research tools get cited.
13. **Acknowledgements / Related work** — optional.

Principles:
- **Above the fold = title, badges, value prop, install, one example.** Everything
  else can scroll.
- **Show, don't tell.** Runnable blocks over prose.
- **Link out, don't inline.** Long architecture/API content lives in `docs/`,
  keeping the README skimmable (target < ~400 lines).
- **No dead links, no TODO placeholders.** An empty/placeholder README reads as
  abandoned. Current `README.md` is 0 bytes — top priority to fill.
- **ASCII-friendly.** Per repo convention, avoid non-ASCII in console-facing text;
  emojis in README headings are tolerated by convention but the repo style guide
  bans emojis — keep them out for consistency.

---

## 2. shields.io badges

Place in a single row directly under the H1. Use [shields.io](https://shields.io)
for consistent styling. Recommended set for this project, in priority order:

| Badge | Markdown source | Notes |
|-------|-----------------|-------|
| License | `![License](https://img.shields.io/github/license/<owner>/wikify)` | Auto-reads `LICENSE`; needs the file to exist. |
| CI status | `![CI](https://github.com/<owner>/wikify/actions/workflows/ci.yml/badge.svg)` | GitHub-native badge from the workflow; preferred over shields for CI. |
| Coverage | `![codecov](https://codecov.io/gh/<owner>/wikify/branch/main/graph/badge.svg)` | Requires Codecov upload step (see §7). Coveralls is the alternative. |
| Python versions | `![Python](https://img.shields.io/pypi/pyversions/wikify)` | From PyPI metadata once published; before publish, hardcode `3.12+`. |
| PyPI version | `![PyPI](https://img.shields.io/pypi/v/wikify)` | Only once published. Omit until then — a broken badge looks worse than none. |
| Code style | `![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)` | Signals the `ruff` toolchain this repo already uses. |

Guidance:
- **Only show badges that are green and real.** A perpetually-red or 404 badge
  erodes trust. Before PyPI publish, drop the version/pyversions-from-PyPI badges
  and use a static `python-3.12+` shield instead.
- Prefer the **GitHub Actions native badge** for CI (updates without a 3rd party).
- Keep the row to **4-6 badges**; more is noise.
- Pin badge `branch` to your default branch (this repo's default is `master`, not
  `main` — set badge URLs accordingly).

---

## 3. LICENSE choice for a research tool

Decision axes: permissive vs copyleft, patent grant, academic norms, dependency
compatibility.

- **MIT** — shortest, most permissive, maximal adoption. Best default if the goal
  is "let anyone use/embed it, including commercially." No explicit patent grant.
- **Apache-2.0** — permissive **plus an explicit patent grant and patent-retaliation
  clause**, plus a `NOTICE` mechanism. Preferred when there's any patentable method
  (novel algorithms — plausible for a research/strategy tool) or when you want
  corporate users to adopt without legal hesitancy. Slightly more ceremony.
- **BSD-3-Clause** — like MIT plus a no-endorsement clause. Common in academia.
- **Copyleft (GPL-3.0 / AGPL-3.0)** — forces downstream open-sourcing. AGPL closes
  the "SaaS loophole." Choose only if you specifically want to prevent closed
  derivatives; it deters many corporate and academic-industrial collaborators.

**Recommendation for `wikify`:** **Apache-2.0**. A research strategy tool with
novel methods benefits from the explicit patent grant, and it remains permissive
enough for broad academic + industry adoption. MIT is the acceptable simpler
fallback if you value brevity over patent protection. Avoid copyleft unless
reciprocity is an explicit goal.

Dependency note: verify license compatibility of the dependency tree. Permissive
deps (MIT/BSD/Apache) impose no constraint; a GPL dependency would force GPL on
distribution. Run a scan (`pip-licenses` / `uv pip licenses` equivalent, or
`pipdeptree` + manual check) before finalizing.

Mechanics:
- Add a top-level `LICENSE` file (GitHub auto-detects and drives the license badge
  + repo sidebar). Use the canonical SPDX text verbatim; do not edit the body.
- Set `license = "Apache-2.0"` (SPDX expression) in `pyproject.toml` `[project]`
  and add the `License :: OSI Approved :: Apache Software License` classifier (or
  rely on the SPDX field per PEP 639).
- For Apache-2.0, optionally add a short per-file header and a `NOTICE` file.

---

## 4. Community health files

GitHub recognizes these by name and surfaces them in the contribute/community UI.
They can live at repo root or in `.github/` (root is most visible). A repo-level
`.github` org repo can provide org-wide defaults, but per-repo files are clearer.

### CONTRIBUTING.md
Covers: how to set up the dev env (`uv sync`, `uv run pytest`), how to run lint
(`uv run ruff check src/wikify tests/wikify`), branch/PR conventions, commit
message style (this repo uses Conventional-Commits-style prefixes:
`chore(...)`, `refactor(...)`, `feat(...)`), the trailer requirement
(`Co-Authored-By:` per CLAUDE.md), and the testing/coverage bar for a PR to merge.
Link the `CODE_OF_CONDUCT.md`. Keep it actionable, not aspirational.

### SECURITY.md
Covers: supported versions table, **private** vulnerability reporting channel.
Prefer **GitHub Private Vulnerability Reporting** (Security tab -> enable) over a
plaintext email, or list a dedicated security contact. State a response-time SLA
(e.g. "acknowledge within 72h"). For an LLM/agent tool, explicitly mention scope:
prompt-injection, secret handling (API keys must never be committed), and
supply-chain (pinned deps) — consistent with the repo's supply-chain memory note.

### CODE_OF_CONDUCT.md
Standard: adopt the **Contributor Covenant v2.1** verbatim, fill in the enforcement
contact email. Low effort, high signal; expected by most contributors and by
GitHub's community-standards checklist.

### Other recognized health files
- `SUPPORT.md` — where to ask questions (Discussions, issues).
- `GOVERNANCE.md` — optional; only if multi-maintainer.
- `FUNDING.yml` (in `.github/`) — sponsor links; optional.
- `CITATION.cff` — see §8.

---

## 5. Issue & PR templates

Live in `.github/`. Modern GitHub supports **YAML issue forms** (structured fields
with validation) which are strongly preferred over legacy markdown templates
because they enforce useful input.

Layout:
```
.github/
  ISSUE_TEMPLATE/
    config.yml          # blank_issues_enabled: false; contact_links (Discussions, security)
    bug_report.yml      # form: version, repro steps, expected/actual, env (OS, Python, GPU)
    feature_request.yml # form: problem, proposed solution, alternatives
  PULL_REQUEST_TEMPLATE.md
```

- **`config.yml`**: set `blank_issues_enabled: false` and add `contact_links` that
  route security reports to the SECURITY policy and questions to Discussions.
- **`bug_report.yml`**: for this tool, capture `wikify --version`, Python version,
  OS (Windows is a primary target per env), parser backend
  (docling/marker/lite), GPU/driver (the repo has GPU-stability lessons), and the
  exact command + minimal corpus to reproduce. Include a "no secrets/API keys in
  logs" reminder.
- **`PULL_REQUEST_TEMPLATE.md`**: checklist mirroring the CLAUDE.md merge bar —
  tests added/updated, `ruff` clean, callers/consumers enumerated (blast-radius),
  no personal paths in commits, docs updated, lessons captured if a correction
  occurred. Add a "linked issue" line.

---

## 6. GitHub Actions CI for a uv/pytest project

Use Astral's first-party **`astral-sh/setup-uv`** action; it installs uv, enables
caching, and can install the pinned Python from `.python-version`. Canonical
2026 pattern:

```yaml
# .github/workflows/ci.yml
name: CI
on:
  push:
    branches: [master]
  pull_request:
permissions:
  contents: read
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true
jobs:
  test:
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest]   # Windows matters: primary user platform
        python-version: ["3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
          cache-dependency-glob: "uv.lock"
      - name: Set up Python
        run: uv python install ${{ matrix.python-version }}
      - name: Sync deps
        run: uv sync --all-extras --dev --locked   # --locked fails if uv.lock is stale
      - name: Lint
        run: uv run ruff check src/wikify tests/wikify
      - name: Test
        run: uv run pytest tests/wikify -q --cov=wikify --cov-report=xml
      - name: Upload coverage
        uses: codecov/codecov-action@v5
        with:
          files: coverage.xml
          token: ${{ secrets.CODECOV_TOKEN }}   # required for private; recommended for public
```

Notes and best practices:
- **Pin actions** to a major version tag (or full SHA for max supply-chain safety,
  consistent with the repo's pinning posture).
- **`uv sync --locked`** (or `--frozen`) makes CI fail when `uv.lock` drifts from
  `pyproject.toml` — catches un-committed lockfile updates.
- **`enable-cache: true`** + `cache-dependency-glob: uv.lock` gives fast, correct
  caching keyed on the lockfile.
- **Least-privilege `permissions:`** at the top (`contents: read`); grant more only
  per-job where needed (e.g. a release job).
- **`concurrency` with `cancel-in-progress`** avoids redundant runs on rapid pushes.
- **Matrix Windows + Ubuntu**: this tool is developed on Windows 11 and has
  Windows-specific concerns (Unicode console, paths) — CI must cover it.
- Consider GPU/model-download cost: keep CI on the **`lite` parser path**
  (pymupdf4llm/python-docx/python-pptx/trafilatura, no model downloads) so runs are
  fast and don't pull the 258 MB Docling model. Gate heavy/model/network tests
  behind a marker (`-m "not slow"`) or a separate, manually-triggered workflow.
- **Separate workflows** for concerns: `ci.yml` (lint+test), optional
  `publish.yml` (build + `uv build` + PyPI Trusted Publishing via OIDC, no stored
  token), optional `codeql.yml` for security scanning. Add Dependabot
  (`.github/dependabot.yml`) for `uv`/`pip` and `github-actions` ecosystems.

---

## 7. Coverage / Codecov

- Generate `coverage.xml` in the test step (`--cov=wikify --cov-report=xml`),
  requires `pytest-cov` as a dev dependency (`uv add --dev pytest-cov`).
- Upload via **`codecov/codecov-action@v5`**. For public repos a `CODECOV_TOKEN`
  is now recommended (rate-limit / tokenless deprecation). Store it as a repo
  secret.
- Coverage badge: `https://codecov.io/gh/<owner>/wikify/branch/master/graph/badge.svg`.
- Alternative if avoiding third parties: keep coverage internal and skip the badge,
  or use Coveralls. Don't show a coverage badge you can't keep current.
- Set a realistic target (e.g. `codecov.yml` `coverage.status.project.target: auto`
  with a small threshold) rather than a hard 90% gate that blocks honest PRs.

---

## 8. Research-specific extras

- **`CITATION.cff`** (Citation File Format, YAML) at repo root. GitHub renders a
  "Cite this repository" button and exports BibTeX/APA. Essential for an academic
  tool so users cite it correctly. Include authors, title, version, DOI if minted.
- **Zenodo DOI**: connect the repo to Zenodo to mint a DOI per release — turns a
  GitHub release into a citable artifact. Add the DOI badge once minted.
- **`docs/`** site: this repo already has `docs/`. Consider MkDocs-Material or
  Sphinx for hosted docs; link from README. Keep README as the entry point, docs
  for depth.
- **Reproducibility**: pin `uv.lock`, document model versions and dataset/corpus
  provenance, state determinism caveats (LLM nondeterminism, temperature, seeds).

---

## 9. Repo-state gaps to fix (this repo, against the above)

High priority:
- **`README.md` is empty (0 bytes)** — fill with the §1 structure. Highest-signal
  single fix.
- **No `LICENSE`** — add Apache-2.0 (recommended) or MIT; wire `pyproject.toml`
  license field + classifier and the license badge.
- **No `.github/`** — add `ci.yml`, issue forms, PR template, `dependabot.yml`.
- **No community health files** — add `CONTRIBUTING.md`, `SECURITY.md`,
  `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1).

Medium:
- **No CI** — add the uv/pytest workflow (§6); badge it.
- **No coverage** — add `pytest-cov` + Codecov (§7).
- **Default branch is `master`** — ensure all badge/workflow URLs use `master`
  (or rename to `main` and update; pick one and be consistent).
- **`pyproject.toml` carries a personal email** (`fabio.grillo89@gmail.com`) — fine
  for authorship, but confirm this is intended as public before release; consider a
  project/role alias for the security contact.

Lower:
- **`CITATION.cff` + Zenodo DOI** for academic citability.
- **Working-tree clutter** (`.tmp/`, `tmp_writes/`, `scratch/`, `build/`,
  `.pytest-tmp-*`, `test-tmp-local3/`) should be `.gitignore`d / removed before
  public push so the repo reads clean. (Inventory only — deletes are proposals.)
- **Two agent-instruction files** (`AGENTS.md`, `CLAUDE.md`) — fine, but ensure
  neither leaks personal paths/secrets before going public.

---

## 10. One-line checklist

`README` (filled) · `LICENSE` (Apache-2.0) · `CONTRIBUTING.md` · `SECURITY.md` ·
`CODE_OF_CONDUCT.md` (Covenant 2.1) · `.github/ISSUE_TEMPLATE/*.yml` + `config.yml` ·
`PULL_REQUEST_TEMPLATE.md` · `ci.yml` (setup-uv, matrix, `--locked`, ruff+pytest) ·
coverage -> Codecov · `dependabot.yml` · `CITATION.cff` · badge row (license, CI,
coverage, python, ruff) · clean working tree.
