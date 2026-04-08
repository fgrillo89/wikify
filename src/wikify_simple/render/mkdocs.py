"""Render a wikify_simple bundle to a static HTML site via mkdocs-material.

This is the near-zero-code path: stage the bundle's markdown pages into a
temporary ``docs/`` directory, copy the referenced figures next to them,
emit a minimal ``mkdocs.yml`` that points the Material theme at the
staged docs, and shell out to ``mkdocs build``.

``mkdocs`` is a dev dependency (invoked as a subprocess). This module
does not import it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..paths import BundlePaths
from ..store.wiki_index import WikiIndex

_FIGURE_REF_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")

_MKDOCS_YML_TEMPLATE = """\
site_name: {site_name}
docs_dir: docs
theme:
  name: material
  features:
    - navigation.sections
    - navigation.indexes
    - content.code.copy
    - search.suggest
markdown_extensions:
  - footnotes
  - tables
  - attr_list
  - def_list
  - pymdownx.superfences
  - pymdownx.arithmatex
"""


def _ensure_index_md(bundle: BundlePaths) -> Path:
    """Return path to ``_index.md``, rebuilding the index if necessary."""
    idx = bundle.root / "_index.md"
    if not idx.exists():
        WikiIndex.load(bundle).save()
    return idx


def _iter_page_files(bundle: BundlePaths) -> list[tuple[str, Path]]:
    """Return list of (sub, path) for all concept and people pages."""
    out: list[tuple[str, Path]] = []
    for sub in ("concepts", "people"):
        d = bundle.root / sub
        if not d.exists():
            continue
        for f in sorted(d.glob("*.md")):
            out.append((sub, f))
    return out


def _stage_page(src: Path, dest: Path) -> str:
    """Copy a page file to ``dest`` and return its body text."""
    text = src.read_text(encoding="utf-8")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return text


def _extract_figure_paths(body: str) -> list[str]:
    """Return all raw path strings from ``![..](path)`` refs in the body."""
    return [m.group(1).strip() for m in _FIGURE_REF_RE.finditer(body)]


def _copy_figure(
    rel_path: str,
    corpus_root: Path | None,
    staged_page_dir: Path,
    src_page_dir: Path,
) -> None:
    """Copy a figure referenced by a staged page into the docs tree.

    ``rel_path`` is whatever appeared in the page's markdown link. The
    image path is typically corpus-relative (e.g. ``images/<slug>/f.png``).
    We resolve it against the corpus root when one is supplied; otherwise
    we look for it next to the source page. The file is staged under
    ``docs_dir`` at the SAME relative path the markdown link uses, so the
    link resolves unchanged in the rendered HTML.
    """
    # Absolute paths and URLs are left to the browser.
    if rel_path.startswith(("http://", "https://", "/")):
        return
    dest = staged_page_dir / rel_path
    if dest.exists():
        return
    candidates: list[Path] = []
    if corpus_root is not None:
        candidates.append(corpus_root / rel_path)
    candidates.append(src_page_dir / rel_path)
    for src in candidates:
        if src.exists() and src.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            return
    # Missing image is not fatal: mkdocs will render a broken img tag,
    # which is the correct signal that the figure is missing on disk.


def _stage_docs(bundle: BundlePaths, docs_dir: Path, corpus_root: Path | None) -> None:
    """Stage the bundle's pages and figures into ``docs_dir``."""
    docs_dir.mkdir(parents=True, exist_ok=True)
    index_src = _ensure_index_md(bundle)
    shutil.copy2(index_src, docs_dir / "index.md")
    for sub, page_src in _iter_page_files(bundle):
        dest = docs_dir / sub / page_src.name
        body = _stage_page(page_src, dest)
        for rel in _extract_figure_paths(body):
            _copy_figure(rel, corpus_root, dest.parent, page_src.parent)


def build_site(
    bundle: BundlePaths,
    out_dir: Path,
    *,
    corpus_root: Path | None = None,
) -> Path:
    """Render ``bundle`` to a static HTML site under ``out_dir``.

    Stages the bundle into a temporary ``docs/`` directory, writes a
    minimal ``mkdocs.yml`` with the Material theme, and shells out to
    ``mkdocs build``. Returns ``out_dir``.

    Raises ``FileNotFoundError`` if the ``mkdocs`` executable is not
    available on ``PATH``.
    """
    out_dir = Path(out_dir).resolve()
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wikify-mkdocs-") as tmp:
        tmp_root = Path(tmp)
        docs_dir = tmp_root / "docs"
        _stage_docs(bundle, docs_dir, corpus_root)
        config_path = tmp_root / "mkdocs.yml"
        config_path.write_text(
            _MKDOCS_YML_TEMPLATE.format(site_name=bundle.root.name),
            encoding="utf-8",
        )
        cmd = [
            "mkdocs",
            "build",
            "--config-file",
            str(config_path),
            "--site-dir",
            str(out_dir),
            "--clean",
        ]
        subprocess.run(cmd, check=True)
    return out_dir
