# Physics Writing Guide

## Exemplary Papers
- Einstein (1905) "On the Electrodynamics of Moving Bodies" — Annalen der Physik. Built special relativity from two postulates with thought experiments. Physical intuition before mathematics.
- Purcell (1977) "Life at Low Reynolds Number" — Am. J. Physics. Legendary clarity; makes fluid dynamics intuitive through scaling arguments and humor.
- LIGO Collaboration (2016) "Observation of Gravitational Waves from a Binary Black Hole Merger" — PRL. 1000+ authors, impeccably structured. Signal description, detector characterization, and astrophysical interpretation in clear layers.
- Higgs (1964) "Broken Symmetries and the Masses of Gauge Bosons" — PRL. Two pages. One mechanism. Changed the Standard Model.
- Anderson (1972) "More Is Different" — Science. Perspective on emergence; showed that reductionism has limits. Model of conceptual argumentation.
- Feynman (1982) "Simulating Physics with Computers" — Int. J. Theoretical Physics. Conversational tone that makes quantum computing intuition accessible.
- Weinberg (1967) "A Model of Leptons" — PRL. Electroweak unification in three pages. Every sentence essential.
- Haldane (1988) "Model for a Quantum Hall Effect without Landau Levels" — PRL. Tight theoretical argument with a concrete lattice model.

## Field Conventions
- **Structure**: Introduction (physical motivation + key result), Theory/Model, Experimental Methods or Computational Methods, Results, Discussion, Conclusion. PRL imposes a 4-page limit forcing compression.
- **Citations**: Numbered brackets [1]. APS journals use REVTeX formatting.
- **Figures**: Phase diagrams, band structures, Feynman diagrams, experimental apparatus schematics, data with fits. Error bars mandatory. Log-scale axes common.
- **Tone**: Direct and confident. Passive voice in methods, active elsewhere. Physical reasoning drives the narrative; math supports it.

## Actionable Instructions
- Lead with the physics, not the math. State what happens physically before writing the Hamiltonian or Lagrangian.
- Dimensional analysis and scaling arguments belong in the introduction. They orient the reader and constrain the solution space.
- PRL's 4-page limit is a feature: learn to compress. Every sentence must earn its place. Move derivations to supplemental material.
- Equations are part of the sentence. "The energy is E = mc^2, where m is the rest mass" with proper punctuation.
- Define physical quantities with their units inline: "the critical temperature T_c = 92 K."
- Error analysis is not optional. Report systematic and statistical uncertainties separately. Propagate errors through derived quantities.
- Figures: plot data points with error bars, theoretical fits as smooth curves. Never connect data points with lines unless interpolation is justified.
- Comparison with theory: overlay experimental data and theoretical predictions on the same plot. Discrepancies are interesting; discuss them.
- Order-of-magnitude estimates strengthen arguments. "This corresponds to roughly 10^3 atoms" gives physical intuition that exact numbers do not.
- Acknowledge approximations: state where your model breaks down and why it still captures the relevant physics.
