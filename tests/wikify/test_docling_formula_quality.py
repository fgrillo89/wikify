"""Tests for Granite-Docling formula quality gate + construction helpers.

GPU-free: the construction helpers are tested without instantiating
``DocumentConverter``; the quality gate is pure-Python over already-
extracted records, so the whole suite runs on CI without CUDA or
the Docling models.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from wikify.ingest.parsers import docling
from wikify.ingest.parsers.docling import (
    DoclingOptions,
    FormulaContaminationError,
    _assert_formula_quality,
    _extract_docling_formulas,
    _find_leak_sentinels,
    _formula_item_to_dict,
    _longest_repeated_ngram_run,
    _make_code_formula_options,
    _make_pdf_pipeline_options,
)

# ---------------------------------------------------------------------------
# _longest_repeated_ngram_run
# ---------------------------------------------------------------------------


def test_repetition_run_counts_back_to_back_only() -> None:
    """Same 3-gram repeating adjacently must register as one streak."""
    text = "\\text{not} \\, s " * 50
    assert _longest_repeated_ngram_run(text, n=3) >= 40


def test_repetition_run_short_input_returns_zero() -> None:
    assert _longest_repeated_ngram_run("a b", n=3) == 0
    assert _longest_repeated_ngram_run("", n=3) == 0


def test_repetition_run_unique_tokens_returns_one() -> None:
    """Unique tokens never repeat — the longest streak is a single
    occurrence (1)."""
    text = "alpha beta gamma delta epsilon zeta eta theta"
    assert _longest_repeated_ngram_run(text, n=3) == 1


def test_repetition_run_legit_latex_stays_below_threshold() -> None:
    """Real dense math (subscripts, integration variables) must stay
    well below the >50 threshold. The Chua 1971 paper's variational
    derivations were observed to top out around x16 for any single
    3-gram even on the densest formulas; loops sit at 100+."""
    text = (
        r"\text{curl} \, H = J + \frac{\partial D}{\partial t} "
        r"\quad \text{(2)} \quad \text{Faraday law:} "
        r"\nabla \times E = -\frac{\partial B}{\partial t}"
    )
    assert _longest_repeated_ngram_run(text, n=3) <= 50


# ---------------------------------------------------------------------------
# _find_leak_sentinels
# ---------------------------------------------------------------------------


def test_leak_sentinels_detects_open_close_and_loc() -> None:
    leaks = _find_leak_sentinels("<formula><loc_247>x = 1</formula>")
    assert "<formula" in leaks
    assert "</formula" in leaks
    assert "<loc_" in leaks


def test_leak_sentinels_detects_truncated_close() -> None:
    """Granite-Docling sometimes emits ``</formula`` with no trailing
    ``>`` (the close tag and ``<end_of_utterance>`` decode as one
    contiguous sequence; upstream truncation chops the ``>``). The
    gate must catch this — a previous version keyed on the literal
    ``</formula>`` and let the truncated form through."""
    leaks = _find_leak_sentinels("x = 1</formula")
    assert "</formula" in leaks


def test_leak_sentinels_clean_returns_empty() -> None:
    assert _find_leak_sentinels("$$x = 1$$") == []
    assert _find_leak_sentinels("") == []


def test_leak_sentinels_self_attributed_open_tag() -> None:
    """``<formula attr=...>`` must still match the ``<formula`` prefix."""
    assert "<formula" in _find_leak_sentinels('<formula type="display">x</formula>')


# ---------------------------------------------------------------------------
# _assert_formula_quality
# ---------------------------------------------------------------------------


def _path(tmp_path: Path) -> Path:
    """A throwaway PDF path used in error messages."""
    p = tmp_path / "paper.pdf"
    p.write_bytes(b"%PDF-1.4\n")
    return p


def test_quality_gate_passes_on_clean_formulas(tmp_path: Path) -> None:
    formulas = [
        {"latex": r"x = 1", "type": "display"},
        {"latex": r"\nabla \times E = -\partial_t B", "type": "display"},
    ]
    md = "Some text. $$x = 1$$ More text."
    # Returns None; must not raise.
    assert _assert_formula_quality(formulas, md, _path(tmp_path)) is None


def test_quality_gate_fails_on_formula_wrapper_leak(tmp_path: Path) -> None:
    formulas = [
        {
            "latex": "<formula><loc_247><loc_0>( 3 ) \\quad \\text{for} ...",
            "type": "display",
        },
    ]
    md = "Clean text."  # markdown clean; defect is in FormulaItem.text
    with pytest.raises(FormulaContaminationError, match="leak tokens"):
        _assert_formula_quality(formulas, md, _path(tmp_path))


def test_quality_gate_fails_on_loc_token_only(tmp_path: Path) -> None:
    formulas = [{"latex": "<loc_247> x = 1", "type": "display"}]
    with pytest.raises(FormulaContaminationError, match="leak tokens"):
        _assert_formula_quality(formulas, "clean md", _path(tmp_path))


def test_quality_gate_fails_on_repetition_loop(tmp_path: Path) -> None:
    """Autoregressive degeneration: a 3-gram repeated >50x raises.
    Real loops sit at 100x to 2000x; the cutoff is calibrated so legit
    dense math (Chua-style variational derivations, x16 worst case)
    passes."""
    looped = ("\\text{not} \\, s " * 200).strip()
    formulas = [{"latex": looped, "type": "display"}]
    with pytest.raises(FormulaContaminationError, match="3-gram repetition"):
        _assert_formula_quality(formulas, "clean md", _path(tmp_path))


def test_quality_gate_passes_dense_subscript_math(tmp_path: Path) -> None:
    """Chua-style sums with dense subscripts (e.g. ``_{j=1}^{b}``)
    showed x16 for the most-repeated trigram in the worst legit case
    measured. Anything in that range must pass."""
    # Synthesize 30 repetitions of the same subscript trigram — well
    # above the dense-math empirical max of x16 but below the gate.
    block = " ".join(["A _ { j = 1 } ^ { b }"] * 30)
    formulas = [{"latex": block, "type": "display"}]
    assert _assert_formula_quality(
        formulas, "clean md", _path(tmp_path),
    ) is None


def test_quality_gate_fails_on_markdown_leak(tmp_path: Path) -> None:
    """Even when ``FormulaItem.text`` is clean, leaked tags in the
    EXPORTED markdown still fail the document — the visible HTML is
    the cheap signal that extraction is wrong."""
    md = (
        "Body text\n\n"
        "$$<formula><loc_247><loc_0>x = 1</formula>$$\n\n"
        "more body"
    )
    formulas: list[dict] = []
    with pytest.raises(FormulaContaminationError, match="markdown"):
        _assert_formula_quality(formulas, md, _path(tmp_path))


def test_quality_gate_long_clean_formula_passes(tmp_path: Path) -> None:
    """A long but legitimate formula must not be flagged just for length.

    The gate keys on leak sentinels + adjacent n-gram repetition; a
    Maxwell-system block with many unique terms hits neither.
    """
    big = " ".join(
        f"\\alpha_{{{i}}} = \\beta_{{{i}}} + \\gamma_{{{i}}}"
        for i in range(60)
    )
    formulas = [{"latex": big, "type": "display"}]
    assert _assert_formula_quality(formulas, "md", _path(tmp_path)) is None


def test_quality_gate_error_message_carries_examples(tmp_path: Path) -> None:
    """Examples + counts must appear in the raised error so
    ``failed_files.log`` is diagnostically useful."""
    formulas = [
        {"latex": "<formula><loc_1>x", "type": "display"},
        {"latex": "<formula><loc_2>y", "type": "display"},
    ]
    with pytest.raises(FormulaContaminationError) as exc_info:
        _assert_formula_quality(formulas, "md", _path(tmp_path))
    msg = str(exc_info.value)
    assert "2/2" in msg
    assert "examples" in msg


# ---------------------------------------------------------------------------
# _formula_item_to_dict / _extract_docling_formulas
# ---------------------------------------------------------------------------


def _fake_formula_item(text: str, page: int | None = 3, label: str = "") -> object:
    """Build a stub matching the ``FormulaItem`` attribute shape."""
    prov = [SimpleNamespace(page_no=page)] if page is not None else []
    return SimpleNamespace(text=text, prov=prov, label=label)


def test_formula_item_to_dict_preserves_page_and_label() -> None:
    item = _fake_formula_item("x = 1", page=7, label="(2)")
    record = _formula_item_to_dict(item)
    assert record is not None
    assert record["latex"] == "x = 1"
    assert record["page"] == 7
    assert record["label"] == "(2)"
    assert record["type"] == "display"


def test_formula_item_to_dict_skips_empty() -> None:
    assert _formula_item_to_dict(_fake_formula_item("")) is None
    assert _formula_item_to_dict(_fake_formula_item("   ")) is None


def test_extract_docling_formulas_filters_to_formula_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_extract_docling_formulas`` must select FormulaItem only and
    skip everything else from ``iterate_items()``."""
    try:
        from docling_core.types.doc.document import FormulaItem
    except ImportError:
        pytest.skip("docling_core not available")

    # Two real FormulaItems and one unrelated stub. The walker must
    # ignore the stub and return only the formulas.
    f1 = FormulaItem(
        self_ref="#/equations/0", orig="x = 1", text="x = 1",
        label="formula",
    )
    f2 = FormulaItem(
        self_ref="#/equations/1", orig="y = 2", text="y = 2",
        label="formula",
    )
    not_formula = SimpleNamespace(text="nope", prov=[], label="text")

    fake_doc = SimpleNamespace(
        iterate_items=lambda: iter(
            [(f1, 0), (not_formula, 0), (f2, 0)],
        ),
    )
    out = _extract_docling_formulas(fake_doc)
    assert [r["latex"] for r in out] == ["x = 1", "y = 2"]


# ---------------------------------------------------------------------------
# _make_code_formula_options / _make_pdf_pipeline_options
# ---------------------------------------------------------------------------


def test_make_code_formula_options_uses_from_preset() -> None:
    """The single preset path: ``CodeFormulaVlmOptions.from_preset(name)``."""
    try:
        from docling.datamodel.pipeline_options import CodeFormulaVlmOptions
    except ImportError:
        pytest.skip("docling without formula support")
    opts = DoclingOptions(formula_model="granite_docling")
    cfo = _make_code_formula_options(opts)
    assert cfo is not None
    assert isinstance(cfo, CodeFormulaVlmOptions)


def test_make_code_formula_options_no_overrides_method() -> None:
    """``CodeFormulaVlmOptions`` has no ``.with_overrides()`` — guard
    against future code reaching for a non-existent helper."""
    try:
        from docling.datamodel.pipeline_options import CodeFormulaVlmOptions
    except ImportError:
        pytest.skip("docling without formula support")
    cfo = CodeFormulaVlmOptions.from_preset("granite_docling")
    assert not hasattr(cfo, "with_overrides")


def test_make_code_formula_options_returns_none_without_docling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the running Docling has no ``CodeFormulaVlmOptions``,
    construction returns None instead of crashing the pipeline."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "docling.datamodel.pipeline_options":
            raise ImportError("simulated absent module")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert _make_code_formula_options(DoclingOptions()) is None


def test_make_pdf_pipeline_options_wires_formula_options() -> None:
    """When formulas are enabled, the pipeline carries
    ``code_formula_options``; when off, it does not."""
    try:
        from docling.datamodel.pipeline_options import (  # noqa: F401
            CodeFormulaVlmOptions,
            PdfPipelineOptions,
        )
    except ImportError:
        pytest.skip("docling without formula support")

    accel = docling._make_accelerator()
    on = _make_pdf_pipeline_options(accel, DoclingOptions(formulas=True))
    off = _make_pdf_pipeline_options(accel, DoclingOptions(formulas=False))
    assert on.do_formula_enrichment is True
    assert getattr(on, "code_formula_options", None) is not None
    assert off.do_formula_enrichment is False


# ---------------------------------------------------------------------------
# parse() integration: gate fires before persistence
# ---------------------------------------------------------------------------


def test_parse_raises_before_doc_cache_when_formulas_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: a converter that returns a dirty FormulaItem causes
    ``parse()`` to raise before ``doc.save_as_json`` runs.

    The cache path is monitored via a sentinel: if it was written, the
    test fails. This is the cheapest proof that quarantine fires before
    any artifact lands.
    """
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    cache_path = tmp_path / "cache" / "doc.json"

    saved: list[Path] = []

    class _DirtyFormula:
        text = "<formula><loc_247>( 3 ) for the field"
        prov: list = []
        label = "formula"

    class _FakeDoc:
        name = "paper"

        def export_to_markdown(self) -> str:
            return "body text"

        def iterate_items(self):
            yield (_DirtyFormula(), 0)

        def save_as_json(self, path) -> None:  # pragma: no cover - must NOT run
            saved.append(Path(path))

    class _FakeConvertResult:
        document = _FakeDoc()

    class _FakeConverter:
        def convert(self, _path: str) -> _FakeConvertResult:
            return _FakeConvertResult()

    # Stop the helper from probing for FormulaItem via isinstance — the
    # gate runs from the already-walked records, so the fake item only
    # needs to be discoverable by ``_doc_walk``. Patch ``_doc_walk`` to
    # return the dirty record directly, bypassing isinstance(FormulaItem).
    monkeypatch.setattr(
        docling, "_doc_walk",
        lambda doc, *, want_formulas: (
            0, [], (
                [_formula_item_to_dict(_DirtyFormula())]
                if want_formulas else []
            ),
        ),
    )
    monkeypatch.setattr(docling, "_get_converter", lambda opts: _FakeConverter())
    monkeypatch.setattr(docling, "_has_cuda", lambda: True)
    monkeypatch.setattr(docling, "_pdf_has_text_layer", lambda p: True)
    monkeypatch.setattr(docling, "_patch_hf_symlinks", lambda: None)
    monkeypatch.setattr(docling, "_configure_torch_runtime", lambda: None)
    monkeypatch.setattr(
        docling, "_disable_torch_compile_when_unsafe", lambda: None,
    )

    with pytest.raises(FormulaContaminationError):
        docling.parse(pdf, doc_cache_path=cache_path)

    assert saved == []
    assert not cache_path.exists()


def test_parse_raises_when_markdown_leak_is_html_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: ``export_to_markdown`` HTML-escapes ``<``/``>``/``&``,
    so a leaked ``<formula>`` arrives as ``&lt;formula&gt;`` in the raw
    export. The literal ``_LEAK_SENTINELS`` would miss it; the parser
    must unescape before the gate runs so the persisted markdown is
    inspected. Regression test for the escaped-sentinel bypass.
    """
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    cache_path = tmp_path / "cache" / "doc.json"

    saved: list[Path] = []

    class _FakeDoc:
        name = "paper"

        def export_to_markdown(self) -> str:
            # Exactly what Docling produces when a Granite-Docling leak
            # passes through ``export_to_markdown``: angle brackets are
            # entity-encoded.
            return "Body text\n\n&lt;formula&gt;&lt;loc_247&gt;x = 1&lt;/formula&gt;\n\nmore"

        def iterate_items(self):
            return iter([])

        def save_as_json(self, path) -> None:  # pragma: no cover - must NOT run
            saved.append(Path(path))

    class _FakeConvertResult:
        document = _FakeDoc()

    class _FakeConverter:
        def convert(self, _path: str) -> _FakeConvertResult:
            return _FakeConvertResult()

    # Walker reports zero formulas — the leak is markdown-only, which is
    # the exact shape escaped-sentinel bypass took in production.
    monkeypatch.setattr(
        docling, "_doc_walk",
        lambda doc, *, want_formulas: (0, [], []),
    )
    monkeypatch.setattr(docling, "_get_converter", lambda opts: _FakeConverter())
    monkeypatch.setattr(docling, "_has_cuda", lambda: True)
    monkeypatch.setattr(docling, "_pdf_has_text_layer", lambda p: True)
    monkeypatch.setattr(docling, "_patch_hf_symlinks", lambda: None)
    monkeypatch.setattr(docling, "_configure_torch_runtime", lambda: None)
    monkeypatch.setattr(
        docling, "_disable_torch_compile_when_unsafe", lambda: None,
    )

    with pytest.raises(FormulaContaminationError, match="markdown"):
        docling.parse(pdf, doc_cache_path=cache_path)

    assert saved == []
    assert not cache_path.exists()
