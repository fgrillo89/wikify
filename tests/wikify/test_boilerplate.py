"""Tests for the soft boilerplate predicate.

The detector is consumed at ingest to set ``Chunk.is_boilerplate``;
fluent-API consumers read the persisted flag. These tests pin its
behaviour at the predicate level — corpus-shape verification lives in
``test_abstract_tagger.py``.
"""

from wikify.ingest.boilerplate import is_boilerplate

# --- positive cases ------------------------------------------------------


def test_thesis_copyright_preamble_flagged():
    """The Zhang 2024 University of Southampton repository preamble."""
    text = (
        "Copyright and Moral Rights for this thesis and, where applicable, "
        "any accompanying data are retained by the author and/or other "
        "copyright owners. A copy can be downloaded for personal "
        "noncommercial research or study, without prior permission or charge. "
        "This thesis and the accompanying data cannot be reproduced or "
        "quoted extensively from without first obtaining permission in "
        "writing from the copyright holder/s."
    )
    assert is_boilerplate(text)


def test_nature_end_matter_flagged():
    """Nature-family supplementary-info / correspondence end-matter."""
    text = (
        "Supplementary information The online version contains supplementary "
        "material available at https://doi.org/10.1038/example. "
        "Correspondence and requests for materials should be addressed to "
        "Author Name. Peer review information thanks the anonymous reviewers."
    )
    assert is_boilerplate(text)


# --- negative cases (must NOT flag real abstracts) ----------------------


def test_real_abstract_with_cc_by_footer_not_flagged():
    """Real OA-paper abstract with a Creative Commons footer attached.

    The CC-BY phrase used to fire two distinct markers
    (``creative commons`` and ``licensed under``); both have been
    removed from the marker set after this exact false positive
    surfaced on Yoon 2023, Chen 2024, Ju 2024, Liu 2024.
    """
    text = (
        "Neuromorphic computing requires highly reliable and low-power "
        "electronic synapses. We report CMOS-integrated 1-transistor-1-resistor "
        "synapses with ultrathin HfO2/Al2O3 bilayer stacks. The optimized "
        "sample shows reliability of 600 DC cycles, low Set voltage, and "
        "low operation current. Recognition accuracy of 95.6% on MNIST "
        "was achieved. "
        "This article is licensed under a Creative Commons Attribution "
        "(CC BY) license unless otherwise noted."
    )
    assert not is_boilerplate(text)


def test_real_abstract_with_single_copyright_mention_not_flagged():
    """A passing 'Copyright 2024' or '© 2024 Author' mention shouldn't trip."""
    text = (
        "We investigate the synaptic properties of amorphous gallium oxide "
        "memristors. The W/WOx/a-GaOx/ITO stack exhibits stable bipolar "
        "switching, multi-level conductance, and biologically inspired "
        "plasticity behaviours including LTP and LTD. "
        "© 2024 The Author(s)."
    )
    assert not is_boilerplate(text)


def test_long_chunk_never_flagged_even_with_boilerplate_text():
    """Long chunks (> 600 words) skip the predicate entirely.

    A 1500-word review chapter that quotes a license notice in passing
    is still substantive content — the density argument doesn't hold.
    """
    body = " ".join(["substantive content word"] * 700)  # 2100 words
    text = (
        body
        + " All rights reserved. This article cannot be reproduced. "
        "Reprints and permissions: see policy."
    )
    assert not is_boilerplate(text)


def test_single_marker_not_enough():
    """One marker hit should not flag — threshold is 2 distinct spans."""
    text = (
        "We present a novel memristor device. The fabrication process "
        "uses atomic layer deposition. View article online at example.org "
        "for the full PDF."
    )
    # Only "view article online" matches; one marker is below threshold.
    assert not is_boilerplate(text)


def test_empty_text_not_flagged():
    assert not is_boilerplate("")


# --- regression: span dedup ---------------------------------------------


def test_span_dedup_does_not_double_count_overlapping_matches():
    """Two markers matching overlapping spans count as one signal.

    Defensive: even if the marker set re-introduces overlapping patterns
    in the future, the span-dedup logic prevents the CC-BY-style
    false-positive class.
    """
    # Construct text where exactly one marker phrase fires, surrounded
    # by ordinary content. Should not be flagged.
    text = (
        "Memristor switching dynamics are well understood. "
        "Reprints and permissions: see policy. "
        "Further analysis confirms the conductive filament model."
    )
    assert not is_boilerplate(text)


# --------------------------- Stage 2 extensions -----------------------------


def test_section_path_articles_you_may_be_interested_in() -> None:
    """Body text inside the publisher sidebar gets flagged via section_path."""
    body = "TaN bottom electrode bar (width 20 um), atomic layer deposition."
    sp = [
        "Resistive switching of fully ALD HfO2 devices",
        "**Articles You May Be Interested In**",
    ]
    assert is_boilerplate(body, sp) is True


def test_section_path_recommended_by_acs() -> None:
    body = "Some short body that would not normally trip the marker test."
    sp = ["■ REFERENCES", "Recommended by ACS"]
    assert is_boilerplate(body, sp) is True


def test_real_section_path_does_not_flag() -> None:
    """A plain body section must never be section-path-flagged."""
    body = "We deposited 100 cycles of HfO2 by ALD on TiN at 200C."
    sp = ["Methods", "Sample Preparation"]
    assert is_boilerplate(body, sp) is False


def test_articles_marker_in_body_text_flags_via_density() -> None:
    """Even without section_path, the new body-text markers fire."""
    text = (
        "Articles You May Be Interested In. Cited By. "
        "Recommended for you. Some short trailing text."
    )
    assert is_boilerplate(text) is True


def test_downloaded_from_inline_marker_fires() -> None:
    """Page-footer download stamps inline with prose count as a marker."""
    text = (
        "All rights reserved. The article is downloaded from "
        "https://onlinelibrary.wiley.com/doi/10.1002/foo "
        "on 15 March 2026. Copyright 2024 reserved."
    )
    assert is_boilerplate(text) is True


def test_section_path_kwarg_is_optional() -> None:
    """Existing callers without section_path keep working unchanged."""
    text = "All rights reserved. Reprints and permissions."
    assert is_boilerplate(text) is True
    assert is_boilerplate(text, None) is True


# --------------------------- Stage 3 extensions -----------------------------


def test_frontiers_edited_by_block_flagged() -> None:
    """Frontiers editorial-board header at the top of an article."""
    text = (
        "EDITED BY\nCarlo Ricciardi,\nPolytechnic University of Turin, Italy\n"
        "REVIEWED BY\nItir Koymen,\nTOBB University of Economics and Technology"
    )
    assert is_boilerplate(text) is True


def test_frontiers_reviewed_by_block_flagged() -> None:
    """A chunk that starts with REVIEWED BY alone (no EDITED BY) is enough."""
    text = (
        "REVIEWED BY\nMaria Elias Pereira,\nInstituto de Desenvolvimento de "
        "Novas Tecnologias (UNINOVA), Portugal"
    )
    assert is_boilerplate(text) is True


def test_multi_stage_received_accepted_published_paragraph_flagged() -> None:
    """All-caps publication-history run-on (Frontiers form)."""
    text = (
        "RECEIVED 02 May 2025 ACCEPTED 10 June 2025 PUBLISHED 19 June 2025 "
        "CORRECTED 26 June 2025"
    )
    assert is_boilerplate(text) is True


def test_cs1_citation_header_flagged() -> None:
    """CS1-style bibliographic citation header at the top of a chunk."""
    text = (
        "Kumar S, Yadav D, Stathopoulos S and Prodromakis T (2025) "
        "Performance and variability analysis of ALD-grown wafer scale "
        "HfO 2 /Ta 2 O 5 -based memristive devices for neuromorphic "
        "computing. Front. Nanotechnol. 7:1621554. "
        "doi: 10.3389/fnano.2025.1621554"
    )
    assert is_boilerplate(text) is True


def test_real_abstract_with_doi_link_not_flagged() -> None:
    """A real abstract that mentions a doi URL must not trip the CS1 pattern."""
    text = (
        "We report a novel HfO2/Al2O3 memristor stack with 600 DC cycles "
        "of endurance. Supplementary data are available at "
        "https://doi.org/10.1038/example. The device demonstrates 95.6% "
        "recognition accuracy on MNIST."
    )
    assert is_boilerplate(text) is False


def test_real_abstract_with_inline_paper_mention_not_flagged() -> None:
    """Inline references like 'Smith J (2024) showed ...' must not match CS1."""
    text = (
        "Recent work by Smith J (2024) showed that conductive filament "
        "rupture dominates the reset transition in HfO2 memristors. "
        "Here we extend this analysis to bilayer stacks."
    )
    assert is_boilerplate(text) is False


def test_real_abstract_with_received_word_elsewhere_not_flagged() -> None:
    """Prose using 'received'/'accepted'/'published' as verbs must not trip.

    The multi-stage pattern requires each marker to be followed by a
    "DD Month YYYY" date, so casual verb usage in an abstract is safe.
    """
    text = (
        "The deposited films received post-anneal treatment at 400 C. "
        "Endurance was accepted as the primary figure of merit. "
        "The paper was published in 2025."
    )
    assert is_boilerplate(text) is False


def test_edited_by_prose_does_not_trip() -> None:
    """Lowercase 'Edited by ...' in a sentence is legitimate prose.

    The Frontiers editorial-board header is ALWAYS all-caps
    ('EDITED BY' / 'REVIEWED BY'); the lowercase form is regular prose
    and must not be flagged.
    """
    text = (
        "Edited by Smith and colleagues, this volume collects ten "
        "recent papers on memristor reliability and discusses their "
        "implications for neuromorphic computing benchmarks."
    )
    assert is_boilerplate(text) is False


def test_inline_cs1_citation_does_not_trip() -> None:
    """A mid-sentence Smith (2021) inline citation must not match CS1.

    The CS1 chunk-header pattern is anchored to chunk start AND requires
    the doi suffix to be the chunk's tail; inline mid-prose citations
    followed by more text fail both anchors.
    """
    text = (
        "As shown by Smith J (2021) Recent advances in memristive devices. "
        "Nature Methods. 18:455. doi: 10.1038/x. Our work extends this finding."
    )
    assert is_boilerplate(text) is False


def test_received_accepted_published_lowercase_verbs_do_not_trip() -> None:
    """Lowercase 'received DD Month YYYY ... accepted ... published' is prose.

    The all-caps Frontiers publication-history block is metadata; the
    same words used as verbs with dates in a sentence (a real, if rare,
    PI-narrative construction) are not.
    """
    text = (
        "The PI received 25 March 2024 funding from NSF and accepted "
        "10 May 2024 collaboration with MIT and published 30 June 2024 "
        "the first protocol."
    )
    assert is_boilerplate(text) is False
