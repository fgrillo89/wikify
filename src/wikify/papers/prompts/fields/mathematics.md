# Mathematics / Statistics Writing Guide

## Exemplary Papers
- Shannon (1948) "A Mathematical Theory of Communication" — Bell System Technical Journal. Founded information theory in a single paper. Remarkable clarity: complex ideas built from simple, well-motivated definitions.
- Turing (1936) "On Computable Numbers" — Proc. London Math. Soc. Introduced the Turing machine with precise definitions and patient construction of examples.
- Nash (1950) "Equilibrium Points in N-Person Games" — PNAS. One page, one theorem, one proof. Extreme economy.
- Milnor (1956) "On Manifolds Homeomorphic to the 7-Sphere" — Annals of Mathematics. Clear exposition of a surprising result; each step logically inevitable.
- Perelman (2002-2003) arXiv preprints on Poincare conjecture — Unconventional format but mathematically airtight; shows that clarity of argument trumps polish.
- Cox (1972) "Regression Models and Life-Tables" — JRSS-B. Introduced the Cox proportional hazards model; clean statistical motivation with immediate practical application.
- Tukey (1977) "Exploratory Data Analysis" — Introduced box plots and stem-and-leaf displays through visual intuition before formalism.
- Efron (1979) "Bootstrap Methods: Another Look at the Jackknife" — Annals of Statistics. Complex resampling idea made accessible through concrete examples.

## Field Conventions
- **Structure**: Introduction (motivation + main result statement), Preliminaries (definitions, notation), Main Results (theorem-proof blocks), Applications/Examples, Discussion. No Methods section.
- **Citations**: Numbered or author-year depending on journal. AMS journals use numbered; statistics journals often use author-year.
- **Figures**: Diagrams, commutative diagrams, graphs. Statistics papers use plots extensively. Pure math papers may have zero figures.
- **Tone**: Formal, impersonal ("one observes," "it follows that," "we define"). First person plural is standard even for single authors.

## Actionable Instructions
- State the main result in the introduction, informally. Readers need to know the destination before the journey.
- Define every symbol before first use. Notation section or inline definitions; never assume shared conventions across subfields.
- Theorem-proof structure is mandatory. State theorems, lemmas, and corollaries as numbered, self-contained blocks. Proofs end with a tombstone symbol.
- Build from simple to complex: start with a special case or example, then generalize. Shannon did this; so should you.
- Separate the idea of a proof from its technical execution. A proof sketch before the formal proof helps the reader.
- In statistics: state assumptions explicitly and discuss what happens when they fail. Robustness is always relevant.
- Examples are not optional. Every abstract definition needs at least one concrete instance.
- Notation must be consistent throughout. If X is a random variable on page 2, it cannot be a topological space on page 7.
- For applied statistics: present the method on real data, not just simulations. Practitioners need to see the workflow.
- Write transitions between proofs. A sequence of lemma-proof blocks with no connecting prose is unreadable.
