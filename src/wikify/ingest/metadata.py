"""Metadata extraction helpers for parsers (pdf/docx/pptx/html).

Stdlib imports only, no dataclasses returned to the outside. Helpers
cover title, authors, summary, year, DOI, venue, and a slide-aware
summary synthesiser.
"""

import re
from dataclasses import dataclass

# --- public surface ------------------------------------------------------


def first_heading(md_text: str) -> str | None:
    in_frontmatter = False
    for line in md_text.split("\n"):
        stripped = line.strip()
        if stripped == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            continue
        match = re.match(r"^#{1,6}\s+(?P<title>.+)$", stripped)
        if match:
            heading = match.group("title").strip()
            heading = clean_markdown(heading)
            heading = re.sub(r"[\ue000-\uf8ff]", "", heading)
            heading = re.sub(r"\s+", " ", heading).strip()
            if heading and not _is_heading_noise(heading):
                return heading
    return None


def parse_filename(filename: str) -> tuple[int | None, str | None, str | None]:
    """Parse a [YYYY Author] Title.ext filename. Returns (year, author, title)."""
    m = re.match(r"\[(\d{4})\s+([^\]]+)\]\s*(.+?)\.(?:pdf|docx|pptx)$", filename, re.IGNORECASE)
    if m:
        return int(m.group(1)), m.group(2).strip(), m.group(3).strip()
    m = re.match(r"\[(\d{4})\]\s*(.+?)\.(?:pdf|docx|pptx)$", filename, re.IGNORECASE)
    if m:
        return int(m.group(1)), None, m.group(2).strip()
    return None, None, None


def parse_authors(raw: str) -> list[str]:
    raw = raw.replace(";", ",").replace(" and ", ",")
    parts = [a.strip() for a in raw.split(",") if a.strip()]
    assembled: list[str] = []
    i = 0
    while i < len(parts):
        part = parts[i]
        if i + 1 < len(parts):
            nxt = parts[i + 1].strip()
            is_initials = bool(re.match(r"^[A-Z][.\s]*(?:[A-Z]\.?\s*)*$", nxt))
            is_first_name = bool(
                re.match(r"^[A-Z][a-z]{1,14}$", nxt)
                and len(part.split()) == 1
                and part[0:1].isupper()
            )
            if is_initials or is_first_name:
                assembled.append(f"{nxt} {part}")
                i += 2
                continue
        assembled.append(part)
        i += 1
    return [a for a in assembled if _is_valid_author(a)]


def extract_doi(text: str) -> str | None:
    m = re.search(r"(10\.\d{4,}/[^\s<>\]]+)", text)
    if m:
        doi = re.split(r"[?#&\]]", m.group(1), maxsplit=1)[0]
        return doi.rstrip(".,;)]}>")
    return None


def extract_document_doi(md_text: str) -> str | None:
    """Extract a document DOI while ignoring the references section."""
    return extract_doi(_pre_references_window(md_text))


def extract_year_from_pdf_meta(meta: dict) -> int | None:
    for key in ("creationDate", "modDate"):
        val = meta.get(key, "")
        m = re.search(r"((?:19|20)\d{2})", val)
        if m:
            return int(m.group(1))
    return None


def extract_summary(md_text: str) -> str | None:
    """Extract a document summary using slide-aware → labeled-section →
    first-prose-paragraph → first-400-words fallbacks.
    """
    slides = _parse_slides(md_text)
    if len(slides) >= 3:
        summary = _synthesize_slide_summary(slides)
        if summary and len(summary) > 50:
            return summary

    search_text = clean_markdown(md_text[:10000])

    label_re = re.compile(
        r"(?:^|\n)\s*(?:#+\s*)?"
        r"(?:abstract|summary|executive\s+summary|overview|scope|synopsis"
        r"|project\s+(?:summary|description)|purpose)"
        r"\s*[:\-—.]*\s*",
        re.IGNORECASE,
    )
    match = label_re.search(search_text)
    if match:
        after_label = search_text[match.end() :]
        end_re = re.compile(
            r"\n\s*(?:#+\s+|(?:keywords?|introduction|index\s+terms"
            r"|i\.\s+introduction|table\s+of\s+contents|background)\b)",
            re.IGNORECASE,
        )
        end_match = end_re.search(after_label)
        text = (after_label[: end_match.start()] if end_match else after_label[:3000]).strip()
        text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
        text = re.sub(r"\n{2,}", "\n\n", text)
        paragraphs = text.split("\n\n")
        text = paragraphs[0].strip()
        if len(text.split()) < 50 and len(paragraphs) > 1:
            for extra in paragraphs[1:]:
                extra = extra.strip()
                if _is_noise_paragraph(extra):
                    break
                text += " " + extra
                if len(text.split()) >= 50:
                    break
        if len(text) > 50 and not _is_noise_paragraph(text):
            return clean_markdown(text)

    paragraphs = re.split(r"\n\s*\n", search_text)
    for para in paragraphs:
        para = para.strip()
        if not para or para.startswith("#"):
            continue
        if _is_noise_paragraph(para):
            continue
        if len(para) > 100 and re.search(r"[.!?]", para):
            return clean_markdown(para)

    body_words: list[str] = []
    for para in paragraphs:
        para = para.strip()
        if not para or para.startswith("#") or _is_noise_paragraph(para) or len(para) < 10:
            continue
        body_words.extend(para.split())
        if len(body_words) >= 400:
            break
    if body_words:
        text = " ".join(body_words[:400])
        last_period = max(text.rfind(". "), text.rfind(".\n"), text.rfind("."))
        if last_period > 50:
            text = text[: last_period + 1]
        return clean_markdown(text)

    return None


def extract_venue(md_text: str) -> str | None:
    """Extract a likely journal / venue name from parser markdown.

    This is intentionally conservative: it only accepts structural publisher
    patterns that show up in paper front/back matter, and returns ``None`` for
    generic landing text such as "Contents lists available at ScienceDirect".
    """
    window = _pre_references_window(md_text)

    for line in window.splitlines():
        venue = _venue_from_homepage_line(line)
        if venue:
            return venue

    for line in window.splitlines():
        venue = _venue_from_italic_citation(line)
        if venue:
            return venue

    for line in window.splitlines():
        venue = _venue_from_volume_line(line, require_heading=False)
        if venue:
            return venue

    # Some parser outputs leave the journal citation as a final heading after
    # article body text, e.g. "## Nature 453, 80-83 (2008)". Only accept this
    # whole-document scan for headings to avoid harvesting reference entries.
    for line in md_text.splitlines():
        venue = _venue_from_volume_line(line, require_heading=True)
        if venue:
            return venue

    return None


def extract_publication_fields(md_text: str) -> dict[str, str]:
    """Extract BibTeX-ready publication fields from parser markdown."""
    window = _pre_references_window(md_text)

    fields: dict[str, str] = {}

    for line in window.splitlines():
        line_fields = _publication_from_cite_this_line(line)
        if line_fields:
            fields.update(line_fields)
            return fields

    for line in window.splitlines():
        venue = _venue_from_published_by_line(line) or _venue_from_homepage_line(line)
        if venue:
            fields.setdefault("venue", venue)
            return fields

    for line in window.splitlines():
        line_fields = _publication_from_italic_citation(line)
        if line_fields:
            fields.update(line_fields)
            return fields

    for line in window.splitlines():
        line_fields = _publication_from_volume_line(line, require_heading=False)
        if line_fields:
            fields.update(line_fields)
            return fields

    for line in md_text.splitlines():
        line_fields = _publication_from_volume_line(line, require_heading=True)
        if line_fields:
            fields.update(line_fields)
            return fields
    return fields


def clean_markdown(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text.strip()


def is_garbled_title(title: str) -> bool:
    if re.search(r"\d+\.\.\d+", title):
        return True
    if re.match(r"^[a-z0-9_\-]{3,20}$", title, re.IGNORECASE):
        return True
    if re.match(r"^untitled$", title, re.IGNORECASE):
        return True
    if len(title) < 5 and not any(c.isalpha() for c in title):
        return True
    if re.match(r"^[a-z]{2,4}[_\-]", title) and re.search(r"\d{4}", title):
        return True
    return False


def extract_authors_from_markdown(md_text: str, fn_author: str | None = None) -> list[str]:
    """Find the paper's author list in the rendered markdown body.

    Many journal PDFs (AIP, IOP, APL, etc.) prepend a "landing page" with a
    recommendations block ("You may also like ...") whose own author lists
    appear *before* the real paper title. The first-heading heuristic is
    fooled by these. When the caller passes ``fn_author`` (surname parsed
    from the filename) we anchor on it: scan the first ~12k chars for any
    reasonable-length line containing that surname as a whole word, and
    return the first parse whose names include the surname.
    """
    window = md_text[:12000]
    lines = window.split("\n")

    # Strategy 1: filename-surname anchor. Most robust when the PDF has a
    # landing page that would otherwise fool a first-heading scanner.
    if fn_author:
        surname = _extract_surname(fn_author)
        if surname:
            best_names: list[str] = []
            surname_re = re.compile(rf"\b{re.escape(surname)}\b", re.IGNORECASE)
            for line in lines:
                s = line.strip()
                if not s or len(s) > 500:
                    continue
                if not surname_re.search(s):
                    continue
                if re.search(r"(?i)(correspondence|nanotechnology|j\. phys\.)", s):
                    continue
                names = _parse_author_line(_author_line_prefix(s))
                if names and any(surname.lower() in n.lower() for n in names):
                    if len(names) > len(best_names):
                        best_names = names
            if best_names:
                return best_names

    # Strategy 2: first-heading heuristic. Used for single-author papers or
    # when no fn_author hint is available. Unchanged from the original.
    title_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") and len(stripped.lstrip("# ")) > 5:
            title_idx = i
            break
    if title_idx < 0:
        return []
    candidates: list[str] = []
    for i in range(title_idx + 1, min(title_idx + 15, len(lines))):
        line = lines[i].strip()
        if not line:
            continue
        if re.match(r"(?i)^#*\s*\*?\*?(abstract|introduction|index\s+terms|keywords)", line):
            break
        candidates.append(_author_line_prefix(line))
    for line in candidates:
        names = _parse_author_line(line)
        if len(names) >= 2:
            return names
    return []


def _author_line_prefix(line: str) -> str:
    """Keep the author segment before affiliation/email prose starts."""
    return re.split(
        r"(?i)\b(?:department|institute|school|laboratory|centre|center|university"
        r"|college|faculty|e-mail|email)\b|@",
        line,
        maxsplit=1,
    )[0].strip(" ,;")


def _extract_surname(author_hint: str) -> str:
    """Get the surname from a filename author tag like 'Strukov' or 'Jie Ma'."""
    tokens = [t for t in re.split(r"\s+", author_hint.strip()) if t]
    if not tokens:
        return ""
    # Filename author tags are usually "Surname" or "First Last" — the last
    # token is conventionally the surname in both cases. Strip any trailing
    # punctuation.
    return tokens[-1].strip(" .,;:")


# --- internal ------------------------------------------------------------


def _pre_references_window(md_text: str) -> str:
    """Return early paper text, stopping before references when detectable."""
    window = md_text[:12000]
    ref_re = re.compile(
        r"(?im)^\s*(?:#+\s*)?(?:references|bibliography|works\s+cited)\b"
    )
    match = ref_re.search(window)
    if match:
        return window[: match.start()]
    return window


def _venue_from_homepage_line(line: str) -> str | None:
    cleaned = _strip_heading(line)
    match = re.search(r"(.+?)\s+journal\s+homepage\s*:", cleaned, re.IGNORECASE)
    if not match:
        return None
    return _clean_venue_candidate(match.group(1))


def _venue_from_published_by_line(line: str) -> str | None:
    cleaned = _plain_markdown_line(line)
    match = re.search(
        r"(?P<venue>[A-Z][A-Za-z0-9& .:'/\-]{2,120}?)\s+published\s+by\b",
        cleaned,
        re.IGNORECASE,
    )
    if not match:
        return None
    candidate = match.group("venue").split(".")[-1]
    return _clean_venue_candidate(candidate)


def _venue_from_italic_citation(line: str) -> str | None:
    fields = _publication_from_italic_citation(line)
    return fields.get("venue") if fields else None


def _publication_from_italic_citation(line: str) -> dict[str, str] | None:
    match = re.match(
        r"^\s*(?:#+\s*)?[_*]{1,2}(?P<venue>[^_*]{2,120})[_*]{1,2}"
        r"\s+(?P<volume>\d{1,4})\s*,\s*(?P<pages>[A-Za-z]?\d+[A-Za-z]?(?:[-\u2013]\d+)?)",
        line,
        re.IGNORECASE,
    )
    if not match:
        return None
    return _publication_fields_from_match(match)


def _publication_from_cite_this_line(line: str) -> dict[str, str] | None:
    cleaned = _plain_markdown_line(line)
    cleaned = re.sub(r"(?i)^cite\s+this:\s*", "", cleaned).strip()
    match = re.match(
        r"^(?P<venue>[A-Z][A-Za-z0-9& .:'/\-]{1,100}?)\s+"
        r"(?:19|20)\d{2}\s*,\s*"
        r"(?P<volume>\d{1,4})\s*,\s*"
        r"(?P<pages>[A-Za-z]?\d+[A-Za-z]?(?:[-\u2013]\d+)?)",
        cleaned,
    )
    if not match:
        return None
    return _publication_fields_from_match(match)


def _venue_from_volume_line(line: str, *, require_heading: bool) -> str | None:
    fields = _publication_from_volume_line(line, require_heading=require_heading)
    return fields.get("venue") if fields else None


def _publication_from_volume_line(
    line: str, *, require_heading: bool
) -> dict[str, str] | None:
    if require_heading and not re.match(r"^\s*#+\s+", line):
        return None
    cleaned = _plain_markdown_line(line)
    match = re.match(
        r"^(?P<venue>[A-Z][A-Za-z0-9& .:'/\-]{1,100}?)\s+"
        r"(?P<volume>\d{1,4})\s*,\s*(?P<pages>[A-Za-z]?\d+[A-Za-z]?(?:[-\u2013]\d+)?)"
        r"\s*\((?:19|20)\d{2}\)",
        cleaned,
    )
    if not match:
        return None
    return _publication_fields_from_match(match)


def _publication_fields_from_match(match: re.Match[str]) -> dict[str, str] | None:
    venue = _clean_venue_candidate(match.group("venue"))
    if not venue:
        return None
    fields = {"venue": venue}
    volume = (match.groupdict().get("volume") or "").strip()
    pages = (match.groupdict().get("pages") or "").strip().replace("\u2013", "-")
    if volume:
        fields["volume"] = volume
    if pages:
        fields["pages"] = pages
    return fields


def _strip_heading(line: str) -> str:
    return re.sub(r"^\s*#+\s*", "", line.strip())


def _plain_markdown_line(line: str) -> str:
    line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
    return clean_markdown(_strip_heading(line))


def _clean_venue_candidate(candidate: str) -> str | None:
    candidate = clean_markdown(candidate)
    candidate = re.sub(r"<[^>]+>", "", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip(" -:;,")
    candidate = re.sub(r"(?i)^(?:cite\s+as|citation)\s*:\s*", "", candidate).strip()
    candidate = re.sub(
        r"\s+journal\s+homepage\b.*$",
        "",
        candidate,
        flags=re.IGNORECASE,
    ).strip(" -:;,")
    if not _is_valid_venue(candidate):
        return None
    return candidate


def _is_valid_venue(candidate: str) -> bool:
    lower = candidate.lower()
    if len(candidate) < 2 or len(candidate) > 120:
        return False
    if not any(c.isalpha() for c in candidate):
        return False
    if re.search(r"https?://|www\.|@", lower):
        return False
    noise = (
        "contents lists available",
        "sciencedirect",
        "journal homepage",
        "articles you may be interested in",
        "article info",
        "abstract",
        "keywords",
        "introduction",
        "references",
        "bibliography",
        "doi",
        "citation",
        "downloaded",
        "copyright",
        "view export",
        "accepted manuscript",
    )
    if any(marker in lower for marker in noise):
        return False
    if len(candidate.split()) > 12:
        return False
    return True


def _is_heading_noise(heading: str) -> bool:
    lower = heading.casefold()
    if lower in {
        "articles you may be interested in",
        "letters",
        "paper",
        "articles",
        "review",
        "open access",
        "references",
        "bibliography",
        "works cited",
        "affiliations",
        "abstract",
        "article",
        "check for updates",
        "rapid communications",
        "topical review",
        "introduction",
        "original article",
        "research article",
        "communication",
        "full paper",
        "you may also like",
        "conflicts of interest",
        "acknowledgements",
        "acknowledgments",
        "supplementary information",
        "supporting information",
        "author information",
        "data availability",
    }:
        return True
    # Numbered section headers (e.g. "1. Introduction")
    if re.match(r"^\d+\.\s", heading):
        return True
    return False

_AUTHOR_NOISE = {
    "ieee",
    "member",
    "senior",
    "fellow",
    "student",
    "life",
    "associate",
    "et",
    "al",
    "and",
    "the",
    "of",
    "vol",
    "no",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "transactions",
    "journal",
    "proceedings",
    "letters",
}


def _is_valid_author(name: str) -> bool:
    name = name.strip()
    if not name or len(name) < 2:
        return False
    words = name.split()
    if len(words) == 1:
        if not any("\u4e00" <= c <= "\u9fff" or "\uac00" <= c <= "\ud7af" for c in name):
            return False
    if len(words) > 5:
        return False
    if not words[0][0:1].isupper():
        return False
    if re.search(r"[(\[|]|\d+\s*$", name):
        return False
    if all(w.lower() in _AUTHOR_NOISE for w in words):
        return False
    # Reject journal / venue names that slip through citation parsing.
    if _looks_like_journal(name):
        return False
    # Reject "et al." fragments that leak from citation parsing.
    if "et al" in name.lower():
        return False
    # Reject names containing ampersand (citation parsing artifact like
    # "B. & Alibart" from broken "Alibart, F. & Strukov, D. B." splits).
    if "&" in name:
        return False
    # Reject well-known place names that leak from affiliations.
    if _normalize_lower(name) in _PLACE_NAMES:
        return False
    # Reject names containing common non-name words (title/topic fragments).
    if _has_non_name_words(name):
        return False
    return True


# Words that never appear in a person's name. When any of these appear
# (case-insensitive) in a candidate "author" string, it's a citation
# parsing artifact (e.g. "J. Vector-matrix multiply" or "Information
# Technology").
_NON_NAME_WORDS = {
    "multiply",
    "learning",
    "training",
    "network",
    "circuit",
    "device",
    "memory",
    "synapse",
    "computing",
    "technology",
    "information",
    "system",
    "systems",
    "analysis",
    "design",
    "control",
    "model",
    "models",
    "method",
    "theory",
    "simulation",
    "process",
    "energy",
    "performance",
    "structure",
    "material",
    "materials",
    "effect",
    "effects",
    "properties",
    "application",
    "applications",
    "based",
    "using",
    "toward",
    "towards",
    "novel",
    "high",
    "low",
    "ultra",
    "nano",
    "micro",
    "oxide",
    "metal",
    "thin",
    "film",
    "layer",
    "switching",
    "resistive",
    "neuromorphic",
    "memristor",
    "memristive",
    "crossbar",
    "array",
    "integrated",
    "operation",
    "architecture",
    "reconfigurable",
    "analog",
    "digital",
    "vector",
    "matrix",
    "wiley",
    "springer",
    "elsevier",
    "taylor",
    "francis",
    "usa",
    "ieee",
    "acm",
}


_PLACE_NAMES = {
    "san francisco",
    "new york",
    "los angeles",
    "san jose",
    "san diego",
    "washington",
    "boston",
    "chicago",
    "seattle",
    "london",
    "berlin",
    "tokyo",
    "beijing",
    "shanghai",
    "usa wiley",
    "usa_ wiley",
}


def _normalize_lower(s: str) -> str:
    return re.sub(r"[_\s]+", " ", s.lower()).strip()


def _has_non_name_words(name: str) -> bool:
    """Return True if name contains words that indicate a title fragment, not a person."""
    words_lower = {w.lower().rstrip(".,;:-") for w in name.split()}
    return bool(words_lower & _NON_NAME_WORDS)


# Abbreviated journal tokens. A name containing 2+ of these is almost
# certainly a publication venue, not a person.
_JOURNAL_ABBREV_TOKENS = {
    "adv",
    "appl",
    "chem",
    "commun",
    "electron",
    "eng",
    "funct",
    "lett",
    "mater",
    "nanotechnol",
    "phys",
    "rev",
    "sci",
    "technol",
    "trans",
    "proc",
    "int",
    "conf",
    "symp",
}
_JOURNAL_FULL_WORDS = {
    "journal",
    "proceedings",
    "transactions",
    "letters",
    "review",
    "reviews",
    "annals",
    "bulletin",
    "reports",
    "communications",
    "magazine",
    "quarterly",
    "archives",
    "nano",
    "nature",
    "science",
    "cell",
}


def _looks_like_journal(name: str) -> bool:
    """Return True if ``name`` looks like an abbreviated or full journal title."""
    # Split into normalized tokens (strip trailing dots, commas, etc.)
    tokens = [w.lower().rstrip(".,;:") for w in name.split()]
    # Count abbreviated journal tokens (e.g. "Adv", "Funct", "Mater")
    abbrev_hits = sum(1 for t in tokens if t in _JOURNAL_ABBREV_TOKENS)
    # If 2+ abbreviated tokens match, it's a journal
    if abbrev_hits >= 2:
        return True
    # Full journal-word match
    token_set = set(tokens)
    journal_hits = token_set & _JOURNAL_FULL_WORDS
    if journal_hits:
        non_journal = token_set - _JOURNAL_FULL_WORDS - _JOURNAL_ABBREV_TOKENS
        # All words are journal-ish
        if not non_journal:
            return True
        # 2-word name where one is a journal word ("ACS Nano", "RSC Adv")
        if len(tokens) <= 2:
            return True
    # Single abbreviated token + short name (e.g. "RSC Adv." = 2 words)
    if abbrev_hits >= 1 and len(tokens) <= 2:
        # Check if the other token looks like an abbreviation too (all caps or short)
        non_abbrev = [t for t in tokens if t not in _JOURNAL_ABBREV_TOKENS]
        if all(len(t) <= 4 or t.upper() == t for t in non_abbrev):
            return True
    return False


def _parse_author_line(line: str) -> list[str]:
    cleaned = re.sub(r"^#+\s*", "", line)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", cleaned)
    # Strip leading bullet markers (-, *, +, •) that pymupdf4llm leaves on
    # recommendation lists like "- Zhao Jin-Wei, ...".
    cleaned = re.sub(r"^[\-*+•]\s+", "", cleaned)
    cleaned = re.sub(r"_+", " ", cleaned)
    cleaned = re.sub(
        r",?\s*(?:Life |Senior |Student |Associate )?(?:Fellow|Member),?\s*(?:IEEE)?,?",
        ",",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\[[^\]]*\]", "", cleaned)
    # Footnote/affiliation marker cluster like " *" or " †" or " ✉" right
    # after a name becomes a hard separator. Convert to comma BEFORE we
    # strip the markers — this is how we pry apart lines where pymupdf4llm
    # concatenated a title and an author list through a footnote star,
    # e.g. "... Memristor * Sungjun Kim, Hyungjin Kim ...".
    cleaned = re.sub(r"(?<=[A-Za-z\)\]])\s*[†‡§✉✱*]+", ",", cleaned)
    cleaned = re.sub(r"[†‡§✉✱*]+", "", cleaned)
    # Treat ampersand as an author separator ("A, B & C").
    cleaned = cleaned.replace("&", ",")
    # Strip trailing per-author affiliation superscripts like "H. Kim 1,2"
    # -> "H. Kim" and "M. R. Mahmoodi 1" -> "M. R. Mahmoodi".
    cleaned = re.sub(
        r",\s*\d+(?:\s*,\s*\d+)*(?:\s*,\s*[a-z])?\)?\s*",
        ", ",
        cleaned,
    )
    cleaned = re.sub(r"(?<=[A-Za-z])(?:\s+\d+(?:\s*,\s*\d+)*)+\b", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return []
    # Split on commas OR semicolons OR " and " — AIP landing pages often
    # use semicolons ("Yu. Matveyev ; K. Egorov; A. Markeev; A. Zenkevich").
    parts = re.split(r"[,;|]\s*|\s+and\s+", cleaned)
    names: list[str] = []
    for part in parts:
        part = part.strip().rstrip(",. ")
        part = re.sub(r"\s+et\s+al\.?$", "", part, flags=re.IGNORECASE).strip()
        # Strip a leading "and " that survives when the line uses an Oxford
        # comma before "and" (", and X") — the comma splitter leaves
        # "and X" as its own part.
        part = re.sub(r"^(?:and|&)\s+", "", part, flags=re.IGNORECASE).strip()
        if not part:
            continue
        words = part.split()
        if all(w.lower() in _AUTHOR_NOISE for w in words):
            continue
        if re.match(r"^\d", part) or len(part) < 2:
            continue
        if not words[0][0:1].isupper():
            continue
        if len(words) > 5:
            continue
        if len(words) == 1 and not any(
            "\u4e00" <= c <= "\u9fff" or "\uac00" <= c <= "\ud7af" for c in part
        ):
            continue
        if re.search(r"[(\[|]|\d+\s*$", part):
            continue
        names.append(part)
    return names


def _is_noise_paragraph(text: str) -> bool:
    """Return True if a paragraph is bibliographic / boilerplate metadata,
    not real content.
    """
    lower = text.lower()
    noise_markers = (
        "authorized licensed use",
        "downloaded on",
        "©",
        "copyright",
        "all rights reserved",
        "using government drawings",
        "this report is the result of",
        "ieee transactions",
        "proceedings of",
        "permission to make digital",
        "this article has been accepted",
        "personal use of this material",
        "redistribution",
        "university of",
        "department of",
        "manuscript received",
        "doi:",
        "digital object identifier",
        "color versions of",
        "published by",
        "accepted for publication",
        "public release; distribution",
        "fundamental research",
        "approved for public",
        "report number",
        "technical report",
        "contract no",
        "scientific and technical information",
        "in the interest of",
        "==> picture",
        "intentionally omitted",
        "----- start of picture text -----",
        "----- end of picture text -----",
    )
    return any(m in lower for m in noise_markers)


# Public alias so other modules don't reach into a private name.
is_noise_paragraph = _is_noise_paragraph


@dataclass
class _Slide:
    index: int
    title: str
    body: str
    notes: str


def _parse_slides(md_text: str) -> list[_Slide]:
    slide_splits = re.split(r"(?=^## (?:Slide \d+))", md_text, flags=re.MULTILINE)
    slides: list[_Slide] = []
    for block in slide_splits:
        block = block.strip()
        if not block:
            continue
        h = re.match(r"^## Slide (\d+)(?::\s*(.+))?$", block, re.MULTILINE)
        if not h:
            continue
        index = int(h.group(1))
        title = (h.group(2) or "").strip()
        rest = block[h.end() :].strip()
        body_lines: list[str] = []
        note_lines: list[str] = []
        for line in rest.splitlines():
            if line.strip().startswith(">"):
                note_lines.append(line.strip().lstrip("> ").strip())
            else:
                body_lines.append(line)
        body = "\n".join(body_lines).strip()
        notes = " ".join(note_lines).strip()
        notes = re.sub(r"^Note:\s*", "", notes, flags=re.IGNORECASE).strip()
        slides.append(_Slide(index=index, title=title, body=body, notes=notes))
    return slides


def _is_conclusion_slide(slide: _Slide) -> bool:
    title_lower = slide.title.lower()
    keywords = (
        "conclusion",
        "concluding",
        "summary",
        "takeaway",
        "key finding",
        "wrap up",
        "wrap-up",
        "closing",
        "final",
        "outlook",
        "future work",
    )
    return any(kw in title_lower for kw in keywords)


def _synthesize_slide_summary(slides: list[_Slide]) -> str:
    parts: list[str] = []
    for slide in slides[:3]:
        slide_text = slide.title or ""
        body_clean = clean_markdown(slide.body)
        if body_clean:
            words = body_clean.split()
            excerpt = " ".join(words[:150])
            slide_text = f"{slide_text}. {excerpt}" if slide_text else excerpt
        if slide.notes:
            notes_clean = clean_markdown(slide.notes)
            words = notes_clean.split()
            notes_excerpt = " ".join(words[:80])
            slide_text = f"{slide_text} {notes_excerpt}" if slide_text else notes_excerpt
        if slide_text:
            parts.append(slide_text.strip())

    tail_slides = slides[-3:] if len(slides) > 3 else []
    conclusion_parts: list[str] = []
    for slide in tail_slides:
        if _is_conclusion_slide(slide):
            conclusion_text = slide.title or ""
            body_clean = clean_markdown(slide.body)
            if body_clean:
                words = body_clean.split()
                excerpt = " ".join(words[:200])
                conclusion_text = f"{conclusion_text}. {excerpt}" if conclusion_text else excerpt
            if slide.notes:
                notes_clean = clean_markdown(slide.notes)
                words = notes_clean.split()
                notes_excerpt = " ".join(words[:100])
                conclusion_text = (
                    f"{conclusion_text} {notes_excerpt}" if conclusion_text else notes_excerpt
                )
            if conclusion_text:
                conclusion_parts.append(conclusion_text.strip())

    if conclusion_parts:
        parts.append("Conclusions: " + ". ".join(conclusion_parts))

    if not parts:
        return ""
    text = ". ".join(parts)
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
