"""Wiki article authoring agent.

Provides direct-LLM functions (not agent loops) for writing and updating
wiki articles from corpus evidence. Uses the same LLM client as pi_review.py.
"""

from __future__ import annotations

_ARTICLE_SYSTEM_PROMPT = """\
You are a technical knowledge-base writer. Your task is to write a focused,
well-structured wiki article on a given concept, based on evidence from a
research corpus.

Guidelines:
- 400-800 words for stubs/drafts; 600-1200 for full articles.
- Write in clear, declarative prose. One concept per sentence.
- Use inline citations in the format [REF:paper_id] immediately after the
  claim they support. Never cluster citations.
- Structure with ## headings: Overview, Key Properties/Methods, Applications,
  Open Questions (or similar — adapt to the topic).
- Do not include a title heading (the frontmatter title field handles that).
- Do not use em-dashes as separators. Do not start sentences with "However," or
  "Moreover," as filler transitions.
- Ground every non-trivial claim in the provided evidence. If a claim is not
  supported, omit it or explicitly flag it as speculative.
"""

_UPDATE_SYSTEM_PROMPT = """\
You are updating an existing wiki article with new evidence from the research
corpus. Revise the article to incorporate new findings, correct outdated
claims, and add new citations where appropriate.

Rules:
- Return the complete revised article body (no frontmatter).
- Mark revised passages clearly with new [REF:...] citations.
- Remove or qualify claims that contradict newer evidence.
- Keep the same section structure unless a new section is clearly needed.
- Do not change the word count by more than 30%.
"""


def build_wiki_article(
    title: str,
    topic_query: str,
    status: str = "draft",
    model: str | None = None,
    top_k: int = 8,
) -> tuple[str, list[str]]:
    """Use the LLM to write a wiki article on `title`.

    Steps:
      1. search_papers(topic_query, top_k) to get relevant sources.
      2. For top 3 sources: read_paper_digest to get evidence.
      3. Call LLM to write a focused concept article (400-800 words)
         with inline [REF:...] citations.

    Args:
        title: Article title (also used as concept for article writing).
        topic_query: Query string for corpus search.
        status: "stub", "draft", or "full" — controls target length hint.
        model: litellm model string. Defaults to settings.llm_model.
        top_k: Number of papers to retrieve for evidence.

    Returns:
        (article_markdown_content, list_of_source_paper_ids)
    """
    from scholarforge.agent.tools import read_paper_digest, search_papers
    from scholarforge.llm.client import complete

    # Step 1: search for relevant papers
    search_result = search_papers(topic_query, top_k=top_k, reason=f"wiki article: {title}")

    # Extract paper IDs from the search result (format: "Paper: <id> | ...")
    import re

    source_ids: list[str] = re.findall(r"Paper:\s*([a-f0-9]{8,})", search_result)
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_ids: list[str] = []
    for pid in source_ids:
        if pid not in seen:
            seen.add(pid)
            unique_ids.append(pid)

    # Step 2: deep-read top 3 sources
    digests: list[str] = []
    for paper_id in unique_ids[:3]:
        digest = read_paper_digest(paper_id[:16], reason=f"evidence for wiki: {title}")
        if digest:
            digests.append(digest)

    # Build evidence block
    evidence = "\n\n---\n\n".join(digests) if digests else search_result

    length_hint = {
        "stub": "200-300 words",
        "draft": "400-600 words",
        "full": "600-1200 words",
    }.get(status, "400-800 words")

    user_msg = (
        f"Write a wiki article titled '{title}'.\n"
        f"Target length: {length_hint}.\n\n"
        f"Evidence from the corpus:\n\n{evidence}"
    )

    content = complete(
        messages=[
            {"role": "system", "content": _ARTICLE_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        model=model,
        temperature=0.3,
        max_tokens=2000,
        use_cache=False,
    )

    return content, unique_ids


def update_wiki_article(
    existing_content: str,
    new_source_digests: list[str],
    model: str | None = None,
) -> str:
    """Update an existing article with new evidence.

    The LLM receives the current article and new digests, and returns a
    revised version incorporating new findings.

    Args:
        existing_content: Full current article body (without frontmatter).
        new_source_digests: List of read_paper_digest results for new sources.
        model: litellm model string. Defaults to settings.llm_model.

    Returns:
        Revised article body (without frontmatter).
    """
    from scholarforge.llm.client import complete

    new_evidence = "\n\n---\n\n".join(new_source_digests) if new_source_digests else ""

    user_msg = (
        "Here is the current wiki article:\n\n"
        f"{existing_content}\n\n"
        "---\n\n"
        "New evidence to incorporate:\n\n"
        f"{new_evidence}"
    )

    return complete(
        messages=[
            {"role": "system", "content": _UPDATE_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        model=model,
        temperature=0.2,
        max_tokens=2500,
        use_cache=False,
    )
