"""Short-handle resolution for corpus doc and chunk ids.

Real corpus ids are unwieldy: a doc id is the natural-title prefix plus
a 12-char hex suffix, e.g.::

    [2011 Yang] Dopant Control by Atomic Layer Deposition...Switches_5f92b0389ccd

The hex suffix is globally unique. This module lets the CLI accept and
emit the suffix alone (``5f92b0389ccd``) without losing the ability to
take a full id, while still resolving correctly when no hash suffix is
present (test fixtures use plain ids like ``paper_0``).

For bulk callers that resolve thousands of ids, use ``build_index`` /
``resolve_indexed`` / ``try_resolve`` so each lookup is O(1) rather
than O(N) per call.  ``resolve`` and ``try_resolve`` also accept a
pre-built ``HandleIndex`` transparently.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

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


# ---------------------------------------------------------------------------
# Indexed (O(1)) resolver
# ---------------------------------------------------------------------------


@dataclass
class HandleIndex:
    """Pre-built O(1) resolver over a fixed candidate set.

    Build once with ``build_index(candidates)``; reuse for many lookups.

    Internal maps:

    * ``_exact`` — all full ids as a set (tier-1 hit).
    * ``_by_short`` — ``short_id(full) -> [full, ...]`` (tier-2).
    * ``_by_suffix`` — ``"_" + suffix -> full`` for unambiguous
      ``endswith`` matches (tier-3).  Ambiguous entries are omitted.
    """

    _exact: set[str] = field(default_factory=set)
    _by_short: dict[str, list[str]] = field(default_factory=dict)
    _by_suffix: dict[str, str] = field(default_factory=dict)

    def __contains__(self, item: str) -> bool:  # noqa: D105
        return item in self._exact


def build_index(candidates: Iterable[str]) -> HandleIndex:
    """Build a ``HandleIndex`` from *candidates* (full ids).

    The tier semantics are identical to ``resolve``:

    * Tier 1 — exact match.
    * Tier 2 — ``short_id(full) == short`` (unique short-hash match).
    * Tier 3 — ``full.endswith("_" + short)`` for unambiguous suffix.

    Ambiguous tier-2 and tier-3 entries are retained (they still raise
    ``AmbiguousHandleError`` at lookup time), but tier-3 entries that
    are ambiguous are simply omitted from ``_by_suffix`` — a
    ``HandleNotFoundError`` falls through to ``resolve`` callers
    correctly.
    """
    idx = HandleIndex()
    # tier-3 tracking: suffix_key -> [full, ...]
    suffix_map: dict[str, list[str]] = {}

    for full in candidates:
        idx._exact.add(full)
        s = short_id(full)
        idx._by_short.setdefault(s, []).append(full)
        # Tier-3: the leading-underscore suffix key.
        last_under = full.rfind("_")
        if last_under != -1:
            suffix_key = full[last_under:]  # includes the leading "_"
            suffix_map.setdefault(suffix_key, []).append(full)

    # Populate _by_suffix only for unambiguous entries.
    for suffix_key, fulls in suffix_map.items():
        if len(fulls) == 1:
            idx._by_suffix[suffix_key] = fulls[0]

    return idx


def resolve_indexed(short: str, index: HandleIndex) -> str:
    """Resolve *short* against a pre-built ``HandleIndex``.

    Same tier semantics and same exceptions as ``resolve``.
    """
    # Tier 1: exact.
    if short in index._exact:
        return short

    # Tier 2: short_id match.
    by_short = index._by_short.get(short, [])
    if len(by_short) == 1:
        return by_short[0]
    if len(by_short) > 1:
        raise AmbiguousHandleError(short, by_short)

    # Tier 3: unambiguous leading-underscore suffix.
    suffix_key = "_" + short
    if suffix_key in index._by_suffix:
        return index._by_suffix[suffix_key]

    # Check whether the suffix was present but ambiguous (not in _by_suffix
    # but more than one candidate ends with this key).
    ambiguous = [f for f in index._exact if f.endswith(suffix_key)]
    if len(ambiguous) > 1:
        raise AmbiguousHandleError(short, ambiguous)

    raise HandleNotFoundError(short)


def resolve(short: str, candidates: Iterable[str] | HandleIndex) -> str:
    """Resolve *short* against *candidates* (full ids) or a ``HandleIndex``.

    Resolution rules, in order:

    1. Exact match wins.
    2. Otherwise: any candidate whose ``short_id`` equals *short*.
    3. Otherwise: any candidate ending with ``_<short>`` (delimited
       suffix). The leading underscore is required so a one-or-two-char
       short like ``"5"`` does not match every id whose hex hash
       happens to end in ``5``.

    Raises ``HandleNotFoundError`` on zero matches and
    ``AmbiguousHandleError`` on multiple matches at the same tier.

    Accepts either an iterable of full ids or a pre-built ``HandleIndex``
    (the indexed path is O(1) per lookup; the iterable path is O(N)).
    """
    if isinstance(candidates, HandleIndex):
        return resolve_indexed(short, candidates)

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


def try_resolve(
    short: str,
    candidates: Iterable[str] | HandleIndex,
) -> str | None:
    """Like ``resolve`` but returns ``None`` instead of raising on miss.

    Convenient for "resolve or skip" sites.  Still propagates
    ``AmbiguousHandleError`` so callers can decide whether to warn.
    """
    try:
        return resolve(short, candidates)
    except HandleNotFoundError:
        return None


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
    "HandleIndex",
    "HandleNotFoundError",
    "build_index",
    "format_chunk_handles",
    "format_handle",
    "resolve",
    "resolve_indexed",
    "short_id",
    "try_resolve",
]
