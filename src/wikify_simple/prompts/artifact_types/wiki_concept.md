# Wiki Concept Article — Output Template

Write a kind="concept" page in neutral Wikipedia voice, grounded in the supplied evidence.

Voice: neutral, declarative, third person prose. No em-dashes as parenthetical separators. No meta-commentary. No invented claims. No [[wikilinks]]. Cite using [^eN] markers (1-based into evidence list).

Figures: name by label in prose ("as shown in Figure 3"), then embed ![Figure N](<path>) on the immediately following line. Never group at top.

Sections (guidance, not strict): Definition | Background | Mechanism/Process | Applications | Open Questions | References (required, last). Use whatever section names fit the concept. References format: [^eN]: <chunk_id> (<doc_id>) > "<quote>" — one line per cited entry.

Hard minimums (validator enforced): body >=1200 chars; >=1 H2 heading; >=3 prose paragraphs outside References; >=1 [^eN] in prose; no [[wikilinks]]; final ## References with >=1 [^eN]: definition; every prose marker has a matching definition.
