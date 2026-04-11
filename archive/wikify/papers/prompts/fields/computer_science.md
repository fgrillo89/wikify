# Computer Science Writing Guide

## Exemplary Papers
- Vaswani et al. (2017) "Attention Is All You Need" — NeurIPS. Clean modular presentation: architecture, then training, then results. Figures explain the mechanism at a glance.
- He et al. (2016) "Deep Residual Learning for Image Recognition" — CVPR. Problem stated in one paragraph, solution in the next. Ablation studies as the gold standard of empirical rigor.
- Devlin et al. (2019) "BERT: Pre-training of Deep Bidirectional Transformers" — NAACL. Clear separation of pre-training and fine-tuning. Tables that tell the story without prose.
- Krizhevsky et al. (2012) "ImageNet Classification with Deep CNNs" — NeurIPS. Landmark empirical paper; direct writing, no hedging on results.
- Hochreiter & Schmidhuber (1997) "Long Short-Term Memory" — Neural Computation. Rigorous mathematical presentation with clear motivation for each design choice.
- Kingma & Ba (2015) "Adam: A Method for Stochastic Optimization" — ICLR. Concise algorithm description with pseudocode that practitioners can implement directly.
- Silver et al. (2016) "Mastering the Game of Go with Deep Neural Networks" — Nature. Complex system explained in layers of increasing detail.
- Goodfellow et al. (2014) "Generative Adversarial Networks" — NeurIPS. Three-page paper that launched a subfield. Brevity as a virtue.

## Field Conventions
- **Structure**: Introduction (problem + contribution list), Related Work, Method, Experiments, Results, Conclusion. Appendix for proofs and extra experiments.
- **Citations**: Author-year in text (Vaswani et al., 2017) for ML venues. Numbered for IEEE venues.
- **Figures**: Architecture diagrams, training curves, attention visualizations, ablation bar charts. Pseudocode in algorithm blocks. Tables dominate results sections.
- **Tone**: Direct, somewhat informal compared to natural sciences. First person plural ("we propose"). Bold claims backed by benchmarks.

## Actionable Instructions
- State contributions as a numbered list in the introduction. Readers skim; make claims findable.
- Include pseudocode or algorithm blocks for any novel method. Mathematical notation alone is insufficient for reproducibility.
- Ablation studies are mandatory: remove each component, measure the drop. This is the primary evidence for design choices.
- Report baselines honestly. Use the same dataset splits, hyperparameter budgets, and compute resources for fair comparison.
- Figures must have large, readable axis labels. Conference papers are read on screens at 100% zoom; 6pt font fails.
- Error bars or confidence intervals on all quantitative results. Report mean and standard deviation over multiple runs.
- Related work should position your contribution, not survey the field. "X does A but not B; we address B" is the pattern.
- Avoid vague improvement claims. "Our method improves accuracy by 2.3 points on ImageNet" beats "significantly outperforms."
- Reproducibility: report hyperparameters, training time, hardware, random seeds. Link to code if possible.
