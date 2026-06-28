# Audit: Personal / Sensitive Information

Scope: entire working tree + full git history (838 commits, all refs).
Method: `git grep`, `git log -p --all -S`, regex sweeps for emails, absolute
personal paths, API keys/tokens (`sk-`, `ghp_`, `AKIA`, `xox*`, PEM blocks),
passwords, private URLs/IPs. **No history was rewritten.** History-only hits
are listed for awareness only; the safe fixes all target the working tree.

## Summary

- **No credentials of any kind found** — no API keys, tokens, passwords,
  private keys, or `.env`/credential files in the working tree OR anywhere in
  history.
- **No private URLs or LAN IPs** (localhost/127.0.0.1/192.168/10.x) committed.
- `/data/` is gitignored and no `data/` files are tracked — corpora/caches
  cannot leak.
- The only personal data is the owner's name + email (expected for a maintainer)
  and a few real third-party researcher emails embedded in test fixtures.

## Working-tree hits (fix before public release)

### S1 — Owner personal email in `pyproject.toml` (LOW)
`pyproject.toml:7` → `{ name = "Fabio Grillo", email = "fabio.grillo89@gmail.com" }`
A maintainer name/email in package metadata is conventional and acceptable for
public OSS. If a role address is preferred, replace with a neutral placeholder
`maintainers@example.com` (or a project alias). Owner's call — not a leak.

### S2 — Real third-party researcher emails in test fixtures (LOW–MEDIUM)
These are real-looking PII of *other* people, hardcoded as input strings to
tests that assert the ingest pipeline strips emails:
- `tests/wikify/test_chunker.py:97` → `benjamin.spetzler@tu-ilmenau.de`
- `tests/wikify/test_ingest_quality.py:150` → `24DR0241@iitism.ac.in`
- `tests/wikify/test_ingest_quality.py:158` → `sanjaysihag91@gmail.com`
- `tests/wikify/test_ingest_quality.py:159` → `rahulk129@gmail.com`
Recommendation: replace with synthetic addresses (e.g. `name@example.com`,
which `tests/wikify/test_toc_spans.py:223` already does) so no real individual's
contact info ships in the repo. The tests' assertions are unaffected.

### S3 — OpenAlex/Crossref polite-pool contact placeholder (LOW)
- `src/wikify/ingest/dag.py:201` → `OPENALEX_EMAIL` default `wikify@example.com`
- `src/wikify/util/doi_resolver.py:228` → `User-Agent: ...(mailto:wikify@example.com)`
Already non-personal placeholders. For a real public release the API "polite
pool" expects a reachable contact; either document the `OPENALEX_EMAIL` env var
in the README or set it to `maintainers@example.com`. Not a leak.

Test addresses in `tests/wikify/citestore/test_resolver.py` (`test@example.com`)
are synthetic — no action.

## History-only hits (informational — do NOT rewrite history)

### H1 — Owner git identity on all commits (accept)
All 838 commits are authored by `Fabio Grillo <fabio.grillo89@gmail.com>`
(plus the GitHub noreply alias `61154687+fgrillo89@users.noreply.github.com`).
This is the normal, expected authorship trail for a public repo. Removing it
would require a full history rewrite and is not recommended.

### H2 — Personal absolute path `C:\Users\fgril\OneDrive\Documents\scholarforge` (accept)
Present in early history of `docs/architecture.md` and `docs/project-status.md`;
removed by commit `909e405` ("Move project from OneDrive to C:\dev\scholarforge").
**Working tree is clean** of this path. The replacement `C:\dev\scholarforge` is
still a local path but is not personal/identifying. History-only; leave as-is
unless a full scrub is later requested.

### H3 — Owner email referenced in historical docs (accept)
`fabio.grillo89@gmail.com` appears only via the author trail and old
`docs/project-status.md` revisions; no secrets attached. History-only.

## Clean (verified absent)
- API keys / bearer tokens / OAuth secrets: none (working tree + history).
- AWS keys (`AKIA…`), GitHub tokens (`ghp_…`), Slack (`xox…`), `sk-…`: none.
- Private keys / PEM blocks: none.
- `.env`, `*.pem`, `*.key`, `id_rsa`, credential files: none tracked.
- Private/internal URLs or LAN IPs: none.

## Recommended safe actions (no history rewrite)
1. S2: swap the four real third-party emails in test fixtures for
   `name@example.com`-style synthetics.
2. S1/S3 (optional): standardize maintainer/contact email to a neutral alias
   (`maintainers@example.com`) if a personal address is undesirable.
3. Leave git history intact (H1–H3); document the decision so a future
   maintainer doesn't "fix" authorship by force-rewriting shared history.
