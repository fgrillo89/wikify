"""Tests for ``ingest.parsers._clean.clean_markdown_text``."""

from __future__ import annotations

from wikify_simple.ingest.parsers._clean import clean_markdown_text


def test_strips_licensing_lines() -> None:
    md = (
        "# A Real Title\n\n"
        "Authorized licensed use limited to: Some University. "
        "Downloaded on April 08,2026 at 12:00:00 UTC from IEEE Xplore. "
        "Restrictions apply.\n\n"
        "This is the actual abstract text describing memristors.\n"
    )
    out = clean_markdown_text(md)
    assert "authorized licensed use" not in out.lower()
    assert "restrictions apply" not in out.lower()
    assert "actual abstract text" in out


def test_strips_copyright_paragraph() -> None:
    md = (
        "# Title\n\n"
        "Copyright 2008 IEEE. All rights reserved. Personal use of this "
        "material is permitted.\n\n"
        "Real body content about resistive switching follows here with "
        "a complete sentence and a period.\n"
    )
    out = clean_markdown_text(md)
    assert "all rights reserved" not in out.lower()
    assert "real body content" in out.lower()


def test_strips_repeated_running_header() -> None:
    header = "IEEE TRANSACTIONS ON CIRCUIT THEORY VOL. CT-18 NO. 5"
    md = "\n\n".join(
        [
            "# Memristor",
            header,
            "Page one body talking about the missing element.",
            header,
            "Page two body continuing the derivation.",
            header,
            "Page three body with the conclusion.",
        ]
    )
    out = clean_markdown_text(md)
    assert header not in out
    assert "missing element" in out
    assert "derivation" in out


def test_strips_leading_journal_heading() -> None:
    md = (
        "## IEEE Transactions on Circuit Theory\n\n"
        "# Memristor: The Missing Circuit Element\n\n"
        "## Abstract\n\nWe show that a fourth element exists.\n"
    )
    out = clean_markdown_text(md)
    # The journal heading goes; the real title heading stays.
    assert "## IEEE Transactions" not in out
    assert "Memristor: The Missing" in out
    assert "fourth element" in out


def test_preserves_real_content() -> None:
    md = (
        "# Real Title\n\n"
        "## Abstract\n\nA proper abstract sentence.\n\n"
        "## Introduction\n\nA proper intro sentence.\n"
    )
    out = clean_markdown_text(md)
    assert "Real Title" in out
    assert "proper abstract sentence" in out
    assert "proper intro sentence" in out


def test_empty_input() -> None:
    assert clean_markdown_text("") == ""
