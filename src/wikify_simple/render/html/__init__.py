"""HTML renderer for wikify_simple bundles.

Direct port of the legacy ``src/wikify/wiki/presentation/html.py``
renderer, trimmed to the two page kinds wikify_simple actually
emits (concepts and people). Public entry point: ``build_site``.
"""

from .render import build_site

__all__ = ["build_site"]
