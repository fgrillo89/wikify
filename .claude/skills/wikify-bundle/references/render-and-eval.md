# Render And Eval

Rendering and evaluation consume committed wiki state.

```bash
wikify render --bundle <bundle> --format html [--out <dir>] [--corpus <corpus>]
wikify eval --bundle <bundle> [--corpus <corpus>] [--report <path>]
```

Render writes a static site, usually under `derived/site/`. Eval writes
metrics, usually under `derived/eval.json`.

These commands do not choose strategy. They snapshot the result of a
workflow for inspection and comparison.
