"""Smoke tests for the wikify HTML renderer.

These tests synthesise a tiny bundle on disk and assert that the
rendered HTML resolves wikilinks, stages inline figures into
``<out>/assets/``, copies the CSS into ``<out>/static/``, and renders
``[^eN]`` evidence markers as proper footnote anchors.
"""

import io
import sys
from pathlib import Path

from wikify.paths import BundlePaths
from wikify.render.html import build_site

# 1x1 PNG (smallest valid PNG)
_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

_PHOTOCAT_BODY = """\
---
id: Photocatalysis
kind: article
title: Photocatalysis
aliases: [photo-catalysis]
links: []
---

# Photocatalysis

## Definition

Photocatalysis is the acceleration of a chemical reaction by light.

## Mechanism / Process

Photocatalysis proceeds through electron-hole pair generation under
illumination[^e1].

As shown in Figure 1, the band-gap alignment drives the reaction[^e1].
![Figure 1](images/doc1/fig1.png)

Charge carriers migrate to the surface and drive redox chemistry[^e1].
The mechanism is supported by [[Atomic Layer Deposition]] coatings[^e1].

## Key Facts

- Photocatalysis is widely used in water splitting[^e1].
- It depends on the band gap of the semiconductor[^e1].
- Quantum efficiency is the standard metric[^e1].

## In This Corpus

The corpus emphasises photocatalysis as a route to solar fuels[^e1]. It
is discussed in multiple primary sources[^e1].

## Relationships

| Related Concept            | Relation |
|----------------------------|----------|
| [[Atomic Layer Deposition]] | related |

## Open Questions

The corpus does not address industrial-scale deployment.

## Evidence

[^e1]: chunk_abc (doc1) > "Photocatalysis refers to ..."
"""

_ALD_BODY = """\
---
id: Atomic Layer Deposition
kind: article
title: Atomic Layer Deposition
aliases: [ALD]
links: []
---

# Atomic Layer Deposition

## Definition

ALD is a self-limiting vapor-phase thin-film growth technique.

## Mechanism / Process

ALD proceeds through pulsed half-reactions[^e1].
Each pulse saturates the surface[^e1].
The cycle repeats to grow films[^e1].

## Key Facts

- ALD produces conformal films[^e1].
- Process temperatures are mild[^e1].
- Growth is sub-monolayer per cycle[^e1].

## In This Corpus

The corpus discusses ALD as a fabrication step[^e1]. Multiple sources
cite its conformality[^e1].

## Relationships

| Related Concept | Relation |
|-----------------|----------|

## Open Questions

The corpus does not address scale-up.

## Evidence

[^e1]: chunk_xyz (doc2) > "ALD self-limiting"
"""

_PERSON_BODY = """\
---
id: Akira Honda
kind: person
title: Akira Honda
aliases: []
links: []
---

# Akira Honda

## Definition

Akira Honda is a researcher cited in the corpus.

## Mechanism / Process

Honda contributed to early photocatalysis work[^e1].
He published with Fujishima[^e1].
The collaboration shaped the field[^e1].

## Key Facts

- Honda co-authored the 1972 Nature paper[^e1].
- He worked at the University of Tokyo[^e1].
- His work seeded modern photocatalysis[^e1].

## In This Corpus

The corpus includes one reference to Honda[^e1]. He is cited briefly[^e1].

## Relationships

| Related Concept | Relation |
|-----------------|----------|

## Open Questions

The corpus does not detail later work.

## Evidence

[^e1]: chunk_h (doc3) > "Honda and Fujishima reported ..."
"""


def _make_corpus_with_image(root: Path) -> Path:
    img = root / "images" / "doc1" / "fig1.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(_PNG_BYTES)
    return root


def _make_bundle(root: Path) -> BundlePaths:
    bundle = BundlePaths(root=root)
    bundle.ensure()
    (bundle.articles_dir / "Photocatalysis.md").write_text(_PHOTOCAT_BODY, encoding="utf-8")
    (bundle.articles_dir / "Atomic Layer Deposition.md").write_text(_ALD_BODY, encoding="utf-8")
    (bundle.people_dir / "Akira Honda.md").write_text(_PERSON_BODY, encoding="utf-8")
    return bundle


def test_build_site_renders_index_and_pages(tmp_path: Path) -> None:
    corpus = _make_corpus_with_image(tmp_path / "corpus")
    bundle = _make_bundle(tmp_path / "bundle")
    out = tmp_path / "_html"
    build_site(bundle, out, corpus_root=corpus)

    index = out / "index.html"
    assert index.exists()
    index_html = index.read_text(encoding="utf-8")
    assert "Photocatalysis" in index_html
    assert "Atomic Layer Deposition" in index_html
    assert "Akira Honda" in index_html

    page = out / "articles" / "Photocatalysis.html"
    assert page.exists()
    rendered = page.read_text(encoding="utf-8")
    assert "<h1>Photocatalysis</h1>" in rendered
    # Six-section layout renders as h2 elements.
    assert "Definition</h2>" in rendered
    assert "Mechanism" in rendered
    assert "Open Questions</h2>" in rendered


def test_build_site_stages_inline_figure(tmp_path: Path) -> None:
    corpus = _make_corpus_with_image(tmp_path / "corpus")
    bundle = _make_bundle(tmp_path / "bundle")
    out = tmp_path / "_html"
    build_site(bundle, out, corpus_root=corpus)

    page = out / "articles" / "Photocatalysis.html"
    rendered = page.read_text(encoding="utf-8")
    assert "<img" in rendered
    assert "fig1.png" in rendered

    staged = out / "assets" / "images" / "doc1" / "fig1.png"
    assert staged.exists(), "figure not staged into out/assets"


def test_build_site_resolves_wikilinks(tmp_path: Path) -> None:
    corpus = _make_corpus_with_image(tmp_path / "corpus")
    bundle = _make_bundle(tmp_path / "bundle")
    out = tmp_path / "_html"
    build_site(bundle, out, corpus_root=corpus)

    page = out / "articles" / "Photocatalysis.html"
    rendered = page.read_text(encoding="utf-8")
    # The [[Atomic Layer Deposition]] wikilink should resolve to a real
    # <a href> pointing at the ALD page.
    assert 'href="../articles/Atomic_Layer_Deposition.html"' in rendered
    # No literal [[...]] should remain in the rendered HTML.
    assert "[[Atomic Layer Deposition]]" not in rendered


def test_build_site_renders_evidence_footnotes(tmp_path: Path) -> None:
    corpus = _make_corpus_with_image(tmp_path / "corpus")
    bundle = _make_bundle(tmp_path / "bundle")
    out = tmp_path / "_html"
    build_site(bundle, out, corpus_root=corpus)

    page = out / "articles" / "Photocatalysis.html"
    rendered = page.read_text(encoding="utf-8")
    # python-markdown's footnotes extension emits a <div class="footnote">
    # block plus footnote-ref anchors in the prose.
    assert "footnote" in rendered.lower()
    assert "fnref" in rendered or "footnote-ref" in rendered


def test_build_site_copies_css(tmp_path: Path) -> None:
    corpus = _make_corpus_with_image(tmp_path / "corpus")
    bundle = _make_bundle(tmp_path / "bundle")
    out = tmp_path / "_html"
    build_site(bundle, out, corpus_root=corpus)

    css = out / "static" / "wiki.css"
    assert css.exists()
    page = out / "articles" / "Photocatalysis.html"
    rendered = page.read_text(encoding="utf-8")
    assert "static/wiki.css" in rendered


def test_build_site_skips_skeleton_pages(tmp_path: Path) -> None:
    """build_site must omit skeleton pages and log the count to stderr."""
    bundle = BundlePaths(root=tmp_path / "bundle")
    bundle.ensure()

    # Two pages with real bodies (well above 200 chars).
    (bundle.articles_dir / "Photocatalysis.md").write_text(_PHOTOCAT_BODY, encoding="utf-8")
    (bundle.articles_dir / "Atomic Layer Deposition.md").write_text(_ALD_BODY, encoding="utf-8")

    # One skeleton page with an empty body.
    skeleton_body = (
        "---\nid: Stub Concept\nkind: concept\ntitle: Stub Concept\naliases: []\nlinks: []\n---\n"
    )
    (bundle.articles_dir / "Stub Concept.md").write_text(skeleton_body, encoding="utf-8")

    out = tmp_path / "_html"
    stderr_capture = io.StringIO()
    old_stderr = sys.stderr
    sys.stderr = stderr_capture
    try:
        build_site(bundle, out)
    finally:
        sys.stderr = old_stderr

    # Only the 2 real pages should produce HTML output files.
    html_files = list((out / "articles").glob("*.html"))
    assert len(html_files) == 2, f"expected 2 HTML files, got {[f.name for f in html_files]}"
    page_names = {f.stem for f in html_files}
    assert "Photocatalysis" in page_names
    assert "Atomic_Layer_Deposition" in page_names

    # The stderr log must mention skipping 1 skeleton.
    log = stderr_capture.getvalue()
    assert "skipped 1 skeleton" in log
