# Article Style

Use for `kind="article"` pages.

## Lead

- Start with the bold title in the first sentence.
- Define what the subject is.
- Follow with context or significance grounded in evidence.
- No heading above the lead.

## Body

- Use at least two topical `## H2` sections before `## References`.
- Choose headings that match the evidence, such as `## Background`,
  `## Mechanism`, `## Applications`, `## Properties`, or
  `## Characterization`.
- Every topical section should include at least one evidence marker.
- Place `[^eN]` markers where Wikipedia would: every distinct claim
  must be backed by nearby evidence, but a single marker can carry the
  surrounding sentences in the same paragraph. Anchor specific facts
  (numbers, named devices, mechanisms, historical events) directly;
  let connective and summary sentences ride on the nearest marker.
- Do not add sections unsupported by evidence.

## Math and chemistry

The renderer typesets `$...$` (inline) and `$$...$$` (display) regions
with KaTeX. Wrap formulas, symbolic expressions, and chemical notation
in math delimiters when the evidence contains them:

- inline scalar relations and symbols: `$M(q) = d\varphi / dq$`,
  `$E_g \approx 5.6\,\text{eV}$`;
- display equations on their own line: `$$\Delta G = \Delta H - T\,\Delta S$$`;
- subscripts and superscripts: `$\text{HfO}_2$`, `$\text{Ca}^{2+}$`,
  `$\text{Ge}_2\text{Sb}_2\text{Te}_5$`;
- chemical reactions with mhchem when needed: `$\ce{Hf + 2 H2O -> HfO2 + 2 H2}$`.

Inline maths counts as much as display maths. EVERY mathematical
symbol, subscript, superscript, and relation in running prose must be
delimited, not just the equations that sit on their own line: write
`$\sigma_D$`, not `σ_D` or `sigma_D`; `$G_{i,j} \approx (t_i -
t_j)^{-\beta}$`, not `G_{i,j} ≈ (t_i − t_j)^(−β)`. Undelimited notation
renders as literal text, and an article with zero inline `$...$` is
almost always wrong.

Never paste a Unicode maths character. Greek letters and operators
(`σ β δ α λ ≈ ∝ ∼ ≤ ≥ ≠ − × ⟨ ⟩`) go in as LaTeX commands inside a math
region — `$\sigma$`, `$\approx$`, `$\propto$`, `$\langle c \rangle$` —
never as the raw character. A bare `<c>` for ⟨c⟩ is worse: markdown eats
it as an HTML tag and the symbol vanishes from the page.

`wikify draft check` reports an `undelimited_math` warning counting every
symbol left outside a math region (verbatim quotations, fenced code, and
the `## References` block are exempt). It does not block the commit; fix
the count to zero anyway.

Do not invent equations. If the quoted evidence does not contain a
formula, do not introduce one. Plain unit strings such as `100 nm`,
`5 µs`, or `1.8 V` should remain plain text, not math.

`response.json` is JSON, so every backslash inside a math region must
be doubled. Write `"$\\Delta G = \\Delta H - T\\,\\Delta S$"` in the
JSON string; it decodes to `$\Delta G = \Delta H - T\,\Delta S$` in
the committed markdown. A single backslash before any letter
(`\Delta`, `\varphi`, `\text`, `\ce`) is an invalid JSON escape and
the response will be unparseable.

## References

`## References` is always the final section and contains one definition
per cited evidence marker.
