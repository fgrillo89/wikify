"""``canonicalize`` + ``Candidate`` — separate from ``dossier.py``.

The merge logic that turns extracted concept candidates into canonical
``WikiPage`` records lives here because it is what ``work add concept``
and ``work tend`` invoke. The ``Dossier`` class lives in
``dossier.py``.
"""

from __future__ import annotations

from . import dossier as _dossier

# Direct re-export. The implementation still lives in dossier.py; this
# module is the canonical import path. A future change can move the
# function body here without touching call sites.

Candidate = _dossier.Candidate
canonicalize = _dossier.canonicalize

__all__ = ["Candidate", "canonicalize"]
