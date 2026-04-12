"""Domain-persona generation for the layered writer prompt.

A corpus persona is a 150-200 word expert perspective statement generated
ONCE per corpus from a small sample of source documents. It is prepended
to every writer system message so the article tone, register, and
vocabulary stay consistent across the entire wiki.

This module is binding-agnostic: it takes a ``complete`` callable that
maps a single prompt string to a single text response. The CLI is
responsible for wiring that callable to either a deterministic stub
(``--binding fake``) or the Claude Code dispatcher (``--binding
file_dispatch``). No vendor imports live here.

The result is written to ``<corpus_root>/persona.txt`` and the same
string is returned. ``distill.pipeline.run`` reads the file at the start
of every run.
"""

from typing import Callable

from wikify.models import Document
from wikify.paths import CorpusPaths

CompleteFn = Callable[[str], str]

_PERSONA_PROMPT_TEMPLATE = """\
You are about to write Wikipedia-style encyclopedia articles for a curated
knowledge base. Below is a sample of sources from the corpus. Define the
expert perspective from which all articles should be written.

Corpus field hint: {field}

Sample of sources in this corpus:
{source_sample}

Your response must address:
1. REGISTER: What technical vocabulary and level of precision is appropriate?
2. CLAIMS: What distinguishes a strong claim from an opinion or speculation
   in this domain?
3. UNCERTAINTY: How is uncertainty qualified in this field?
4. DEBATES: What are the active disputes that should appear in
   "Open Questions" sections?
5. READER: Who reads this wiki -- researcher, engineer, practitioner?
   What do they most need from each article?

Write 150-200 words in second person ("You are a senior..."). Be specific
to this domain, not generic. Plain prose only. No bullet lists, no section
headings, no meta-commentary about how you wrote this.
"""

_STUB_PERSONA = (
    "You are a senior researcher writing neutral encyclopedia articles for "
    "a curated corpus of technical literature. You maintain precise "
    "vocabulary, prefer specific numbers over vague quantifiers, and ground "
    "every claim in the supplied evidence. You distinguish observation from "
    "interpretation, calibrate hedging to evidence strength, and never "
    "describe how the corpus was searched or how the article was assembled. "
    "Your reader is a domain practitioner who needs accurate definitions, "
    "clear mechanisms, and an honest accounting of what is unresolved."
)


def _format_sample(docs: list[Document], n: int = 20) -> str:
    sample = docs[:n]
    if not sample:
        return "(no documents in corpus)"
    lines: list[str] = []
    for d in sample:
        title = d.title or d.id
        abstract_snippet = (d.abstract or d.tldr or "").strip().replace("\n", " ")
        if len(abstract_snippet) > 240:
            abstract_snippet = abstract_snippet[:237] + "..."
        if abstract_snippet:
            lines.append(f"- {title} -- {abstract_snippet}")
        else:
            lines.append(f"- {title}")
    return "\n".join(lines)


def build_persona_prompt(docs: list[Document], field: str = "generic") -> str:
    """Return the full persona-generation prompt string."""
    return _PERSONA_PROMPT_TEMPLATE.format(
        field=field,
        source_sample=_format_sample(docs),
    )


def generate_corpus_persona(
    *,
    corpus: CorpusPaths,
    sample_docs: list[Document],
    complete: CompleteFn | None,
    field: str = "generic",
) -> str:
    """Generate, persist, and return the corpus persona.

    Args:
        corpus: paths handle for the target corpus; the result is written
            to ``corpus.persona_path``.
        sample_docs: up to 20 documents to seed the persona prompt.
        complete: a callable that maps prompt -> response. If ``None``
            (or for the fake binding), the function returns a deterministic
            stub persona without invoking any model.
        field: optional field-name hint passed into the prompt.

    Returns:
        The persona text that was written to disk.
    """
    if complete is None:
        text = _STUB_PERSONA
    else:
        prompt = build_persona_prompt(sample_docs, field=field)
        text = complete(prompt).strip() or _STUB_PERSONA
    corpus.persona_path.parent.mkdir(parents=True, exist_ok=True)
    corpus.persona_path.write_text(text, encoding="utf-8")
    return text


def load_corpus_persona(corpus: CorpusPaths) -> str:
    """Return the cached persona text or an empty string if absent."""
    p = corpus.persona_path
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8").strip()
