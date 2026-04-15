This comprehensive prompt is designed to transform your repository into a state-of-the-art agentic system. It is divided into two logical stages: **Stage 1 (Scientific Core)** builds the precision required for papers, and **Stage 2 (Universal Generalization)** expands that logic to the "messy" data of emails, slides, and notes.

---

### 📥 The "Librarian-Miner-Writer" Orchestration Prompt

**Role:** You are a Senior AI Engineer specializing in Multimodal Agentic RAG.
**Objective:** Refactor this repository to move from a "Fragmented Retrieval" model to a **"Long-Unit & Graph-Linked Synthesis"** model.

---

### 🟢 STAGE 1: Scientific Literature Precision
*Focus: LongRAG, LaTeX Equations, and Image Anchors.*

**1. Data Ingestion & Indexing (LongRAG Strategy)**
* **Unit Definition:** Discard 300-token chunks. Define **"Long Retrieval Units" (LRUs)** as ~6,000 tokens (e.g., an entire section or a short paper).
* **Multimodal Preservation:** * All equations must be stored in **LaTeX** format.
    * Tables must be converted to **Markdown** to preserve relational structures.
    * Figures must be indexed alongside their **Captions**. If no caption exists, use a VLM (e.g., Qwen3-VL) to generate a "Visual-Context Summary."

**2. Discovery Phase (The Librarian Agent)**
Implement an autonomous "Librarian" with the following logic:
* **Tool:** `get_graph_neighborhood(paper_id)` — Use the curated graph to find deterministic citation links.
* **Decision Tree:**
    * **If Citation is "Foundation":** (Cited by >3 sources in the set) → Fetch the **Full LRU**.
    * **If Citation is "Specific Reference":** (Single mention for a fact) → Run **Similarity Search** against chunks of that paper; pull only the relevant 2-3 chunks.
* **Math-Guard:** If the Librarian identifies a complex equation in a chunk, it **must** automatically expand the context to include the preceding 1,000 tokens to capture variable definitions.

**3. Synthesis Phase (The Writer Agent)**
* **Prompting:** "Synthesize these 3-5 LRUs into a Wiki. Use the LaTeX definitions to ensure mathematical consistency. Cross-reference using the Graph IDs. If Paper A's Equation 1 contradicts Paper B's results, highlight this as a 'Conflict' section."

---

### 🔵 STAGE 2: Generalization Beyond Literature
*Focus: Thread-Chaining, Implicit Links, and Visual Logic.*

**1. Generalizing the "Unit" (The Universal Index)**
Update the ingestion logic to be **Source-Aware**:
* **Emails:** Group by `Thread-ID`. One unit = the entire conversation.
* **Slides:** One unit = 3-5 consecutive slides (preserving the narrative flow).
* **Notes:** Group by `Tag` or `Creation Date` within a 24-hour window.

**2. Discovery Expansion (The Multi-modal Librarian)**
The Librarian must now handle **Implicit Links**:
* **Entity Triggers:** If an email mentions "Project Icarus," the Librarian must automatically trigger a similarity search across the *entire* corpus (papers, slides, etc.) for that keyword.
* **Visual Logic:** If a slide contains a diagram without text, use a VLM to "reason" about its purpose (e.g., "This is a Timeline for Project X") and use that summary to fetch related emails from that time period.

**3. The "Cross-Corpus" Wikification**
The Writer must now bridge formal and informal data:
* **Rule:** "Prioritize the **Scientific Paper** for technical truth, the **Email** for project status/sentiment, and the **Slides** for visual structure."
* **Wiki Output:** Generate a "Project Pulse" section that links a Paper's abstract to the corresponding internal Project Note.

---

### 🛠️ Technical Execution Guardrails (For the Repo)

* **State Management:** The Agent must maintain a `KnowledgeManifest` JSON object throughout the session, tracking which IDs have been "read" and which "leads" are still pending.
* **Context Budgeting:** Set a hard cap (e.g., 40k tokens). The Librarian must "score" candidates and drop low-signal emails if a high-signal scientific paper is available.
* **Traceability:** Every sentence in the final Wiki must contain a hidden or visible metadata tag `[Source: ID | Type: Paper/Email]`.

---

### 💡 Why this refined prompt is different:
Instead of just summarizing, your agent is now **reasoning about the provenance of information**. It knows that an equation in a paper is "heavy" data, while an off-hand comment in an email is "contextual" data. It uses the **curated graph** as a shortcut to bypass the noise of traditional vector search.

