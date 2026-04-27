"""Long-lived committed-wiki query session."""

from __future__ import annotations

from dataclasses import dataclass

from ...api import Bundle
from . import queries


@dataclass
class WikiSearchSession:
    """Reusable in-process view over committed wiki pages."""

    bundle: Bundle

    def __post_init__(self) -> None:
        self._pages: dict[str, dict] = {}
        for slug in queries.list_articles(self.bundle):
            info = queries.show_page(self.bundle, handle=slug)
            if info is not None:
                self._pages[slug] = info
        for slug in queries.list_people(self.bundle):
            info = queries.show_page(self.bundle, handle=slug)
            if info is not None:
                self._pages[slug] = info

    @property
    def n_pages(self) -> int:
        return len(self._pages)

    def list_pages(self) -> list[dict]:
        return sorted(
            (
                {"slug": info["slug"], "kind": info["kind"]}
                for info in self._pages.values()
            ),
            key=lambda item: (item["kind"], item["slug"]),
        )

    def list_articles(self) -> list[str]:
        return sorted(
            slug for slug, info in self._pages.items()
            if info["kind"] == "article"
        )

    def list_people(self) -> list[str]:
        return sorted(
            slug for slug, info in self._pages.items()
            if info["kind"] == "person"
        )

    def list_files(self) -> list[str]:
        return sorted(info["path"] for info in self._pages.values())

    def find_text(self, needle: str, *, top_k: int) -> list[dict]:
        needle_lower = needle.lower()
        out: list[dict] = []
        for info in sorted(
            self._pages.values(),
            key=lambda item: (item["kind"], item["slug"]),
        ):
            text = info["text"]
            idx = text.lower().find(needle_lower)
            if idx < 0:
                continue
            out.append(
                {
                    "slug": info["slug"],
                    "kind": info["kind"],
                    "path": info["path"],
                    "snippet": text[max(0, idx - 40) : idx + 120].replace("\n", " "),
                }
            )
            if len(out) >= top_k:
                break
        return out

    def show(self, handle: str) -> dict | None:
        if handle in self._pages:
            return self._pages[handle]
        return queries.show_page(self.bundle, handle=handle)


__all__ = ["WikiSearchSession"]
