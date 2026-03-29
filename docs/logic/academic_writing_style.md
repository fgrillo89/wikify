# Academic Writing Style Guide for LLM Generation

> Read this document before generating any research paper, review, or academic draft.
> Every rule here is a direct instruction. Follow them unless the user explicitly overrides.
>
> **Self-revision requirement:** After generating each section, re-read it against this
> guide. Check for: banned words/phrases, nominalizations, passive voice overuse, missing
> known-new flow, unsupported claims, vague quantifiers, and structural LLM tells (em-dashes,
> uniform hedging, rule-of-three). Revise any violations before returning the text.

## 1. Sentence-Level Clarity

**Use active voice by default.** Write "We measured the film thickness" not "The film thickness was measured." Reserve passive voice only when the actor is irrelevant or unknown, or when convention demands it (e.g., "Samples were annealed at 300 C" is acceptable in Methods).

**Put the subject and verb close together, early in the sentence.** Do not bury the main verb behind a long subordinate clause. Bad: "The relationship between growth rate and precursor partial pressure, which has been debated in the literature for decades, remains unclear." Better: "The relationship between growth rate and precursor partial pressure remains unclear, despite decades of debate."

**Use strong, precise verbs — avoid nominalizations.** Do not turn verbs into abstract nouns (the "zombie nouns" ending in -ation, -ism, -ity, -ence). Write "We analyzed" not "We performed an analysis of." Write "Implementing the policy irritated the staff" not "The implementation of the policy was a cause of irritation." Write "X increases Y" not "X leads to an increase in Y."

**Make characters the subjects and actions the verbs.** The real actors in your story (researchers, devices, processes) should be grammatical subjects. Bad: "An increase in resistance was observed." Better: "The device exhibited increased resistance." This is Williams' core principle: readers understand sentences best when subjects are characters and verbs are their actions.

**Cut every word that does not earn its place.** Apply Strunk & White's Rule 17: omit needless words. Delete filler phrases: "it is worth noting that," "it should be mentioned that," "in order to" (use "to"), "a number of" (use "several" or state the number), "due to the fact that" (use "because"), "in the context of" (use "in" or "for"). Delete qualifiers that weaken prose: "very," "rather," "quite," "fairly," "pretty." They add nothing.

**Use short words over long ones when meaning is equal.** Write "use" not "utilize," "show" not "demonstrate," "about" not "approximately" (unless precision matters), "begin" not "commence," "help" not "facilitate."

**Vary sentence length deliberately.** Mix short declarative sentences (8-12 words) with longer compound ones (20-30 words). Never produce five consecutive sentences of similar length. Short sentences deliver emphasis. Use them for key claims.

**Be specific.** Replace vague quantifiers with numbers. Write "three samples" not "several samples." Write "at 350 K" not "at elevated temperature." Specificity is the single strongest signal of expert human writing.

## 2. Paragraph Structure and Flow

**Each paragraph makes exactly one point.** State that point in the first or second sentence (the topic sentence). The remaining sentences provide evidence, elaboration, or qualification. The final sentence either concludes the point or transitions to the next paragraph.

**Use the known-new contract (old-to-new flow).** Begin each sentence with information the reader already knows (from the previous sentence or established context), then place new, complex, or "heavy" information at the end. This creates forward momentum without explicit transition words. Williams calls this the single most powerful tool for readable prose.

**Apply the breadcrumb test.** Every sentence must logically necessitate the next. If a sentence can be removed without breaking the chain of logic, delete it. Each paragraph is a chain, not a collection.

**Limit paragraphs to 4-8 sentences.** A paragraph longer than 10 sentences almost certainly contains two ideas. Split it.

**Use transition words sparingly and precisely.** "However" signals a genuine contrast. "Moreover" signals a genuine addition. "Therefore" signals a genuine logical consequence. Do not use these words as paragraph-starting filler. If the logical connection is clear from context, omit the transition word entirely. When you do use them, match the relationship precisely:
- Contrast: "however," "despite this," "on the other hand"
- Concession: "admittedly," "of course"
- Elaboration: "specifically," "in other words"
- Consequence: "therefore," "as a result"

## 3. Section-by-Section Guidance

### Abstract (150-250 words typically)

Structure as four moves: (1) Context — one or two sentences on the problem and why it matters. (2) Gap or objective — what is unknown or what this paper does. (3) Approach and key results — the most important findings with numbers. (4) Significance — what this means for the field.

Do not start with "This paper presents..." or "In this study, we..." Start with the scientific context. Do not include citations, abbreviations (unless universally known), or figure references. Every sentence must carry information; there is no room for filler.

### Introduction

Follow the "funnel" structure: broad context, narrowing to the specific gap, then your contribution. Apply the Graff/Birkenstein "They Say / I Say" framework: always establish the existing conversation ("they say") before stating what this paper contributes ("I say"). Apply McEnerney's principle: create instability. The reader must feel that something is unresolved, contradictory, or unknown. Use words like "however," "yet," "remains unclear," "conflicting reports" to establish the gap.

End the Introduction with a clear statement of what this paper does and, optionally, a brief roadmap of the paper's structure. Do not preview results here unless the journal style requires it.

Integrate citations to support claims, not to pad the reference list. Every cited work should serve a purpose: establishing context, identifying the gap, or justifying the approach.

### Methods (or Experimental)

Write with enough detail for reproduction. Use past tense. Organize by logical procedure, not chronological order. Group related techniques under subheadings.

State specific parameters: temperatures, pressures, durations, concentrations, equipment models. Do not write "standard conditions" without defining them. Reference established protocols by citation rather than re-describing them in full.

### Results

Present findings in logical order, which may differ from the order experiments were performed. Lead each subsection with the main finding, then support it with data.

Direct the reader's attention when referencing figures and tables: "Film thickness increased linearly with cycle count (Fig. 2a)" is better than "Fig. 2a shows the results." State what the reader should see, then point to the evidence.

Report quantitative results with appropriate precision and uncertainty. Write "3.2 +/- 0.1 nm" not "approximately 3 nm." Include statistical measures where relevant.

Do not interpret results here unless the journal uses a combined Results and Discussion section. State what was observed, not what it means.

### Discussion

Interpret results in context of existing literature. Structure as: (1) Summarize the key finding. (2) Compare with prior work — agree or disagree, and explain why. (3) Propose mechanisms or explanations. (4) Acknowledge limitations honestly. (5) State implications.

Distinguish clearly between what the data shows and what you infer from it. Use hedging appropriately but not excessively: "These results suggest" is fine; "These results might potentially suggest" is not.

Address counterarguments. If an alternative explanation exists, state it and explain why your interpretation is preferred. This is argumentation, not explanation.

### Conclusion

Do not simply restate the abstract. Synthesize: what do the results mean collectively? State the primary contribution in one or two sentences. Identify concrete future directions — not vague "further study is needed" but specific next experiments or open questions.

Keep this section short (one to three paragraphs). End with the strongest, most forward-looking statement.

## 4. Citation Integration

**Weave citations into the narrative.** Do not dump citations at the end of a sentence as an afterthought. Bad: "ALD has been used for many applications [1-15]." Better: "ALD enables conformal coating of high-aspect-ratio structures [1,2], and recent work has extended it to area-selective deposition [3,4]."

**Use author names when the finding matters more than the field consensus.** Write "Smith et al. demonstrated that..." when referring to a specific, important result. Use bracketed numbers or parenthetical citations when citing general background.

**Do not cite sources you have not read or that do not exist.** If a specific citation is unavailable, write a placeholder like [REF] and flag it for the user. Never fabricate DOIs, page numbers, or author names.

**Match citation density to section.** Introduction and Discussion are citation-heavy. Methods cite protocols and instruments. Results cite your own figures and tables primarily. The Abstract and Conclusion rarely contain citations.

## 5. Avoiding LLM-Specific Pitfalls

### Banned Words and Phrases

Never use these unless the user explicitly includes them in source material:

- "delve," "delves," "delving"
- "crucial," "pivotal," "paramount," "groundbreaking," "cutting-edge," "novel" (use "new"), "innovative"
- "landscape" (as metaphor), "tapestry," "beacon," "realm," "multifaceted"
- "it is important to note that," "it bears mentioning"
- "In recent years" as an opening phrase
- "a comprehensive overview," "a nuanced understanding"
- "showcases," "underscores," "highlights" (as emphasis verbs — use "shows," "supports," "indicates")
- "meticulous," "intricate," "commendable"
- "foster," "leverage," "harness" (as academic verbs)

### Structural Pitfalls

**Do not use em-dashes as parenthetical separators.** Use commas or parentheses instead. Em-dashes are a strong LLM tell.

**Do not produce bullet points in paper body text.** Academic papers use flowing prose paragraphs, not lists. Lists are acceptable only in Methods when enumerating procedural steps, or in Supplementary Material.

**Do not summarize what you are about to say, then say it, then summarize what you said.** State things once. The Introduction states the problem; the Conclusion synthesizes findings. Do not echo the same sentences in both.

**Do not use the "rule of three" pattern** where you list three adjectives or three parallel clauses reflexively. Vary your structures.

**Do not hedge uniformly.** Some claims deserve confidence ("X is 3.2 nm"). Others need qualification ("This suggests Y"). Vary hedging intensity based on evidence strength. Uniform hedging signals machine generation.

**Avoid elegant variation.** If you call something "growth rate" in one sentence, do not switch to "deposition velocity" in the next just to avoid repetition. Consistent terminology is more important than stylistic variety in scientific writing.

**Do not open sections with meta-commentary.** Never write "This section discusses..." or "In the following, we describe..." Just begin with the content.

## 6. Tone and Register

Write as a confident expert communicating with peers. Do not write as a student explaining to a teacher. Do not write as a salesperson promoting results.

Maintain a professional but not stilted tone. First person ("we") is appropriate and encouraged in most journals. Avoid "the authors" as a self-reference.

Do not be grandiose. Replace "This groundbreaking study reveals" with "This study shows." Let the data speak. Readers judge significance themselves.

Do not be falsely modest. Replace "We humbly present" or "This modest contribution" with direct statements.

## 7. Logical Argumentation

**Every claim needs support.** A claim can be supported by: your data, a citation, or a logical derivation from established premises. Unsupported claims destroy credibility.

**Signal the logical structure.** If A causes B, say so directly: "A causes B because..." If A correlates with B, do not imply causation. Use "A correlates with B" or "A is associated with B."

**Handle limitations honestly.** State what the study cannot show. State what confounding factors exist. This strengthens, not weakens, the paper — it demonstrates scientific rigor.

**Make comparisons precise.** "Higher than" requires a referent. "Our method is faster" requires: faster than what? By how much? Under what conditions?

## 8. Figures and Tables

**Every figure and table must be referenced in the text.** Reference them at the point where the reader needs to see them, not in a separate paragraph.

**Describe what the figure shows, then what it means.** "Figure 3 shows the XRD pattern of the deposited film. The sharp peak at 2-theta = 33.0 deg corresponds to the (100) reflection of crystalline ZnO, confirming the wurtzite phase."

**Do not describe every data point in a figure.** Identify trends and key features. The figure itself provides the detail.

**Table captions go above tables; figure captions go below figures.** Captions must be self-contained — a reader should understand the figure without reading the main text.

## 9. Transitions Between Sections

Use the last paragraph of each section to set up the next section's topic. The final sentence of Results might note an unexpected observation that motivates the Discussion. The final sentence of the Introduction states what the paper does, leading naturally into Methods or Results.

Do not use explicit signpost phrases like "The next section will discuss..." Instead, create logical bridges through content. If the Results show an anomaly, begin the Discussion by addressing that anomaly. The reader follows the logic, not the signpost.

---

*Sources synthesized: Williams, "Style: Toward Clarity and Grace" (characters-as-subjects, known-new contract); Strunk & White, "The Elements of Style"; Orwell, "Politics and the English Language" (six rules); Schimel, "Writing Science"; Sword, "Stylish Academic Writing" (zombie nouns, verb vitality); Graff & Birkenstein, "They Say / I Say" (argumentative moves); McEnerney (UChicago) on value-driven academic writing; Nature author guidelines; ACS Guide to Scholarly Communication; Stanford "Writing in the Sciences" (Sainani); MIT/Broad Institute CommKit.*
