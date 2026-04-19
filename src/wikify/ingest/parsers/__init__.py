"""One parser per source kind. Each returns a ``ParseResult`` via the registry.

All supported backends are declared directly as ``ParserBackend`` enum
members in ``registry.py``. There is no plugin mechanism — adding a new
backend means adding an enum member and a branch in ``overrides()``.
"""
