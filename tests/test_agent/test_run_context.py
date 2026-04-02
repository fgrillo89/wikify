from __future__ import annotations

from scholarforge.agent.run_context import (
    add_run_warning,
    create_run_context,
    record_phase_usage,
)


def test_record_phase_usage_appends_phase_data():
    run_context = create_run_context(topic="edge AI", strategy="fast_generate")

    usage = record_phase_usage(
        "fast_write",
        duration_s=1.5,
        tokens_in=1200,
        tokens_out=800,
        metadata={"context_chars": 5000},
        run_context=run_context,
    )

    assert run_context.phase_usage == [usage]
    assert usage.name == "fast_write"
    assert usage.tokens_in == 1200
    assert usage.metadata["context_chars"] == 5000


def test_add_run_warning_records_warning():
    run_context = create_run_context()

    add_run_warning("fallback used", run_context=run_context)

    assert run_context.warnings == ["fallback used"]
