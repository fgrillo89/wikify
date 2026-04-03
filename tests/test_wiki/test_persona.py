"""Tests for wiki/persona.py -- domain persona generation and caching."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import wikify.wiki.persona as persona_mod

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_paper(pid="abc123", title="Test Paper", doc_type="paper"):
    p = MagicMock()
    p.id = pid
    p.title = title
    p.doc_type = doc_type
    p.display_name.return_value = f"Author 2024 - {title}"
    return p


def _make_topic(paper_id="abc123", topic="material_science"):
    t = MagicMock()
    t.paper_id = paper_id
    t.topic = topic
    return t


def _make_persona_row(domain="material_science", text="You are a senior materials scientist."):
    row = MagicMock()
    row.domain = domain
    row.persona_text = text
    return row


_SENTINEL = object()


def _make_exec_result(items):
    """Wrap a list in a mock that supports .all() as SQLModel exec() results do."""
    r = MagicMock()
    r.all.return_value = items
    return r


def _make_session(exec_side_effects=None, get_return=_SENTINEL):
    """Build a context-manager-compatible mock session."""
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    if exec_side_effects is not None:
        session.exec.side_effect = [_make_exec_result(items) for items in exec_side_effects]
    if get_return is not _SENTINEL:
        session.get.return_value = get_return
    return session


# ── generate_domain_persona ───────────────────────────────────────────────────


def test_generate_domain_persona_calls_complete_with_domain_name():
    """generate_domain_persona() should call complete() with domain name in the prompt."""
    topic = _make_topic()
    paper = _make_paper()

    captured: list[list[dict]] = []

    def fake_complete(messages, model=None, temperature=0.3, max_tokens=400):
        captured.append(messages)
        return "You are a senior scientist."

    session1 = _make_session(exec_side_effects=[[topic]])
    session1.get.return_value = paper

    session2 = _make_session(get_return=None)

    call_count = [0]

    def patched_get_session():
        call_count[0] += 1
        return session1 if call_count[0] == 1 else session2

    with (
        patch.object(persona_mod, "get_session", side_effect=patched_get_session),
        patch.object(persona_mod, "complete", side_effect=fake_complete),
        patch.object(persona_mod, "DomainPersona"),
    ):
        result = persona_mod.generate_domain_persona("material_science")

    assert result == "You are a senior scientist."
    assert captured, "complete() must have been called"
    all_text = " ".join(m["content"] for m in captured[0])
    assert "material_science" in all_text


def test_generate_domain_persona_falls_back_to_all_papers_when_no_topic_match():
    """When no topics match the domain, fall back to all papers."""
    paper = _make_paper(title="Unrelated Paper")
    unrelated_topic = _make_topic(topic="quantum_computing")  # does not match "material_science"

    # First exec = PaperTopic list (no match); second exec = all papers fallback
    session1 = _make_session(exec_side_effects=[[unrelated_topic], [paper]])
    session2 = _make_session(get_return=None)

    call_count = [0]

    def patched_get_session():
        call_count[0] += 1
        return session1 if call_count[0] == 1 else session2

    with (
        patch.object(persona_mod, "get_session", side_effect=patched_get_session),
        patch.object(persona_mod, "complete", return_value="Persona text."),
        patch.object(persona_mod, "DomainPersona"),
    ):
        result = persona_mod.generate_domain_persona("material_science")

    assert result == "Persona text."


def test_generate_domain_persona_stores_result_in_db():
    """generate_domain_persona() must add a DomainPersona row to the session."""
    # Use a topic that matches "ald" so we take the matching-papers path (one exec call)
    topic = _make_topic(topic="ald_deposition")
    paper = _make_paper()

    session1 = _make_session(exec_side_effects=[[topic]])
    session1.get.return_value = paper

    session2 = _make_session(get_return=None)  # no existing row

    call_count = [0]

    def patched_get_session():
        call_count[0] += 1
        return session1 if call_count[0] == 1 else session2

    with (
        patch.object(persona_mod, "get_session", side_effect=patched_get_session),
        patch.object(persona_mod, "complete", return_value="Generated persona."),
        patch.object(persona_mod, "DomainPersona"),
    ):
        persona_mod.generate_domain_persona("ald")

    session2.add.assert_called_once()
    session2.commit.assert_called_once()


# ── get_or_create_persona ─────────────────────────────────────────────────────


def test_get_or_create_persona_returns_cached_value_if_row_exists():
    """get_or_create_persona() must return persona_text directly from DB if row found."""
    existing_row = _make_persona_row(text="Cached expert persona.")
    session = _make_session(get_return=existing_row)

    with (
        patch.object(persona_mod, "get_session", return_value=session),
        patch.object(persona_mod, "generate_domain_persona") as mock_gen,
    ):
        result = persona_mod.get_or_create_persona("material_science")

    assert result == "Cached expert persona."
    mock_gen.assert_not_called()


def test_get_or_create_persona_generates_when_missing():
    """get_or_create_persona() calls generate_domain_persona() when no DB row."""
    session = _make_session(get_return=None)

    with (
        patch.object(persona_mod, "get_session", return_value=session),
        patch.object(persona_mod, "generate_domain_persona", return_value="Fresh persona.") as mock_gen,
    ):
        result = persona_mod.get_or_create_persona("machine_learning")

    assert result == "Fresh persona."
    mock_gen.assert_called_once_with("machine_learning", model=None)


# ── invalidate_persona ────────────────────────────────────────────────────────


def test_invalidate_persona_removes_db_row():
    """invalidate_persona() should delete the DomainPersona row."""
    existing_row = _make_persona_row()
    session = _make_session(get_return=existing_row)

    with patch.object(persona_mod, "get_session", return_value=session):
        persona_mod.invalidate_persona("material_science")

    session.delete.assert_called_once_with(existing_row)
    session.commit.assert_called_once()


def test_invalidate_persona_is_noop_when_no_row():
    """invalidate_persona() should not crash when row is missing."""
    session = _make_session(get_return=None)

    with patch.object(persona_mod, "get_session", return_value=session):
        # Must not raise
        persona_mod.invalidate_persona("nonexistent_domain")

    session.delete.assert_not_called()
    session.commit.assert_not_called()
