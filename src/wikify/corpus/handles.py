"""Short-handle resolution for corpus doc and chunk ids.

Real corpus ids are unwieldy: a doc id is the natural-title prefix plus
a 12-char hex suffix, e.g.::

    [2011 Yang] Dopant Control by Atomic Layer Deposition...Switches_5f92b0389ccd

The hex suffix is globally unique. This module lets the CLI accept and
emit the suffix alone (``5f92b0389ccd``) without losing the ability to
take a full id, while still resolving correctly when no hash suffix is
present (test fixtures use plain ids like ``paper_0``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# Hex-suffix pattern at end of an id, preceded by ``_``. Length 8+ is a
# heuristic that catches real corpus ids without false-positives on
# titles that happen to end in 8 hex chars (those don't exist with the
# leading underscore in current ingest).
_HASH_SUFFIX_RE = re.compile(r"_([0-9a-f]{8,})$")


class HandleNotFoundError(LookupError):
    """No corpus id matches the given short handle."""


class AmbiguousHandleError(LookupError):
    """More than one corpus id matches the given short handle."""

    def __init__(self, short: str, matches: list[str]) -> None:
        super().__init__(
            f"handle {short!r} is ambiguous; matches: {', '.join(matches[:5])}"
            + (f" (+{len(matches) - 5} more)" if len(matches) > 5 else "")
        )
        self.short = short
        self.matches = matches


def short_id(full_id: str) -> str:
    """Return the canonical short form of *full_id*.

    For ids that end in ``_<hex>``: return the hex suffix alone.
    For compound ids of the form ``<doc_id>/<stem>`` (figures, with
    possible future media handles): shorten the doc-id portion only.
    Otherwise: return *full_id* unchanged.
    """
    if "/" in full_id:
        doc_part, _, rest = full_id.partition("/")
        return f"{short_id(doc_part)}/{rest}"
    m = _HASH_SUFFIX_RE.search(full_id)
    return m.group(1) if m else full_id


def resolve(short: str, candidates: Iterable[str]) -> str:
    """Resolve *short* against *candidates* (full ids).

    Resolution rules, in order:

    1. Exact match wins.
    2. Otherwise: any candidate whose ``short_id`` equals *short*.
    3. Otherwise: any candidate ending with ``_<short>`` (delimited
       suffix). The leading underscore is required so a one-or-two-char
       short like ``"5"`` does not match every id whose hex hash
       happens to end in ``5``.

    Raises ``HandleNotFoundError`` on zero matches and
    ``AmbiguousHandleError`` on multiple matches at the same tier.
    """
    cands = list(candidates)

    # Tier 1: exact.
    if short in cands:
        return short

    # Tier 2: short matches a hash suffix exactly.
    by_short = [c for c in cands if short_id(c) == short]
    if len(by_short) == 1:
        return by_short[0]
    if len(by_short) > 1:
        raise AmbiguousHandleError(short, by_short)

    # Tier 3: ends with _<short>. The underscore is the explicit
    # delimiter — required so short strings cannot bleed into adjacent
    # characters (e.g. ``"5"`` matching ``..._a8b15``).
    underscore = [c for c in cands if c.endswith("_" + short)]
    if len(underscore) == 1:
        return underscore[0]
    if len(underscore) > 1:
        raise AmbiguousHandleError(short, underscore)

    raise HandleNotFoundError(short)


def format_handle(kind: str, full_id: str, *, long: bool = False) -> str:
    """Format ``kind:id`` for CLI output.

    Default emits the short hash suffix (or the full id if no suffix is
    present). Pass ``long=True`` to keep the full id.

    For ``author:`` handles, internal keys contain spaces (lowercase
    ``"first last"``); we emit ``author:first_last`` so the handle is
    pipe-safe. The matching resolver substitutes back at lookup time.
    """
    payload = full_id if long else short_id(full_id)
    if kind == "author":
        payload = payload.replace(" ", "_")
    return f"{kind}:{payload}"


def format_chunk_handles(rows: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Disambiguated chunk handles for a result set.

    For each ``(chunk_id, doc_id)`` pair, returns ``chunk:<short>`` when
    the bare short suffix is unique within ``rows``; otherwise escalates
    to ``chunk:<doc-short>/<chunk-short>`` so two distinct chunks never
    print the same handle. Falls back to the full chunk_id if no
    ``doc_id`` is available to namespace by. Pipe-safe and resolvable
    by ``resolve_chunk_id``.
    """
    pairs = list(rows)
    by_short: dict[str, set[str]] = {}
    for cid, _ in pairs:
        by_short.setdefault(short_id(cid), set()).add(cid)
    out: dict[str, str] = {}
    for cid, did in pairs:
        s = short_id(cid)
        if len(by_short[s]) > 1:
            if did:
                out[cid] = f"chunk:{short_id(did)}/{s}"
            else:
                out[cid] = f"chunk:{cid}"
        else:
            out[cid] = f"chunk:{s}"
    return out


__all__ = [
    "AmbiguousHandleError",
    "HandleNotFoundError",
    "format_chunk_handles",
    "format_handle",
    "resolve",
    "short_id",
]
