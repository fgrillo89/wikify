"""HTML renderer for wiki bundles.

Renders the two page kinds wikify actually emits (concepts and
people). Public entry point: ``build_site``.
"""

from .render import build_site

__all__ = ["build_site"]
