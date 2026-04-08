"""Metadata extraction helpers for parsers (pdf/docx/pptx/html).

Ported verbatim from the legacy ``wikify.ingest.extract.metadata`` with
the only changes being: stdlib imports only, no dataclasses returned to
the outside, no Paper coupling. Helpers cover title, authors, summary,
year, DOI, and a slide-aware summary synthesiser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --- public surface ------------------------------------------------------


def first_heading(md_text: str) -> str | None:
    for line in md_text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# "):
            heading = stripped.lstrip("# ").strip()
            heading = clean_markdown(heading)
            if heading:
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
    m = re.search(r"(10\.\d{4,}/[^\s]+)", text)
    if m:
        return m.group(1).rstrip(".,;)")
    return None


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


def extract_authors_from_markdown(md_text: str) -> list[str]:
    lines = md_text[:5000].split("\n")
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
        if re.match(r"(?i)^#*\s*\*?\*?(abstract|introduction|index\s+terms)", line):
            break
        if re.search(
            r"(?i)(university|department|institute|school|laboratory"
            r"|lab\b|@|e-mail|email)",
            line,
        ):
            continue
        candidates.append(line)
    for line in candidates:
        names = _parse_author_line(line)
        if len(names) >= 2:
            return names
    return []


# --- internal ------------------------------------------------------------

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
    return True


def _parse_author_line(line: str) -> list[str]:
    cleaned = re.sub(r"^#+\s*", "", line)
    cleaned = re.sub(r"\*+", "", cleaned)
    cleaned = re.sub(r"_+", " ", cleaned)
    cleaned = re.sub(
        r",?\s*(?:Life |Senior |Student |Associate )?(?:Fellow|Member),?\s*(?:IEEE)?,?",
        ",",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\[[^\]]*\]", "", cleaned)
    cleaned = re.sub(r"[†‡§]+", "", cleaned)
    cleaned = re.sub(r"\*+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return []
    parts = re.split(r",\s*|\s+and\s+", cleaned)
    names: list[str] = []
    for part in parts:
        part = part.strip().rstrip(",. ")
        part = re.sub(r"\s+et\s+al\.?$", "", part, flags=re.IGNORECASE).strip()
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
    lower = text.lower()
    noise_markers = (
        "authorized licensed use",
        "downloaded on",
        "©",
        "copyright",
        "all rights reserved",
        "ieee transactions",
        "proceedings of",
        "permission to make digital",
        "this article has been accepted",
        "personal use of this material",
        "manuscript received",
        "doi:",
        "published by",
        "accepted for publication",
        "==> picture",
    )
    return any(m in lower for m in noise_markers)


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
