"""Zotero integration client for ScholarForge."""

from __future__ import annotations

from pyzotero import zotero

from scholarforge.config import settings
from scholarforge.store.models import Paper


class ZoteroClient:
    """Push papers to a Zotero library via the pyzotero API."""

    def __init__(self) -> None:
        if not settings.zotero_library_id or not settings.zotero_api_key:
            raise RuntimeError(
                "Zotero is not configured. Set SCHOLARFORGE_ZOTERO_LIBRARY_ID and "
                "SCHOLARFORGE_ZOTERO_API_KEY environment variables (or in .env)."
            )
        self._zot = zotero.Zotero(
            library_id=settings.zotero_library_id,
            library_type=settings.zotero_library_type,
            api_key=settings.zotero_api_key,
        )

    # ── internal helpers ───────────────────────────────────────────────────────

    def _paper_to_item(self, paper: Paper) -> dict:
        """Convert a Paper to a Zotero journalArticle item dict."""
        template = self._zot.item_template("journalArticle")
        template["title"] = paper.title or ""
        template["DOI"] = paper.doi or ""
        if paper.year:
            template["date"] = str(paper.year)
        # Zotero creators: list of {creatorType, firstName, lastName}
        creators = []
        for full_name in paper.parsed_authors:
            parts = full_name.strip().split()
            if len(parts) >= 2:
                first, last = " ".join(parts[:-1]), parts[-1]
            else:
                first, last = "", full_name
            creators.append({"creatorType": "author", "firstName": first, "lastName": last})
        if creators:
            template["creators"] = creators
        if paper.summary:
            template["abstractNote"] = paper.summary
        return template

    # ── public API ─────────────────────────────────────────────────────────────

    def push_papers(self, papers: list[Paper]) -> dict[str, str]:
        """Push a list of papers to Zotero.

        Returns a mapping of ``{paper.id: zotero_key}`` for each successfully
        created item.
        """
        if not papers:
            return {}

        items = [self._paper_to_item(p) for p in papers]
        result = self._zot.create_items(items)

        # pyzotero returns {"success": {"0": key, ...}, "unchanged": {}, "failed": {}}
        id_to_key: dict[str, str] = {}
        success = result.get("success", {})
        for idx_str, zotero_key in success.items():
            idx = int(idx_str)
            if idx < len(papers):
                id_to_key[papers[idx].id] = zotero_key
        return id_to_key

    def sync_corpus(self, papers: list[Paper] | None = None) -> int:
        """Push all un-synced papers to Zotero.

        Args:
            papers: Explicit list of papers to consider. If ``None``, the caller
                    is responsible for providing the list (no implicit DB query
                    is performed here to keep this module DB-agnostic).

        Returns:
            Count of successfully synced papers.
        """
        if papers is None:
            return 0

        unsynced = [p for p in papers if not p.zotero_key]
        if not unsynced:
            return 0

        id_to_key = self.push_papers(unsynced)
        return len(id_to_key)
