# Sampling Patterns

Named patterns are building blocks for workflows. They are not default
strategies.

## pagerank-entrypoint

Start from central documents returned by corpus seed ranking. Useful for
first-pass baselines.

## abstract-first

Read abstracts, introductions, and conclusions before full documents.
Useful when documents are long and the workflow needs broad candidate
concepts cheaply.

## citation-neighborhood

Traverse cited and citing papers from seed sources. Useful for finding
communities around a method or concept.

## topic-vocabulary

Use extracted topics or author keywords as probes. Useful for coverage
checks and concept recall.

## wiki-gap-driven

Start from thin, orphan, or low-evidence committed pages. Useful for
refinement workflows.

## query-driven

Use user questions to expose missing concepts or evidence. Useful for
interactive improvement.

## coverage-residual

Explore chunks far from current wiki embeddings. Useful for benchmark
strategies once residual metrics are available.

## author-network

Use bibliography and coauthor graph structure to discover person pages
and communities.
