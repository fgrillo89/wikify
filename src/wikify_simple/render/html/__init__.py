"""HTML renderer for wikify_simple bundles.

Renders the two page kinds wikify_simple actually emits (concepts and
people). Public entry point: ``build_site``.
"""

from .render import build_site

__all__ = ["build_site"]
