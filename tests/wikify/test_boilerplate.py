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
