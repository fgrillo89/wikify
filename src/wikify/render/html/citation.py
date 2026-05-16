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

# Generational suffixes that belong AFTER the initialized given names,
# e.g. "John Smith Jr." -> "Smith, J., Jr.". Matched case-insensitively
# against the token with any trailing period stripped.
_SUFFIX_TOKENS = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})

# Lowercase surname particles. When a name token sequence contains any
# of these, the surname runs from that particle to the end, so the
# author "Johannes van der Waals" formats as "van der Waals, J." not
# "Waals, J. V. D.". Treated case-insensitively but only triggered by
# a strictly-lowercase form, since "Van" capitalised is a given name
# in many naming conventions.
_PARTICLE_TOKENS = frozenset({
    "van", "von", "de", "del", "della", "der", "den", "das", "dos",
    "da", "di", "du", "la", "le", "el", "al", "bin", "ibn", "ter",
    "ten", "te",
})


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

    Handles four common author-name forms:

    * Comma-flipped: ``Chua, Leon`` -> ``Chua, L.``
    * Western: ``J. Joshua Yang`` -> ``Yang, J. J.``
    * Surname-then-initials (no comma): ``Kumar S`` -> ``Kumar, S.``
    * Particle surnames: ``Johannes van der Waals`` -> ``van der Waals, J.``

    Generational suffixes (``Jr``, ``Sr``, ``II`` ...) are stripped
    before surname selection and re-appended after the initials so
    ``John Smith Jr.`` -> ``Smith, J., Jr.``. Hyphenated given names
    yield hyphenated initials (``Sung-Hyun`` -> ``S.-H.``).

    Single-token mononyms and otherwise-unparseable strings round-trip
    unchanged.
    """
    raw = unicodedata.normalize("NFC", name).strip().rstrip(".")
    if not raw:
        return raw

    # Pre-flipped: "Chua, Leon" or "Smith, John Jr." -- surname is the
    # comma-prefix, suffix (if any) trails the given names.
    if "," in raw:
        surname, rest = (s.strip() for s in raw.split(",", 1))
        rest_tokens, suffix = _split_suffix(rest.split())
        initials = _make_initials(rest_tokens)
        body = f"{surname}, {initials}" if initials else surname
        return f"{body}, {suffix}" if suffix else body

    tokens = raw.split()
    if len(tokens) == 1:
        return tokens[0]

    # Strip generational suffix from the end before deciding the surname.
    tokens, suffix = _split_suffix(tokens)
    if not tokens:
        return suffix or raw

    # "Surname I." or "Surname I I." form: first token is multi-char, all
    # following are single letters. ``Kumar S`` -> ``Kumar, S.``.
    if (
        len(tokens) >= 2
        and len(tokens[0].rstrip(".")) >= 2
        and all(len(t.rstrip(".")) == 1 and t.rstrip(".").isalpha() for t in tokens[1:])
    ):
        body = f"{tokens[0]}, {_make_initials(tokens[1:])}"
        return f"{body}, {suffix}" if suffix else body

    # Particle surname: any token that is strictly lowercase and listed in
    # _PARTICLE_TOKENS starts the surname; everything before it is given.
    for i in range(1, len(tokens)):
        token = tokens[i]
        if token.islower() and token.rstrip(".").lower() in _PARTICLE_TOKENS:
            given = tokens[:i]
            surname = " ".join(tokens[i:])
            initials = _make_initials(given)
            body = f"{surname}, {initials}" if initials else surname
            return f"{body}, {suffix}" if suffix else body

    # Western default: last token is surname, everything else is given.
    surname = tokens[-1]
    initials = _make_initials(tokens[:-1])
    body = f"{surname}, {initials}" if initials else surname
    return f"{body}, {suffix}" if suffix else body


def _split_suffix(tokens: list[str]) -> tuple[list[str], str]:
    """If the last token is a generational suffix, peel it off.

    Returns ``(tokens_without_suffix, suffix_str)``. ``suffix_str`` is
    empty when no suffix was found and otherwise carries the suffix
    with a trailing period normalized (``Jr`` -> ``Jr.``).
    """
    if not tokens:
        return tokens, ""
    candidate = tokens[-1].rstrip(".").lower()
    if candidate in _SUFFIX_TOKENS:
        suffix = tokens[-1].rstrip(".")
        # Capitalize-first for word suffixes; uppercase for roman numerals.
        if candidate in {"ii", "iii", "iv", "v"}:
            suffix = suffix.upper()
        else:
            suffix = suffix.capitalize() + "."
        return tokens[:-1], suffix
    return tokens, ""


def _make_initials(parts: list[str]) -> str:
    """Reduce given-name tokens to space-separated initials.

    ``["J.", "Joshua"]`` -> ``"J. J."`` ; ``["Leon."]`` -> ``"L."`` ;
    ``["Sung", "Hyun"]`` -> ``"S. H."``.

    Hyphenated given names produce hyphenated initials so the
    semantic split survives: ``["Sung-Hyun"]`` -> ``"S.-H."``.
    """
    out: list[str] = []
    for token in parts:
        cleaned = token.strip(".")
        if not cleaned:
            continue
        if "-" in cleaned:
            sub_initials = [
                f"{piece[0].upper()}." for piece in cleaned.split("-") if piece
            ]
            out.append("-".join(sub_initials))
        else:
            out.append(f"{cleaned[0].upper()}.")
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
