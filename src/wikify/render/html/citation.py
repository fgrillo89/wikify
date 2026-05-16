"""Format a doc's bibliographic record as a Wikipedia CS1-style citation.

CS1 (Citation Style 1) is the default Wikipedia style for journal
articles. The rendered shape is::

    Last, F. M.; Last2, F. M. (Year). "Title". *Journal*. **Vol** (Issue):
    pages. doi:10.xxxx/yyyy.

Fields are dropped silently when the metadata is missing, so a doc
with only authors+year+title still produces a clean line. The title
is wrapped in a markdown link when a URL is provided so the rendered
HTML carries the same DOI/PDF hyperlink that the legacy author-date
formatter produced.
"""

from __future__ import annotations

import re
import unicodedata

_AUTHOR_CAP = 4  # show this many, then append "et al." for longer lists
_INITIAL_TOKEN_RE = re.compile(r"^[A-Z]\.?$")


def format_cs1(meta: dict, *, url: str = "") -> str:
    """Return a CS1-style citation in markdown.

    ``meta`` keys consulted (all optional): ``authors`` (list[str]),
    ``year``, ``title``, ``venue``, ``volume``, ``issue``, ``pages``,
    ``doi``. ``url``, when given, wraps the title text as a link.
    """
    authors = _format_authors(_as_list(meta.get("authors")))
    year = _stringify(meta.get("year"))
    title = _stringify(meta.get("title"))
    venue = _stringify(meta.get("venue"))
    volume = _stringify(meta.get("volume"))
    issue = _stringify(meta.get("issue"))
    pages = _normalize_pages(_stringify(meta.get("pages")))
    doi = _stringify(meta.get("doi"))

    parts: list[str] = []

    # Authors and year head: "Smith, J.; Doe, J. (2024). "
    if authors and year:
        parts.append(f"{authors} ({year}).")
    elif authors:
        parts.append(f"{authors}.")
    elif year:
        parts.append(f"({year}).")

    # Title: '"Foo"' or '"[Foo](url)"'.
    if title:
        title_text = _escape_brackets(title)
        title_md = f"[{title_text}]({url})" if url else title_text
        parts.append(f'"{title_md}".')

    # Venue (italicized): "*Nature*."
    if venue:
        parts.append(f"*{venue}*.")

    # Volume/issue/pages: "**453** (7191): 80–83."
    locator = _format_locator(volume, issue, pages)
    if locator:
        parts.append(locator)

    # DOI as its own linked identifier; skip when it's already the title URL.
    if doi and url != f"https://doi.org/{doi}":
        parts.append(f"[doi:{doi}](https://doi.org/{doi}).")

    return " ".join(parts).strip()


def _format_authors(authors: list[str]) -> str:
    """Format author list as ``Last, F. M.; Last2, F. M.`` with cap."""
    cleaned = [a.strip() for a in authors if a and a.strip()]
    if not cleaned:
        return ""
    if len(cleaned) > _AUTHOR_CAP:
        cleaned = cleaned[:_AUTHOR_CAP] + ["et al."]
    flipped: list[str] = []
    for name in cleaned:
        if name == "et al.":
            flipped.append(name)
        else:
            flipped.append(_to_surname_first(name))
    return "; ".join(flipped)


def _to_surname_first(name: str) -> str:
    """Convert ``Leon. Chua`` or ``J. Joshua Yang`` to ``Chua, L.`` or
    ``Yang, J. J.``.

    Already-flipped names like ``Chua, Leon`` are detected by the comma
    and rewritten with initials. Names that are unparseable round-trip
    unchanged so we don't mangle non-Western or single-token authors.
    """
    raw = unicodedata.normalize("NFC", name).strip().rstrip(".")
    if not raw:
        return raw

    # Pre-flipped: "Chua, Leon" -> surname is the comma-prefix.
    if "," in raw:
        surname, rest = (s.strip() for s in raw.split(",", 1))
        initials = _make_initials(rest.split())
        return f"{surname}, {initials}" if initials else surname

    tokens = raw.split()
    if len(tokens) == 1:
        return tokens[0]
    surname = tokens[-1]
    initials = _make_initials(tokens[:-1])
    return f"{surname}, {initials}" if initials else surname


def _make_initials(parts: list[str]) -> str:
    """Reduce given-name tokens to space-separated initials.

    ``["J.", "Joshua"]`` -> ``"J. J."`` ; ``["Leon."]`` -> ``"L."`` ;
    ``["Sung", "Hyun"]`` -> ``"S. H."``.
    """
    out: list[str] = []
    for token in parts:
        token = token.strip(".")
        if not token:
            continue
        if _INITIAL_TOKEN_RE.match(f"{token}."):
            out.append(f"{token[0].upper()}.")
        else:
            out.append(f"{token[0].upper()}.")
    return " ".join(out)


def _format_locator(volume: str, issue: str, pages: str) -> str:
    """Bold volume + optional (issue) + colon-prefixed pages, with a
    trailing period.
    """
    if not (volume or issue or pages):
        return ""
    head = ""
    if volume:
        head = f"**{volume}**"
        if issue:
            head += f" ({issue})"
    elif issue:
        head = f"({issue})"
    if pages:
        head = f"{head}: {pages}" if head else pages
    return f"{head}." if head else ""


def _normalize_pages(pages: str) -> str:
    """Render LaTeX-style ``80--83`` as an en-dash ``80–83``."""
    return pages.replace("--", "–")


def _stringify(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _escape_brackets(text: str) -> str:
    """Square brackets inside the link text break the markdown link
    parser. Replace them with parentheses so the title still reads.
    """
    return text.replace("[", "(").replace("]", ")")
