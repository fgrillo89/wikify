"""Smoke test for the mkdocs-material renderer."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from wikify_simple.paths import BundlePaths


def _mkdocs_available() -> bool:
    try:
        subprocess.run(
            ["mkdocs", "--version"],
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    return True


pytestmark = pytest.mark.skipif(not _mkdocs_available(), reason="mkdocs executable not available")


_PAGE_BODY = """\
---
id: concept-photocatalysis
kind: concept
title: Photocatalysis
aliases: [photo-catalysis]
links: []
---

# Photocatalysis

Photocatalysis accelerates reactions under illumination[^e1].

As shown in Figure 1, the band-gap alignment drives the reaction[^e1].
![Figure 1](images/doc1/fig1.png)

## Evidence

[^e1]: chunk_abc (doc1) > "Photocatalysis refers to ..."
"""


def _make_corpus(root: Path) -> Path:
    """Create a stub corpus with one image file at the expected path."""
    img = root / "images" / "doc1" / "fig1.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    # 1x1 PNG (smallest valid PNG)
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    img.write_bytes(png)
    return root


def _make_bundle(root: Path) -> BundlePaths:
    bundle = BundlePaths(root=root)
    bundle.ensure()
    (bundle.concepts_dir / "concept-photocatalysis.md").write_text(_PAGE_BODY, encoding="utf-8")
    return bundle


def test_build_site_renders_figure(tmp_path: Path) -> None:
    from wikify_simple.render.mkdocs import build_site

    corpus_root = _make_corpus(tmp_path / "corpus")
    bundle = _make_bundle(tmp_path / "bundle")
    out_dir = tmp_path / "_html"

    build_site(bundle, out_dir, corpus_root=corpus_root)

    index_html = out_dir / "index.html"
    assert index_html.exists(), "mkdocs build did not produce index.html"

    page_html = out_dir / "concepts" / "concept-photocatalysis" / "index.html"
    assert page_html.exists(), "concept page not rendered"
    rendered = page_html.read_text(encoding="utf-8")
    assert "Photocatalysis" in rendered
    assert "<img" in rendered
    assert "fig1.png" in rendered

    staged_img = out_dir / "concepts" / "images" / "doc1" / "fig1.png"
    assert staged_img.exists(), "figure not staged into site output"

    shutil.rmtree(out_dir, ignore_errors=True)
