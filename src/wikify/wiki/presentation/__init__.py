"""Wiki presentation subsystem.

Owns wiki HTML rendering, the dashboard, the visible-page layout
helpers, and HTML/Jinja templates. Kept local to the wiki package so
presentation lives next to the page contracts it consumes.
"""

from wikify.wiki.presentation.layout import (
    iter_visible_page_files,
    normalize_page_type,
)

__all__ = ["iter_visible_page_files", "normalize_page_type"]
