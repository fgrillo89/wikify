"""Deterministic per-chunk extraction cache.

The cache is keyed by (model_id, prompt_hash, chunk_id). On hit, callers
get the cached result without dispatching any model call. On miss, the
caller's `compute` callable is invoked, the result is stored, and the
result is returned.

There is exactly one public method, `get_or_extract`. There is no public
`get` and no public `put`. There is no way to use the cache wrong.

A cache hit is invisible to anything above the binding layer: the agent
never sees a "cache hit" sentinel, the deterministic strategies just
notice that some chunks are free.

Storage layout:
    {root}/{binding_name}/{model_id}/{prompt_hash}/{chunk_id}.json

The ``binding_name`` prefix guarantees that entries produced by one
binding (for example ``fake``) can never be served to another (for
example ``claude_code``), even when the other fields collide.

The on-disk format is the JSON-serialised result the compute callable
returned, plus a small wrapper recording the first-time token cost so
the cost meter can replay it on every subsequent hit.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExtractCacheKey:
    binding_name: str
    model_id: str
    prompt_hash: str
    chunk_id: str

    def relpath(self) -> Path:
        # Hash the chunk_id so path length stays bounded on Windows (MAX_PATH
        # = 260). Chunk ids can include long doc filenames + offsets and blow
        # past that otherwise. The ``binding_name`` is the top-level
        # namespace so cross-binding collisions are impossible.
        chunk_key = hashlib.sha256(self.chunk_id.encode("utf-8")).hexdigest()[:24]
        return Path(self.binding_name) / self.model_id / self.prompt_hash / f"{chunk_key}.json"


@dataclass(frozen=True)
class CachedExtract:
    """Wrapper for one cache entry.

    `payload` is whatever the compute callable returned (it must be
    JSON-serialisable). `tokens_in` and `tokens_out` are the *first-time*
    token cost; they are stored on miss and returned on hit so the cost
    meter records the same compute cost regardless of cache state.
    """

    payload: Any
    tokens_in: int
    tokens_out: int


def prompt_hash(prompt_template: str) -> str:
    """Stable hash of a prompt template, used as the cache namespace."""
    return hashlib.sha256(prompt_template.encode("utf-8")).hexdigest()[:16]


class ExtractCache:
    """One on-disk cache. Construct once per run, share across strategies."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._hits = 0
        self._misses = 0

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    def get_or_extract(
        self,
        key: ExtractCacheKey,
        compute: Callable[[], CachedExtract],
    ) -> tuple[CachedExtract, bool]:
        """Return the cached entry for `key`, computing it on miss.

        Returns (entry, was_hit). `was_hit` is True if the entry came from
        disk, False if `compute` was called.
        """
        path = self._root / key.relpath()
        if path.exists():
            self._hits += 1
            data = json.loads(path.read_text(encoding="utf-8"))
            return (
                CachedExtract(
                    payload=data["payload"],
                    tokens_in=int(data["tokens_in"]),
                    tokens_out=int(data["tokens_out"]),
                ),
                True,
            )
        self._misses += 1
        entry = compute()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "payload": entry.payload,
                    "tokens_in": entry.tokens_in,
                    "tokens_out": entry.tokens_out,
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        return entry, False
