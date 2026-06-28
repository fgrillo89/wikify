# Commit-Message Audit for Public History

Scope: every commit reachable from `HEAD` (838 commits, oldest `c2ba926 "first commit"`).
Goal: identify personal info, meta-commentary, references to other repos / private
sessions, and AI co-author / session trailers that should be sanitized before the
history is published. **No history has been rewritten** — this is a proposal only.

## Method

```
git log --format='%H %an <%ae>%n%B'   # full bodies + trailers
git log --format='%an <%ae>' | sort | uniq -c
git log --format='%b' | grep -E 'Claude-Session|Co-Authored-By|@'
```

## Summary of findings

| # | Category | Where | Count | Severity | Decision |
|---|----------|-------|-------|----------|----------|
| 1 | Private session URLs (`Claude-Session:` trailer) | commit bodies | 8 commits (2 distinct URLs) | high | **Strip** |
| 2 | Personal email as author/committer (`fabio.grillo89@gmail.com`) | author + committer ident | 723 author / 715 committer | medium | **Decide: keep or map to GitHub noreply** |
| 3 | AI co-author trailer (`Co-Authored-By: Claude ...`) | commit bodies | 722 (10 spelling/version variants) | low | **Keep (honest attribution); optionally normalize** |
| 4 | Redundant self co-author (`Co-Authored-By: fgrillo89 <...noreply.github>`) | commit bodies | 11 | low | **Strip (duplicate of author)** |
| 5 | Third-party AI co-author (`Co-Authored-By: Codex <codex@openai.com>`) | 1 commit | 1 | low | **Keep or strip (cosmetic)** |
| 6 | Dev-process meta-commentary (adversarial-review rounds, PR #, `tasks/*.md` refs, "(not run)") | bodies | ~80 | low | **Keep — legitimate engineering history** |
| 7 | Superseded project names (`scholarforge`, `wikify_simple`) in subjects | subjects/bodies | ~162 | low | **Keep — real rename history** |

No API keys, secrets, passwords, profanity, or personal filesystem paths
(`C:\Users\...`, `/home/...`) were found in any commit message. The only emails
present are `fabio.grillo89@gmail.com`, the GitHub noreply
`61154687+fgrillo89@users.noreply.github.com`, `noreply@anthropic.com`, and
`codex@openai.com`. URL hits other than the session trailers are all in-prose
technical references (`https://doi.org/`, `http://` vs file-served rendering, a
URL-noise regex) — not sanitization concerns.

---

## 1. Private session URLs — STRIP (high)

8 commits carry a `Claude-Session:` trailer pointing at a private `claude.ai/code`
session. These links are inaccessible to the public, leak the existence/timing of
private working sessions, and add noise. They are the clearest must-remove item.

Affected commits (2 distinct URLs):

```
55ce9b7  chore(workflows): tasks/ removal, known-findings triage, restartable resume
c3e2778  chore(workflows): add wikify-public-readiness workflow (not run)
53dfa68  refactor(wikify): addressable coverage, completeness stop, data refs, dedup + person primitives
50fb9ef  refactor(investigate): address adversarial review — leaner budget, explicit F17 signal
3d3ea23  perf(investigate): Phase-2 structural efficiency — run sense, budget reconcile, lean judging
1c9e931  perf(investigate): Phase-1 token-efficiency fixes (F19, F2, F6, F14, F17)
77972c0  docs(friction): record adversarial review verdict (CLEAN)
e3beb6c  fix(render): clean data-artifact citations, preserve per-cell footnotes, fix figure citations
```

Before:

```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Hrp75AUGb3SP1p6A3hXrKg
```

After:

```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

(Delete the `Claude-Session:` trailer line only; keep the Co-Authored-By per item 3.)

---

## 2. Personal email in author/committer identity — DECIDE (medium)

| Identity | Author commits | Committer commits |
|----------|---------------:|------------------:|
| `Fabio Grillo <fabio.grillo89@gmail.com>` | 723 | 715 |
| `Fabio Grillo <61154687+fgrillo89@users.noreply.github.com>` | 115 | 8 |
| `GitHub <noreply@github.com>` (merge commits) | 0 | 115 |

The bulk of the history is authored with a personal Gmail address; the GitHub
merge commits already use the privacy-preserving `users.noreply.github.com`
address. Publishing exposes the Gmail on every commit.

Two acceptable outcomes — owner's call:

- **Keep** — the address is the owner's already-public commit identity; no action.
- **Normalize** — map both Gmail author and committer to
  `61154687+fgrillo89@users.noreply.github.com` (already in use on the merges) so
  the public history carries no personal mailbox.

Before / After (option: normalize):

```
Before:  Fabio Grillo <fabio.grillo89@gmail.com>
After:   Fabio Grillo <61154687+fgrillo89@users.noreply.github.com>
```

This is a full-history rewrite of the identity fields and should be batched with
item 1 in a single `git filter-repo --mailmap` pass if chosen.

---

## 3. AI co-author trailer — KEEP, optionally normalize (low)

722 commits carry a `Co-Authored-By: Claude ...` trailer. This is the standard,
honest GitHub convention for AI-assisted work and is fine to publish. The only
issue is cosmetic inconsistency — 10 variants differing by casing, model version,
and the "(1M context)" suffix:

```
339  Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
205  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
 55  Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
 47  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
 35  Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
 25  Co-authored-by: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  9  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  4  Co-authored-by: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
  2  Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
  1  Co-authored-by: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

Recommendation: **Keep** the trailers. Do **not** rewrite 722 commits purely for
casing/version cosmetics — the churn and rewritten hashes are not worth it. If a
normalization pass is already happening for items 1/2, optionally collapse all to a
single canonical line (`Co-Authored-By: Claude <noreply@anthropic.com>`), but this
is purely cosmetic and may be skipped.

---

## 4. Redundant self co-author — STRIP (low)

11 commits list the author a second time as a co-author via the GitHub noreply
identity. This is noise (the person is already the author).

Before:

```
...body...
Co-Authored-By: fgrillo89 <fgrillo89@users.noreply.github.com>
```

After:

```
...body...
```

If a history rewrite runs for items 1/2, drop these lines in the same pass. Not
worth a dedicated rewrite on its own.

---

## 5. Third-party AI co-author — KEEP or STRIP (low)

One commit, `968675d docs: Codex review revision of skill-centric pivot plan`,
carries `Co-Authored-By: Codex <codex@openai.com>`. It is honest attribution of a
second tool and exposes no private info. Keep it, or drop it for single-tool
consistency — cosmetic, owner's call.

---

## 6. Dev-process meta-commentary — KEEP (low)

~80 commit bodies reference the internal engineering loop: "adversarial review
round N", "Codex adversarial review", "agent review", PR numbers (`PR #96`),
measured-savings proxies, "(not run)", and internal plan files
(`tasks/investigate-measured-savings.md`, `tasks/investigate-efficiency-ledger.md`).

These describe *what the code does and why it changed* and are exactly the kind of
durable history that belongs in commit messages (per the repo's own "historical
framing belongs in commit messages" rule). They are not private and not
embarrassing. **Keep.**

Note: the `tasks/` folder is being removed from the working tree, so body
references to `tasks/*.md` will become dangling pointers into history. That is
expected and harmless for past commits — do not rewrite messages to chase it.

---

## 7. Superseded project names — KEEP (low)

~162 commits mention `wikify_simple` or `scholarforge` (e.g. `c21455c "Promote
wikify_simple to wikify, archive legacy code"`, `0b968fb "Delete
scholarforge.code-workspace"`). These are the genuine rename/refactor history of
the project, not leaks. **Keep.** A public README should simply explain the current
name; the history needs no edit.

---

## Execution note (when approved — not done here)

Items 1, 2, and 4 (and optionally 3/5) are all identity/trailer edits across many
commits and must be done in **one** `git filter-repo` pass to avoid repeated
hash churn:

- Item 1: `--message-callback` (or `--replace-message`) deleting any
  `Claude-Session: ...` line.
- Items 2 & 4: `--mailmap` mapping the Gmail ident to the GitHub noreply ident and
  removing the redundant `fgrillo89` co-author line.

This rewrites every downstream hash and requires a force-push and re-clone by any
collaborator. Defer until the owner explicitly approves the rewrite; this document
only proposes it.
