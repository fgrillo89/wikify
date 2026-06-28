"""Tests for boilerplate filtering in the ingest chunker + cleaner.

The bug these cover: Wiley / Elsevier / ACS license footers and author
affiliation blocks were reaching the chunker and showing up as top
similarity hits on vague queries because they're lexically dense and
identical across papers.
"""

from wikify.ingest.chunker import _is_boilerplate_chunk, chunk_document
from wikify.ingest.images import caption_chunks_for
from wikify.ingest.parsers._clean import clean_markdown_text
from wikify.ingest.parsers._sections import section_spans
from wikify.models import DocImage

_WILEY_LICENSE = (
    "16163028, 2026, 3, Downloaded from https://advanced.onlinelibrary.wiley.com"
    "/doi/10.1002/adfm.202510663 by Ajou University, Central, Wiley Online "
    "Library on [15/03/2026]. See the Terms and Conditions "
    "(https://onlinelibrary.wiley.com/terms-and-conditions) on Wiley Online "
    "Library for rules of use; OA articles are governed by the applicable "
    "Creative Commons License"
)


# -------------------------------------------------------------------------
# Cleaner-level: publisher license paragraph stripping
# -------------------------------------------------------------------------


def test_clean_strips_wiley_license_paragraph() -> None:
    md = (
        "# Paper Title\n\n"
        "## Introduction\n\n"
        "Memristors are two-terminal devices that retain resistance state.\n\n"
        + _WILEY_LICENSE
        + "\n\n"
        "Body content resumes here with a complete sentence.\n"
    )
    out = clean_markdown_text(md)
    assert "Downloaded from" not in out
    assert "onlinelibrary.wiley.com" not in out
    assert "Creative Commons License" not in out
    # Real content is preserved.
    assert "Memristors are two-terminal" in out
    assert "Body content resumes" in out


def test_clean_strips_elsevier_sciencedirect_paragraph() -> None:
    md = (
        "## Methods\n\n"
        "We measured the I-V response under 405 nm illumination.\n\n"
        "Downloaded for Anonymous User (n/a) at Example University from "
        "ScienceDirect on March 10, 2026. For personal use only.\n\n"
        "Sample preparation used standard ALD at 250 C.\n"
    )
    out = clean_markdown_text(md)
    assert "Downloaded for" not in out
    assert "ScienceDirect" not in out
    assert "405 nm illumination" in out
    assert "Sample preparation used standard ALD" in out


def test_clean_strips_creative_commons_license_block() -> None:
    md = (
        "## Discussion\n\n"
        "The device shows stable bipolar switching over 10^6 cycles.\n\n"
        "Open Access This article is licensed under a Creative Commons "
        "Attribution-NonCommercial-NoDerivatives 4.0 International License.\n\n"
        "Future work will explore 3D integration.\n"
    )
    out = clean_markdown_text(md)
    assert "licensed under a Creative Commons" not in out
    assert "bipolar switching" in out
    assert "3D integration" in out


def test_clean_preserves_rights_in_scientific_prose() -> None:
    """A legitimate mention of the word 'rights' in science context must survive."""
    md = (
        "## Theory\n\n"
        "Crystal symmetry rights the observed degeneracy in the "
        "phonon dispersion and also explains the right-handed spin "
        "texture near the K point of the Brillouin zone. This analysis "
        "is required to understand the anomalous Hall response.\n"
    )
    out = clean_markdown_text(md)
    assert "Crystal symmetry rights" in out
    assert "right-handed spin" in out


def test_clean_strips_affiliation_footer() -> None:
    md = (
        "# A Real Paper\n\n"
        "## Abstract\n\nThe abstract describes memristor behavior.\n\n"
        "Micro- and Nanoelectronic Systems, Institute of Micro and "
        "Nanotechnologies MacroNano, Technische Universitat Ilmenau, "
        "Ilmenau, Germany. email: author.one@example.edu\n\n"
        "## Introduction\n\nBody text continues.\n"
    )
    out = clean_markdown_text(md)
    assert "Institute of Micro" not in out
    assert "author.one@example.edu" not in out
    assert "abstract describes memristor behavior" in out
    assert "Body text continues" in out


def test_clean_preserves_institute_reference_without_email() -> None:
    """Real prose that mentions an institute without email stays intact."""
    md = (
        "## Acknowledgments\n\n"
        "We gratefully acknowledge funding from the Institute of Physics "
        "of the Chinese Academy of Sciences for their continued support "
        "of this collaborative research program.\n"
    )
    out = clean_markdown_text(md)
    assert "Institute of Physics" in out
    assert "Chinese Academy of Sciences" in out


# -------------------------------------------------------------------------
# Chunker-level: boilerplate safety net
# -------------------------------------------------------------------------


def test_is_boilerplate_chunk_flags_wiley_block() -> None:
    assert _is_boilerplate_chunk(_WILEY_LICENSE) is True


def test_is_boilerplate_chunk_flags_creative_commons_block() -> None:
    text = (
        "Open Access This article is licensed under a Creative Commons "
        "Attribution License, which permits unrestricted use in any "
        "medium, provided the original author and source are credited. "
        "Copyright 2024, the authors, under the Creative Commons License."
    )
    assert _is_boilerplate_chunk(text) is True


def test_is_boilerplate_chunk_keeps_science_prose() -> None:
    text = (
        "We deposited HfO2 by atomic layer deposition at 250 C using "
        "tetrakis(ethylmethylamino)hafnium as the metal precursor and "
        "water as the oxidant. The resulting films show stable bipolar "
        "resistive switching with set voltages near 1.2 V and a HRS/LRS "
        "ratio exceeding 100. Retention was measured at 85 C and exceeds "
        "1e4 seconds without drift."
    )
    assert _is_boilerplate_chunk(text) is False


def test_is_boilerplate_chunk_keeps_short_text() -> None:
    # Too short for the ratio test; should not trip.
    text = "Downloaded data from ScienceDirect."
    # Short text ( < _BOILERPLATE_MIN_WORDS ) is not evaluated by ratio,
    # and "from sciencedirect" alone is not in the hard-phrase allowlist.
    assert _is_boilerplate_chunk(text) is False


def test_chunk_document_drops_all_license_chunk() -> None:
    """A section whose body is entirely Wiley boilerplate produces no chunks."""
    body = (
        "# Paper\n\n"
        "## Page Footer\n\n"
        + _WILEY_LICENSE
        + "\n"
    )
    spans = section_spans(body)
    chunks = chunk_document("doc1", body, spans)
    for c in chunks:
        assert "Downloaded from" not in c.text
        assert "onlinelibrary.wiley.com" not in c.text


def test_chunks_from_docling_respects_max_chunk_cap() -> None:
    """Regression: Docling's HybridChunker emits chunks past its nominal
    max_tokens=2000 when it merges peers, producing 8 k-char chunks that
    bypass the chunker's max_chunk_chars() safety net. _chunks_from_docling
    must apply _split_oversize like chunk_document does."""
    from wikify.ingest.chunker import max_chunk_chars
    from wikify.ingest.pipeline import _chunks_from_docling

    cap = max_chunk_chars()
    # Build an oversize docling chunk (3× cap, sentence-boundaried so
    # _split_oversize can cut cleanly).
    sentence = "Atomic layer deposition grows thin films by self-limiting surface reactions. " * 3
    big_text = sentence * max(1, (3 * cap) // len(sentence))
    docling_chunks = [
        {"text": big_text, "heading_path": ["body"]},
        {"text": "A normal-sized section about ALD precursors and growth kinetics.",
         "heading_path": ["body", "Methods"]},
    ]
    chunks = _chunks_from_docling("doc_big", docling_chunks)
    # Every emitted chunk must be within the cap.
    for c in chunks:
        assert len(c.text) <= cap, (
            f"chunk {c.ord} is {len(c.text)} chars, exceeds cap {cap}"
        )
    # The oversize chunk should have been split, so we see multiple chunks
    # from the body path.
    body_chunks = [c for c in chunks if c.section_path == ["body"]]
    assert len(body_chunks) >= 2


def test_chunk_document_keeps_real_content_alongside_license() -> None:
    """If cleaner misses a license fragment fused with content, chunker may
    still emit a mixed chunk — but a pure license section must be dropped.
    The test here ensures content sections aren't accidentally rejected
    by the keyword heuristic.
    """
    body = (
        "# Paper\n\n"
        "## Methods\n\n"
        "Thin films of HfO2 were grown by plasma-enhanced atomic layer "
        "deposition on 200 mm silicon wafers. The precursor sequence "
        "consisted of TEMAH and O2 plasma pulses separated by Ar purges. "
        "Film thickness was measured by spectroscopic ellipsometry and "
        "cross-sectional TEM imaging. The growth rate per cycle was "
        "0.11 nm at 250 degrees Celsius, consistent with literature.\n"
    )
    spans = section_spans(body)
    chunks = chunk_document("doc2", body, spans)
    assert any("atomic layer deposition" in c.text for c in chunks)


def test_chunk_document_labels_inline_figure_and_table_chunks() -> None:
    body = (
        "# Paper\n\n"
        "## Figure Panel\n\n"
        "Figure 1. Device switching curves under pulsed voltage operation.\n\n"
        "## Table Data\n\n"
        "| Sample | Current density | Switching endurance |\n"
        "| --- | --- | --- |\n"
        "| Device A | 10 mA per square centimeter | one million cycles |\n"
    )
    chunks = chunk_document("doc3", body, section_spans(body))
    assert any(c.section_type == "figure" for c in chunks)
    assert any(c.section_type == "table" for c in chunks)


def test_caption_chunks_are_labeled_caption() -> None:
    chunks = caption_chunks_for(
        "doc4",
        [
            DocImage(
                id="doc4/Figure_01",
                path="images/doc4/Figure_01.png",
                caption="Figure 1. Device stack schematic.",
            )
        ],
        ord_offset=0,
    )
    assert chunks[0].section_path == ["__image__", "doc4/Figure_01"]
    assert chunks[0].section_type == "caption"
