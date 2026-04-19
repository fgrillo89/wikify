"""One parser per source kind. Each returns a ParseResult via the registry."""

from .registry import register_parser_backend

# -- Docling backend (PDF only) -------------------------------------------

def _lazy_docling():
    from . import docling_pdf as p
    return p


register_parser_backend("docling", {"pdf": ("pdf", _lazy_docling)}, gpu=True)


# -- Marker backend (PDF only) --------------------------------------------

def _lazy_marker():
    from . import marker_pdf as p
    return p


register_parser_backend("marker", {"pdf": ("pdf", _lazy_marker)}, gpu=True)
