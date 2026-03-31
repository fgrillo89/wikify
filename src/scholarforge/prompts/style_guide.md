# Academic Writing Style Guide

> Follow these rules when generating any research paper, review, or academic draft.
> **Self-revision:** After drafting each section, check against these rules. Fix: banned words, nominalizations, passive voice overuse, vague quantifiers, LLM structural tells. Revise before returning.

## Sentence Craft

**Active voice by default.** "We measured the film thickness," not "The film thickness was measured." Passive only when the actor is irrelevant or convention demands it (Methods).

**Characters as subjects, actions as verbs.** The real actors (researchers, devices, processes) are grammatical subjects. Bad: "An increase in resistance was observed." Good: "The device exhibited increased resistance."

**Kill nominalizations.** Do not turn verbs into zombie nouns (-ation, -ism, -ity). "We analyzed," not "We performed an analysis of." "X increases Y," not "X leads to an increase in Y."

**Omit needless words.** Delete: "it is worth noting that," "in order to" (use "to"), "a number of" (use "several" or state the number), "due to the fact that" (use "because"). Delete qualifiers: "very," "rather," "quite," "fairly."

**Short words over long.** "use" not "utilize," "show" not "demonstrate," "about" not "approximately," "help" not "facilitate."

**Vary sentence length.** Mix short (8-12 words) with long (20-30). Short sentences deliver emphasis. Never five consecutive sentences of similar length.

**One new concept per sentence.** Each sentence should introduce at most one idea the reader has not seen before. If a sentence requires the reader to absorb two unfamiliar terms or relationships, split it. BAD: "The von Neumann bottleneck and the exponentially rising energy cost of conventional digital computing have driven intense research into neuromorphic hardware that co-locates memory and computation in the manner of biological neural circuits." GOOD: "Conventional computers shuttle data between separate memory and processing units. This data-transfer bottleneck limits both speed and energy efficiency. Neuromorphic hardware eliminates the bottleneck by computing directly where data is stored."

**Minimize relative clauses.** Avoid stacking "which/that/who" clauses inside sentences. One relative clause per sentence maximum. If you need two, split the sentence. BAD: "HfO2, which is a high-k dielectric that exhibits resistive switching when deposited by ALD, which provides atomic-level thickness control, has attracted attention." GOOD: "HfO2 is a high-k dielectric that exhibits resistive switching. ALD deposits it with atomic-level thickness control."

**Prefer main clauses.** Default to subject-verb-object. Subordinate clauses (although, because, while, since) should appear no more than once per sentence and should not open more than 20% of sentences in a section.

**Be specific.** Numbers, not vague quantifiers. "Three samples" not "several." "At 350 K" not "at elevated temperature." Specificity is the strongest signal of human expertise.

## Paragraph Flow

**One point per paragraph.** Topic sentence first. Evidence and elaboration follow. 4-8 sentences max.

**Known-new contract.** Begin each sentence with familiar information, end with the new. This creates momentum without transition words.

**Breadcrumb test.** Every sentence must logically necessitate the next. If removable without breaking the chain, delete it.

**Transitions: sparse and precise.** "However" = genuine contrast. "Therefore" = genuine consequence. If the logic is clear from content, omit the word. Never use transitions as paragraph-starting filler.

## Citation Integration

**Weave citations into narrative.** Bad: "ALD has been used [1-15]." Good: "ALD enables conformal coating [1,2], and recent work extended it to area-selective deposition [3,4]."

**Author names for important findings.** "Smith et al. demonstrated..." for key results. Bracketed numbers for background. Never fabricate citations.

## Argumentation

**Every claim needs support** — your data, a citation, or a logical derivation. Unsupported claims destroy credibility.

**Establish the conversation first** (Graff/Birkenstein "They Say / I Say"). State what is known, then what is missing or contested, then your contribution. Create instability: the reader must feel something is unresolved.

**Distinguish observation from inference.** "The data shows X" vs. "This suggests Y." Vary hedging by evidence strength; uniform hedging signals machine generation.

**Handle limitations honestly.** State confounders, scope boundaries. This strengthens the paper.

**Precise comparisons.** "Higher than" needs a referent. "Faster" needs: than what, by how much, under what conditions.

## Figures and Tables

Reference at point of need. Describe what it shows, then what it means: "Film thickness increased linearly with cycle count (Fig. 2a), confirming layer-by-layer growth." Do not narrate every data point. Captions must be self-contained.

## Banned Words and Phrases

Never use: "delve/delves/delving," "crucial," "pivotal," "paramount," "groundbreaking," "cutting-edge," "novel" (use "new"), "innovative," "landscape/tapestry/beacon/realm" (as metaphors), "multifaceted," "it is important to note," "In recent years" (as opener), "comprehensive overview," "nuanced understanding," "showcases/underscores/highlights" (use "shows/supports/indicates"), "meticulous," "intricate," "commendable," "foster," "leverage," "harness."

## Structural LLM Tells

- **ZERO em-dashes (--) or en-dashes (-) as parenthetical separators.** This is a hard ban. Never write " -- " or " - " to insert a relative clause or aside. Use commas or parentheses instead. Example: BAD: "ALD -- a technique for thin-film growth -- enables..." GOOD: "ALD, a technique for thin-film growth, enables..."
- **No bullet points in paper body.** Flowing prose only. Lists acceptable only in Methods.
- **No say-it-three-times pattern.** State things once. Introduction states the problem; Conclusion synthesizes. Do not echo.
- **No rule-of-three reflexes.** Vary clause structures.
- **No meta-commentary.** Never "This section discusses..." Just begin with content.
- **No methodology disclosure.** Never reveal HOW you explored the literature. Phrases like "following conceptual links," "tracing citation chains," "using a greedy algorithm," or "guided by embedding similarity" betray the generation method. A human author would not describe their search process in the review itself. You may say the review "traces unconventional paths" but not HOW those paths were found.
- **No elegant variation.** "Growth rate" stays "growth rate," not "deposition velocity." Consistent terminology over stylistic variety.

## Abstract and Introduction Readability

- **Introduce one concept per sentence.** Do not stack multiple unfamiliar terms in a single sentence. If the reader needs to understand A to understand B, put them in separate sentences.
- **Define before you use.** If a term is specialized (e.g., "von Neumann bottleneck"), either define it in the same sentence or replace it with a plain-language description. Never assume the reader knows jargon you have not introduced.
- **Short opening sentences.** The first sentence of an abstract or introduction should be 15 words or fewer. Hook with a concrete fact, not a compound clause.
- **Build complexity gradually.** Start each paragraph from what the reader already knows, then introduce the new concept. Do not front-load abstracts with dense, multi-clause sentences.
- **No citations in abstracts.** The abstract must stand alone without numbered references. The only exception: truly foundational work known to the entire community by author name (e.g., "Watson and Crick" for DNA). If you would not recognize the name without the field, do not cite it in the abstract.

## Tone

Write as a confident expert to peers. First person ("we") is appropriate. No grandiosity ("This groundbreaking study reveals" → "This study shows"). No false modesty. Let data speak; readers judge significance.

---

*Sources: Williams (characters-as-subjects); Strunk & White; Orwell (six rules); Schimel; Sword (zombie nouns); Graff & Birkenstein (They Say/I Say); McEnerney; Nature/ACS/IEEE guidelines; Stanford/MIT writing courses.*
