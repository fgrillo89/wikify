# Contributing to Wikify

Thanks for your interest in improving Wikify. This guide covers the dev
setup, the checks a change must pass, and the conventions we follow.

## Development setup

Wikify is a [uv](https://docs.astral.sh/uv/) project targeting Python
`>=3.12`.

```bash
git clone https://github.com/fgrillo89/wikify.git
cd wikify
uv sync                       # install deps (and dev deps)
uv run wikify --help          # smoke test the CLI
```

First-run note: the default Docling parser downloads the Granite formula
model (~258 MB) plus layout/table models on the first parse. Pass
`--parser lite` for a model-free path during development.

## Checks before you open a PR

Run both locally; CI runs the same on Ubuntu and Windows across Python
3.12 and 3.13.

```bash
uv run ruff check src/wikify tests/wikify          # lint
uv run pytest tests/wikify -q                       # tests
uv run pytest tests/wikify -q --cov=wikify          # tests + coverage
```

Add or update tests for any behavior change. A PR that changes behavior
without a test is incomplete.

## Branch and commit conventions

- Branch off `master`; do not push directly to it.
- Use Conventional-Commits-style prefixes: `feat(...)`, `fix(...)`,
  `refactor(...)`, `chore(...)`, `docs(...)`, `test(...)`.
- Keep commit messages free of absolute or personal machine paths.
- Make surgical changes: every changed line should trace to the stated
  goal. Do not reformat or "improve" adjacent code in the same commit.
- Enumerate the blast radius of a non-trivial change (callers, tests,
  docs, skills) and amend them in the same commit.

## Pull requests

Open a PR against `master` and fill in the pull request template. A PR is
ready to merge when:

- tests pass and new behavior is covered,
- `ruff` is clean,
- callers/consumers are enumerated and updated,
- no personal paths or secrets are introduced,
- docs and skills are updated when the change touches them.

## Code of conduct

Participation in this project is governed by the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Security

Do not file public issues for vulnerabilities. See
[SECURITY.md](SECURITY.md) for private reporting.
