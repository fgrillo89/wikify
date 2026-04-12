"""One parser per source kind. Each returns a ParseResult via the registry."""

from .registry import register_parser_backend

# -- Docling backend (PDF only) -------------------------------------------

def _lazy_docling():
    from . import docling_pdf as p
    return p


register_parser_backend("docling", {"pdf": ("pdf", _lazy_docling)})


# -- dots.ocr backend (PDF only) ------------------------------------------

def _lazy_dots_ocr():
    from . import dots_ocr as p
    return p


register_parser_backend("dots_ocr", {"pdf": ("pdf", _lazy_dots_ocr)})
