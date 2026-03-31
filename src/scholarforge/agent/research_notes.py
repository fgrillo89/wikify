"""Structured research notes — the handoff between explorer and writer agents.

ResearchNotes captures everything the explorer discovered in a compact,
schema-validated format. The writer receives this instead of raw tool
results, keeping its context small and focused.

Design: extensible beyond papers and reviews. The PaperSummary model
can represent any document (paper, patent, report, dataset description).
ResearchNotes can feed into any output type (review, presentation,
grant proposal, Q&A response).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SourceSummary(BaseModel):
    """Structured extraction from a single source document.

    Designed to work for papers, patents, reports, or any text source.
    The field names are generic enough to extend beyond academic papers.
    """

    display_name: str = Field(description="Source identifier matching tool output display names")
    key_findings: list[str] = Field(
        default_factory=list,
        description="3-5 specific findings with numbers and measurements",
    )
    quantitative_data: list[str] = Field(
        default_factory=list,
        description="Specific values, metrics, or statistics extracted",
    )
    relevance: str = Field(default="", description="1-2 sentences: why this source matters")
    gaps_noted: list[str] = Field(
        default_factory=list,
        description="Limitations, missing experiments, or open questions in this source",
    )
    read_depth: str = Field(default="digest", description="full | digest | section | search")


class ResearchNotes(BaseModel):
    """Complete research notes from an exploration session.

    This is the ONLY data that crosses the explorer->writer boundary.
    The writer receives `to_writer_prompt()` as its input, not raw
    tool results or message history.

    Extensible: works for any corpus type and any output format.
    """

    topic: str = Field(description="The user's original query or review topic")
    source_summaries: list[SourceSummary] = Field(
        default_factory=list,
        description="Structured extractions from each source read",
    )
    gap_analysis: str = Field(
        default="",
        description="Raw output from gap detection tools",
    )
    synthesis_opportunities: str = Field(
        default="",
        description="Raw output from synthesis opportunity tools",
    )
    key_contradictions: list[str] = Field(
        default_factory=list,
        description="Contradictions or disagreements found between sources",
    )
    proposed_outline: list[str] = Field(
        default_factory=list,
        description="Proposed section structure: 'N. Heading - description'",
    )

    def to_writer_prompt(self) -> str:
        """Serialize as structured markdown for the writer agent.

        Returns ~5-8KB of organized research context that the writer
        transforms into prose. The format is scannable and citation-ready.
        """
        lines = [
            f"# Research Notes: {self.topic}",
            "",
            f"**Sources consulted**: {len(self.source_summaries)}",
            "",
        ]

        # Proposed outline
        if self.proposed_outline:
            lines.append("## Proposed Structure")
            for item in self.proposed_outline:
                lines.append(f"- {item}")
            lines.append("")

        # Source summaries
        lines.append("## Source Summaries")
        lines.append("")
        for i, s in enumerate(self.source_summaries, 1):
            lines.append(f"### {i}. {s.display_name} [{s.read_depth}]")
            if s.relevance:
                lines.append(f"**Relevance**: {s.relevance}")
            if s.key_findings:
                lines.append("**Findings**:")
                for f in s.key_findings:
                    lines.append(f"  - {f}")
            if s.quantitative_data:
                lines.append("**Data**: " + "; ".join(s.quantitative_data))
            if s.gaps_noted:
                lines.append("**Gaps**: " + "; ".join(s.gaps_noted))
            lines.append("")

        # Gap analysis
        if self.gap_analysis:
            lines.append("## Gap Analysis")
            lines.append(self.gap_analysis)
            lines.append("")

        # Synthesis opportunities
        if self.synthesis_opportunities:
            lines.append("## Synthesis Opportunities")
            lines.append(self.synthesis_opportunities)
            lines.append("")

        # Contradictions
        if self.key_contradictions:
            lines.append("## Key Contradictions")
            for c in self.key_contradictions:
                lines.append(f"- {c}")
            lines.append("")

        return "\n".join(lines)

    @classmethod
    def from_session(cls, topic: str) -> ResearchNotes:
        """Build ResearchNotes from the current session's paper summaries.

        Pulls from the module-level _paper_summaries in tools.py,
        useful when the explorer ran as a single agent (not via
        run_structured) and we need to construct notes post-hoc.
        """
        from scholarforge.agent.tools import get_paper_summaries

        summaries = get_paper_summaries()
        source_summaries = [
            SourceSummary(
                display_name=s["paper_name"],
                key_findings=s.get("key_findings", []),
                quantitative_data=s.get("quantitative_data", []),
                relevance=s.get("relevance", ""),
                gaps_noted=s.get("gaps_noted", []),
                read_depth=s.get("read_depth", "digest"),
            )
            for s in summaries
        ]
        return cls(topic=topic, source_summaries=source_summaries)
