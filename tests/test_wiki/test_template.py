"""Tests for wiki/template.py -- Extraction template management."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import wikify.wiki.template as mod

# ── get_default_template ────────────────────────────────────────────────────


def test_get_default_template_has_all_sections():
    """Default template includes all 5 extraction sections."""
    template = mod.get_default_template()
    assert "## concepts" in template
    assert "## parameters" in template
    assert "## mechanisms" in template
    assert "## relationships" in template
    assert "## gaps" in template


# ── load_template / save_template ───────────────────────────────────────────


def test_load_template_default(tmp_path):
    """Falls back to default when no template file exists."""
    template = mod.load_template(tmp_path)
    assert "## concepts" in template


def test_save_and_load_template(tmp_path):
    """save_template writes, load_template reads back."""
    content = "# Custom Template\n## test_section\nHello"
    mod.save_template(tmp_path, content, epoch=1)

    loaded = mod.load_template(tmp_path)
    assert loaded == content

    # Check versioned backup dir exists
    versions_dir = tmp_path / "_template_versions"
    assert versions_dir.exists()


def test_save_template_creates_backup(tmp_path):
    """Second save creates a versioned backup of the first."""
    mod.save_template(tmp_path, "v1 content", epoch=1)
    mod.save_template(tmp_path, "v2 content", epoch=2)

    backup = tmp_path / "_template_versions" / "template_epoch_2.md"
    assert backup.exists()
    assert backup.read_text() == "v1 content"

    loaded = mod.load_template(tmp_path)
    assert loaded == "v2 content"


# ── build_extraction_prompt ─────────────────────────────────────────────────


def test_build_extraction_prompt_includes_template():
    """Prompt messages include the template content."""
    template = "## concepts\nExtract concepts"
    messages = mod.build_extraction_prompt(template, "some text", [])
    assert len(messages) == 1
    assert "## concepts" in messages[0]["content"]
    assert "some text" in messages[0]["content"]


def test_build_extraction_prompt_includes_prior():
    """Prior concepts are included in the prompt."""
    messages = mod.build_extraction_prompt("template", "text", ["ALD", "CVD"])
    assert "ALD, CVD" in messages[0]["content"]


# ── _count_template_sections ────────────────────────────────────────────────


def test_count_template_sections():
    """Counts ## headings correctly."""
    template = "# Title\n## one\n## two\n## three\n"
    assert mod._count_template_sections(template) == 3


# ── refine_template ─────────────────────────────────────────────────────────


def _mock_session(exec_returns):
    """Build a context-manager mock session with sequential exec returns."""
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)

    results = []
    for ret in exec_returns:
        r = MagicMock()
        r.all.return_value = ret
        results.append(r)

    session.exec.side_effect = results
    return session


def test_refine_template_no_gaps(tmp_path):
    """Returns unchanged template when no gaps exist."""
    mod.save_template(tmp_path, mod.get_default_template(), epoch=0)

    session = _mock_session([[]])  # gaps query returns empty

    with patch("wikify.core.store.db.get_session", return_value=session):
        _, delta = mod.refine_template(tmp_path, epoch=3)

    assert delta == 0.0


def test_refine_template_accepts_proposal(tmp_path):
    """Accepts a proposal when 5+ gaps and 3+ test hits."""
    mod.save_template(tmp_path, mod.get_default_template(), epoch=0)

    # Create 6 mock gaps with same suggested_type
    mock_gaps = []
    for i in range(6):
        g = MagicMock()
        g.description = f"Gap description {i}"
        g.suggested_type = "process_parameter"
        g.paper_id = "p1"
        g.chunk_id = f"c{i}"
        g.epoch = 2
        mock_gaps.append(g)

    # Mock chunks for testing
    mock_chunks = []
    for i in range(5):
        c = MagicMock()
        c.content = f"Chunk {i} with process parameters"
        mock_chunks.append(c)

    session = _mock_session([mock_gaps, mock_chunks])

    # LLM: 1 proposal + 5 test calls (4 YES, 1 NO)
    complete_responses = [
        "## process_parameters\nArray: {name, value}\n- Extract",
        "YES",
        "YES",
        "YES",
        "YES",
        "NO",
    ]

    with (
        patch("wikify.core.store.db.get_session", return_value=session),
        patch("wikify.core.llm.client.complete", side_effect=complete_responses),
    ):
        new_template, delta = mod.refine_template(tmp_path, epoch=3)

    assert "## process_parameters" in new_template
    assert delta > 0.0


def test_refine_template_rejects_low_hit_proposal(tmp_path):
    """Rejects a proposal when fewer than 3 test hits."""
    mod.save_template(tmp_path, mod.get_default_template(), epoch=0)

    mock_gaps = []
    for i in range(6):
        g = MagicMock()
        g.description = f"Rare gap {i}"
        g.suggested_type = "rare_type"
        g.paper_id = "p1"
        g.chunk_id = f"c{i}"
        g.epoch = 2
        mock_gaps.append(g)

    mock_chunks = []
    for i in range(5):
        c = MagicMock()
        c.content = f"Unrelated chunk {i}"
        mock_chunks.append(c)

    session = _mock_session([mock_gaps, mock_chunks])

    # LLM: 1 proposal + 5 NO test calls
    complete_responses = [
        "## rare_section\nArray: {field}\n- Something rare",
        "NO",
        "NO",
        "NO",
        "NO",
        "NO",
    ]

    with (
        patch("wikify.core.store.db.get_session", return_value=session),
        patch("wikify.core.llm.client.complete", side_effect=complete_responses),
    ):
        new_template, delta = mod.refine_template(tmp_path, epoch=3)

    assert "## rare_section" not in new_template
    assert delta == 0.0


def test_refine_template_overfitting_guard_rejects(tmp_path):
    """Overfitting guard rejects corpus-specific proposals."""
    mod.save_template(tmp_path, mod.get_default_template(), epoch=0)

    mock_gaps = []
    for i in range(6):
        g = MagicMock()
        g.description = f"Gap {i}"
        g.suggested_type = "specific_type"
        g.paper_id = "p1"
        g.chunk_id = f"c{i}"
        g.epoch = 2
        mock_gaps.append(g)

    mock_chunks = []
    for i in range(5):
        c = MagicMock()
        c.content = f"Chunk {i} with specific data"
        mock_chunks.append(c)

    session = _mock_session([mock_gaps, mock_chunks])

    # LLM: 1 proposal + 5 YES hits + 1 NO from overfitting guard
    complete_responses = [
        "## corpus_specific\nArray: {field}\n- Very specific",
        "YES",
        "YES",
        "YES",
        "YES",
        "YES",
        "NO",  # overfitting guard says NO
    ]

    with (
        patch("wikify.core.store.db.get_session", return_value=session),
        patch("wikify.core.llm.client.complete", side_effect=complete_responses),
    ):
        new_template, delta = mod.refine_template(tmp_path, epoch=3)

    assert "## corpus_specific" not in new_template
    assert delta == 0.0


# ── _prune_zero_yield_sections ──────────────────────────────────────────────


def test_prune_preserves_default_sections():
    """Default sections are never pruned."""
    template = mod.get_default_template()
    pruned, count = mod._prune_zero_yield_sections(template, current_epoch=5)
    assert count == 0
    assert "## concepts" in pruned
    assert "## parameters" in pruned


def test_prune_removes_custom_sections():
    """Custom sections added after defaults get pruned after lookback."""
    template = mod.get_default_template() + "\n## custom_section\nSome content\n"
    pruned, count = mod._prune_zero_yield_sections(template, current_epoch=5)
    assert count == 1
    assert "## custom_section" not in pruned
    assert "## concepts" in pruned


def test_prune_skips_early_epochs():
    """No pruning in early epochs (epoch <= lookback)."""
    template = mod.get_default_template() + "\n## custom_section\nSome content\n"
    pruned, count = mod._prune_zero_yield_sections(template, current_epoch=2)
    assert count == 0
    assert "## custom_section" in pruned
