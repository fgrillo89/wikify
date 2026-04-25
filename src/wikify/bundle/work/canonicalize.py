"""``canonicalize`` + ``Candidate`` — split out of ``dossier.py`` per W4.

The merge logic that turns extracted concept candidates into canonical
``WikiPage`` records belongs at the head of the work-package surface
because it is what ``work add concept`` and ``work tend`` invoke. The
old home (``dossier.py``) keeps the ``Dossier`` class.
"""

from __future__ import annotations

from . import dossier as _dossier

# Direct re-export. The actual implementation still lives in dossier.py
# to keep the W4 split mechanical (no logic change). A future PR may
# physically relocate the function body — when that happens, this
# module becomes the canonical home.

Candidate = _dossier.Candidate
canonicalize = _dossier.canonicalize

__all__ = ["Candidate", "canonicalize"]
