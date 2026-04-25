"""On-disk index over a wiki bundle.

The bundle source-of-truth is still the per-page markdown files. This
index is a *projection*: a single ``_index.json`` file rebuildable from
the page files at any time. It exists so that runtime operations
(canonicalize, crosslink, dedup-after-extract, the agent's
``inspect_page``/``propose_concept`` actions, and the eval harness) can
look pages up in O(1) instead of walking the directory and re-parsing
every file.

The index is rewritten atomically every time the pipeline finishes
writing pages, and is consulted via ``WikiIndex.load(bundle)``. If the
file is missing or stale, ``rebuild_index(bundle)`` reconstructs it
from the page files alone.
"""

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ...models import WikiPage
from ...paths import BundlePaths
from .page_naming import page_filename, page_id_from_title

_INDEX_FILENAME = "_index.json"
_INDEX_MD_FILENAME = "_index.md"
_NORM_RE = re.compile(r"[^a-z0-9]+")
_SKELETON_MIN_BODY_LEN = 200


def _normalize(s: str) -> str:
    return _NORM_RE.sub("-", s.lower()).strip("-")


@dataclass(frozen=True)
class IndexEntry:
    id: str
    kind: str  # "article" | "person"
    title: str
    aliases: tuple[str, ...]
    path: str  # bundle-relative
    n_evidence: int
    doc_ids: tuple[str, ...]
    links: tuple[str, ...]


@dataclass
class WikiIndex:
    """Fast lookup over a wiki bundle. Loaded once per run."""

    bundle_root: Path
    entries: dict[str, IndexEntry] = field(default_factory=dict)
    _alias_to_id: dict[str, str] = field(default_factory=dict)
    _doc_to_ids: dict[str, list[str]] = field(default_factory=dict)

    # ---- public API ------------------------------------------------------

    def __post_init__(self) -> None:
        self._alias_to_id = {}
        self._doc_to_ids = {}
        for e in self.entries.values():
            self._index_entry(e)

    def __contains__(self, page_id: str) -> bool:
        return page_id in self.entries

    def __iter__(self):
        return iter(self.entries.values())

    def __len__(self) -> int:
        return len(self.entries)

    @property
    def concepts(self) -> list[IndexEntry]:
        return [e for e in self.entries.values() if e.kind == "article"]

    @property
    def people(self) -> list[IndexEntry]:
        return [e for e in self.entries.values() if e.kind == "person"]

    def get(self, page_id: str) -> IndexEntry | None:
        return self.entries.get(page_id)

    def resolve_alias(self, alias: str) -> str | None:
        """Return the page id whose title or alias normalises to ``alias``."""
        return self._alias_to_id.get(_normalize(alias))

    def pages_for_doc(self, doc_id: str) -> list[str]:
        """All page ids that cite at least one chunk from ``doc_id``."""
        return list(self._doc_to_ids.get(doc_id, []))

    def add(self, entry: IndexEntry) -> None:
        self.entries[entry.id] = entry
        self._index_entry(entry)

    def remove(self, page_id: str) -> None:
        entry = self.entries.pop(page_id, None)
        if entry is None:
            return
        for k in (entry.title, *entry.aliases):
            n = _normalize(k)
            if self._alias_to_id.get(n) == page_id:
                self._alias_to_id.pop(n, None)
        for doc_id in entry.doc_ids:
            ids = self._doc_to_ids.get(doc_id, [])
            if page_id in ids:
                ids.remove(page_id)

    # ---- persistence -----------------------------------------------------

    def save(self) -> Path:
        """Write both ``_index.json`` (machine-readable) and ``_index.md``
        (human-inspectable, with relative links to every page).
        """
        path = self.bundle_root / _INDEX_FILENAME
        payload = {
            "version": 1,
            "entries": [
                {
                    "id": e.id,
                    "kind": e.kind,
                    "title": e.title,
                    "aliases": list(e.aliases),
                    "path": e.path,
                    "n_evidence": e.n_evidence,
                    "doc_ids": list(e.doc_ids),
                    "links": list(e.links),
                }
                for e in self.entries.values()
            ],
        }
        _atomic_write(path, json.dumps(payload, indent=2))
        _atomic_write(self.bundle_root / _INDEX_MD_FILENAME, self._render_md())
        return path

    def _render_md(self) -> str:
        """Render the index as a markdown file with relative links.

        Layout:
            # Wiki index
            *N articles, M people*

            ## Concepts
            - [Title](articles/id.md) — *N evidence, K docs* — links: [a](...)

            ## People
            - [Name](people/id.md) — *N evidence, K docs*
        """
        concepts = sorted(self.concepts, key=lambda e: e.title.lower())
        people = sorted(self.people, key=lambda e: e.title.lower())
        lines: list[str] = ["# Wiki index", ""]
        lines.append(f"*{len(concepts)} articles, {len(people)} people*")
        lines.append("")
        if concepts:
            lines.append("## Articles")
            lines.append("")
            for e in concepts:
                lines.append(self._render_entry_line(e))
            lines.append("")
        if people:
            lines.append("## People")
            lines.append("")
            for e in people:
                lines.append(self._render_entry_line(e))
            lines.append("")
        return "\n".join(lines)

    def _render_entry_line(self, e: IndexEntry) -> str:
        link_count = len(e.links)
        doc_count = len(e.doc_ids)
        meta = f"*{e.n_evidence} ev, {doc_count} docs, {link_count} links*"
        aliases = f" (aka {', '.join(e.aliases)})" if e.aliases else ""
        return f"- [{e.title}]({e.path}) — {meta}{aliases}"

    @classmethod
    def load(cls, bundle: BundlePaths) -> "WikiIndex":
        path = bundle.root / _INDEX_FILENAME
        if not path.exists():
            return cls(bundle_root=bundle.root)
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = {
            e["id"]: IndexEntry(
                id=e["id"],
                kind=e["kind"],
                title=e["title"],
                aliases=tuple(e["aliases"]),
                path=e["path"],
                n_evidence=int(e["n_evidence"]),
                doc_ids=tuple(e["doc_ids"]),
                links=tuple(e["links"]),
            )
            for e in data["entries"]
        }
        return cls(bundle_root=bundle.root, entries=entries)

    # ---- internal --------------------------------------------------------

    def _index_entry(self, e: IndexEntry) -> None:
        for key in (e.title, *e.aliases):
            self._alias_to_id[_normalize(key)] = e.id
        for doc_id in e.doc_ids:
            self._doc_to_ids.setdefault(doc_id, []).append(e.id)


# --- builders ------------------------------------------------------------


def entry_from_page(page: WikiPage, bundle: BundlePaths) -> IndexEntry:
    sub = "articles" if page.kind == "article" else "people"
    return IndexEntry(
        id=page.id,
        kind=page.kind,
        title=page.title,
        aliases=tuple(page.aliases),
        path=f"{sub}/{page_filename(page.id)}",
        n_evidence=len(page.evidence),
        doc_ids=tuple(sorted({ev.doc_id for ev in page.evidence})),
        links=tuple(page.links),
    )


def build_index(bundle: BundlePaths, pages: list[WikiPage]) -> WikiIndex:
    """Build an index for a freshly-written set of pages.

    Skeleton pages (body_markdown shorter than _SKELETON_MIN_BODY_LEN chars)
    are excluded so the index only enumerates real, written pages.
    """
    entries = {
        p.id: entry_from_page(p, bundle)
        for p in pages
        if len(p.body_markdown) >= _SKELETON_MIN_BODY_LEN
    }
    return WikiIndex(bundle_root=bundle.root, entries=entries)


def rebuild_index(bundle: BundlePaths) -> WikiIndex:
    """Reconstruct the index by parsing every page file in the bundle.

    Used when the index file is missing or stale. Reads only the YAML
    frontmatter; never blocks on the body.
    """
    from .page import parse_page

    entries: dict[str, IndexEntry] = {}
    for sub in ("articles", "people"):
        d = bundle.root / sub
        if not d.exists():
            continue
        for f in sorted(d.glob("*.md")):
            page = parse_page(f)
            entries[page.id] = IndexEntry(
                id=page.id,
                kind=page.kind,
                title=page.title,
                aliases=tuple(page.aliases),
                path=f"{sub}/{page_filename(page.id)}",
                n_evidence=len(page.evidence),
                doc_ids=tuple(sorted({ev.doc_id for ev in page.evidence})),
                links=tuple(page.links),
            )
    idx = WikiIndex(bundle_root=bundle.root, entries=entries)
    idx.save()
    return idx


def migrate_concepts_dir(bundle: BundlePaths) -> bool:
    """Rename the on-disk ``concepts/`` directory to ``articles/`` if needed.

    Idempotent: if ``articles/`` already exists (or ``concepts/`` does not
    exist), this is a no-op. Returns True if a rename was performed.

    Must be called explicitly before ``WikiIndex.load`` when migrating
    older bundles that still use the ``concepts/`` directory name.
    """
    if not bundle.root.exists():
        return False
    old_dir = bundle.root / "concepts"
    new_dir = bundle.root / "articles"
    if not old_dir.exists() or new_dir.exists():
        return False
    old_dir.rename(new_dir)
    # Rewrite kind="concept" -> kind="article" in every frontmatter.
    for f in sorted(new_dir.glob("*.md")):
        try:
            raw = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if "kind: concept" in raw:
            f.write_text(raw.replace("kind: concept", "kind: article", 1), encoding="utf-8")
    # Drop stale index so rebuild picks up the new paths.
    idx_path = bundle.root / _INDEX_FILENAME
    if idx_path.exists():
        try:
            idx_path.unlink()
        except OSError:
            pass
    return True


def migrate_prefixed_page_ids(bundle: BundlePaths) -> int:
    """Rename any prefixed ``concept-*.md`` / ``person-*.md`` files in-place
    to their natural-title filename. Returns the number of renames.

    Reads the frontmatter ``title:`` field to pick the new filename.
    Rewrites ``_index.json`` if it exists and contained prefixed entries.
    """
    if not bundle.root.exists():
        return 0
    renamed = 0
    for sub in ("articles", "people"):
        d = bundle.root / sub
        if not d.exists():
            continue
        for f in list(d.glob("*.md")):
            stem = f.stem
            if not (stem.startswith("concept-") or stem.startswith("person-")):
                continue
            # Parse frontmatter title.
            try:
                raw = f.read_text(encoding="utf-8")
            except OSError:
                continue
            title = _extract_title_from_frontmatter(raw) or stem.split("-", 1)[1].replace("-", " ")
            new_id = page_id_from_title(title)
            if not new_id or new_id == stem:
                continue
            new_path = d / page_filename(new_id)
            if new_path.exists():
                continue
            # Rewrite frontmatter id/title to new values.
            new_raw = _rewrite_frontmatter_id(raw, new_id, title)
            new_path.write_text(new_raw, encoding="utf-8")
            sidecar = f.with_suffix(".provenance.json")
            if sidecar.exists():
                sidecar.rename(new_path.with_suffix(".provenance.json"))
            f.unlink()
            renamed += 1
    if renamed:
        idx_path = bundle.root / _INDEX_FILENAME
        if idx_path.exists():
            try:
                idx_path.unlink()
            except OSError:
                pass
    return renamed


_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _extract_title_from_frontmatter(raw: str) -> str | None:
    m = _FM_RE.match(raw)
    if not m:
        return None
    for line in m.group(1).splitlines():
        if line.strip().startswith("title:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return None


def _rewrite_frontmatter_id(raw: str, new_id: str, new_title: str) -> str:
    m = _FM_RE.match(raw)
    if not m:
        return raw
    fm = m.group(1)
    new_lines = []
    saw_id = False
    saw_title = False
    for line in fm.splitlines():
        if line.strip().startswith("id:"):
            new_lines.append(f"id: {new_id}")
            saw_id = True
        elif line.strip().startswith("title:"):
            new_lines.append(f"title: {new_title}")
            saw_title = True
        else:
            new_lines.append(line)
    if not saw_id:
        new_lines.insert(0, f"id: {new_id}")
    if not saw_title:
        new_lines.insert(1, f"title: {new_title}")
    return "---\n" + "\n".join(new_lines) + "\n---\n" + raw[m.end() :]


def _atomic_write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".idx-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path
