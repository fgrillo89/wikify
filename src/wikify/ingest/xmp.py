"""Read publisher-injected XMP metadata from a PDF.

Most modern scholarly PDFs ship a Dublin Core + PRISM XMP packet that
is typically cleaner and richer than the older ``/Info`` dict — in
particular the full author list and the DOI, which ``/Info`` usually
omits.

``read_xmp(doc)`` returns a dict with the fields we consume downstream:
``title``, ``authors``, ``keywords``, ``doi``, ``venue``, ``volume``,
``pages``, ``year``. Missing fields are empty strings / empty lists / None.
Never raises: a malformed or absent packet simply returns ``{}``.

Callers own the priority decision — XMP is one more source in the chain,
not an override. Junk detection (``is_garbled_title`` etc.) and the later
``choose_document_title`` / DOI-authoritative merge still apply.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree as ET

# Namespace map covering Dublin Core + PRISM revisions seen in publisher
# PDFs. PRISM 2.0 is most common; 2.1 and 3.0 show up on newer
# Elsevier/Wiley documents.
NS = {
    "dc":      "http://purl.org/dc/elements/1.1/",
    "rdf":     "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "prism":   "http://prismstandard.org/namespaces/basic/2.0/",
    "prism21": "http://prismstandard.org/namespaces/basic/2.1/",
    "prism30": "http://prismstandard.org/namespaces/basic/3.0/",
}

_PRISM_VARIANTS = ("prism", "prism21", "prism30")
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    parts = [(node.text or "").strip() for node in el.iter() if node.text]
    return " ".join(p for p in parts if p).strip()


def _list_items(el: ET.Element | None) -> list[str]:
    if el is None:
        return []
    items = el.findall(".//rdf:li", NS)
    return [t for t in (_text(li) for li in items) if t]


def _first_prism(root: ET.Element, field: str) -> ET.Element | None:
    for ns in _PRISM_VARIANTS:
        el = root.find(f".//{ns}:{field}", NS)
        if el is not None:
            return el
    return None


def read_xmp(doc) -> dict:
    """Parse the XMP packet on a ``fitz`` PDF document. Never raises."""
    try:
        xml = doc.get_xml_metadata()
    except Exception:  # noqa: BLE001 - any pymupdf-internal failure
        return {}
    if not xml or not xml.strip():
        return {}
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return {}

    start = _text(_first_prism(root, "startingPage"))
    end = _text(_first_prism(root, "endingPage"))
    pages = f"{start}-{end}" if (start and end) else start

    date = ""
    for field in ("publicationDate", "coverDate"):
        date = _text(_first_prism(root, field))
        if date:
            break
    year_match = _YEAR_RE.search(date) if date else None
    year = int(year_match.group(0)) if year_match else None

    return {
        "title":    _text(root.find(".//dc:title", NS)),
        "authors":  _list_items(root.find(".//dc:creator", NS)),
        "keywords": _list_items(root.find(".//dc:subject", NS)),
        "doi":      _text(_first_prism(root, "doi")),
        "venue":    _text(_first_prism(root, "publicationName")),
        "volume":   _text(_first_prism(root, "volume")),
        "pages":    pages,
        "year":     year,
    }
