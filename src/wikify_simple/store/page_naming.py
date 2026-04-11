"""Canonical page id / filename / url-slug helpers.

Wikify pages are named the way a Wikipedia entry would be:

    "Atomic Layer Deposition" -> concepts/Atomic Layer Deposition.md
    "Leon Chua"               -> people/Leon Chua.md

The page id IS the title (with whitespace collapsed and filesystem-
reserved characters sanitized). URL slugs are produced only when we
emit HTML output.
"""

import re
import unicodedata

# Characters that are illegal in filenames on Windows (superset of posix).
_RESERVED_FS_CHARS = set('\x00/\\:*?"<>|')
_WS_RE = re.compile(r"\s+")
_URL_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")

# Windows reserved device names (case-insensitive).
_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def page_id_from_title(title: str) -> str:
    """Return the canonical page id for a title.

    - NFKC-normalise
    - collapse internal whitespace to single spaces
    - strip leading/trailing whitespace
    - replace filesystem-reserved characters with '_'
    - map Windows reserved device names to "<NAME>_"
    """
    if title is None:
        return ""
    s = unicodedata.normalize("NFKC", str(title))
    s = _WS_RE.sub(" ", s).strip()
    if not s:
        return ""
    s = "".join("_" if ch in _RESERVED_FS_CHARS else ch for ch in s)
    # Strip trailing dots/spaces (Windows filename constraint).
    s = s.rstrip(". ")
    if not s:
        return ""
    if s.upper() in _RESERVED_NAMES:
        s = s + "_"
    return s


def page_filename(page_id: str) -> str:
    """Return the on-disk filename for a page id."""
    return f"{page_id}.md"


def url_slug(page_id: str) -> str:
    """Return a URL-safe slug for an HTML href.

    Wikipedia-style: spaces -> underscores, everything else that is not
    URL-safe collapses to underscores.
    """
    s = unicodedata.normalize("NFKC", page_id).strip()
    s = s.replace(" ", "_")
    s = _URL_UNSAFE_RE.sub("_", s)
    return s.strip("_") or "page"
