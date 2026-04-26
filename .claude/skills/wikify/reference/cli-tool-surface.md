---
name: wikify/reference/cli-tool-surface
description: v2 CLI grammar — six nouns, deterministic verbs only.
---

# CLI tool surface (v2)

The Wikify CLI is six nouns. Each verb is deterministic Python — no
model SDK calls. Strategy (loop shape, model tier, budget allocation,
stopping criteria) lives in skill markdown, never in Python defaults.

```
wikify <noun> <verb> [args...]
```

## Bundle resolution

- ``--run <bundle>`` overrides; otherwise the current working
  directory must be a v2 bundle root (``run/state.json`` present).
- Mutating verbs acquire ``run/lock`` for the duration. Lock contention
  exits 2.
- Per-concept mutations under ``work/concepts/<slug>/`` acquire the
  concept's ``.claim`` file (TTL-driven; same exit-2 contract).

## Exit codes

- 0 success
- 1 validation / precondition failure
- 2 lock or claim held
- 3 budget exceeded
- 4 stale claim broken by ``work tend``

## Default output

Terse text. Add ``--format json`` for stable automation parsing.

---

## ``wikify corpus``

Read-only against a corpus during a wiki run; ``corpus build`` /
``corpus refresh`` are the only mutating verbs.

```
wikify corpus build <source> --out <corpus> [--mode additive|sync]
                              [--parser default|lite|marker|docling]
                              [--workers N] [--no-refresh]
wikify corpus refresh <corpus>
wikify corpus check   <corpus> [--format text|json]
wikify corpus list    docs|chunks|files [--corpus <c>] [--doc <d>]
wikify corpus find    "<query>" [--corpus <c>] [--top-k N] [--text]
wikify corpus find    --seed [--corpus <c>] [--max N] [--pagerank-weight W]
wikify corpus show    <handle> [--corpus <c>] [--full]
```

Handles: ``doc:<id>`` or ``chunk:<id>``.

## ``wikify run``

```
wikify run init   --bundle <b> --corpus <c> [--strategy <label>]
                  [--target-haiku-eq N]
wikify run show   [--run <b>] [--detail|--full] [--format text|json]
wikify run list   events [--run <b>] [--tail N] [--type <t>]
wikify run lock   [--run <b>] [--owner <id>] [--ttl-seconds N]
wikify run unlock [--run <b>]
wikify run close  [--run <b>] [--status completed|failed|abandoned]
wikify run set    [--run <b>] [--target-haiku-eq N] [--strategy-note <s>]
```

``--strategy`` is a free-form workflow label (``baseline``, ``guided``,
``free``, ``query``); the agent picks. No Python branch reads it.

## ``wikify work``

```
wikify work list                        [--run <b>]
wikify work list claims                 [--run <b>]
wikify work list inbox                  [--run <b>]
wikify work list evidence <concept>     [--run <b>]
wikify work show <concept>              [--run <b>] [--full]
wikify work add  concept "<title>"      [--run <b>] [--kind article|person]
                                        [--aliases <json>]
wikify work add  evidence <concept>     --records <jsonl-path> [--run <b>]
wikify work add  feedback <kind>        --record <json|jsonl-path> [--run <b>]
wikify work set  <concept>              [--status <s>] [--needs-refine]
wikify work claim <concept>             [--owner <id>] [--ttl-seconds N]
wikify work release <concept>           [--owner <id>]
wikify work tend
```

Feedback kinds: ``evidence`` | ``concept`` | ``merge`` | ``query``.

## ``wikify draft``

```
wikify draft build <concept> --task create|refine --corpus <c>
                             --model-id <id> --tier S|M|L [--run <b>]
wikify draft show  <concept> [--run <b>] [--full]
wikify draft check <concept> [--run <b>]
```

``--model-id`` and ``--tier`` are required. Strategy lives in skills.

## ``wikify wiki``

```
wikify wiki list  [articles|people|files] [--run <b>]
wikify wiki find  "<query>" [--run <b>] [--text]
wikify wiki show  <handle>   [--run <b>] [--full]
wikify wiki build indexes|graph|vectors  [--run <b>]
wikify wiki check                        [--run <b>]
wikify wiki commit <concept>             [--run <b>] [--ensure-projections]
```

``wiki commit`` is the gate: refuses unless ``draft.json`` /
``response.json`` / ``validation.json`` are present and
``validation.ok`` is true. Acquired ``run/lock`` for the mutation.

## ``wikify migrate``

```
wikify migrate inspect <bundle> [--format text|json]
```

Read-only inspection of a v1 (legacy) bundle. Reports presence of
legacy artifacts.
