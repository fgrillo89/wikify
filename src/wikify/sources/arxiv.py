"""arXiv harvester: OAI-PMH metadata identify + bounded-async PDF download.

Phase 1 (identify) walks a complete category set via OAI-PMH
``resumptionToken``, serially (ToU: <= 1 request / 3 s), to
``manifest.jsonl`` + ``harvest_state.json``. Phase 2 (download) fetches
each pending PDF concurrently under a shared rate limiter + semaphore.
Both resume from on-disk state; file existence is the source of truth for
download resume, with the manifest ``status`` field a best-effort mirror.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx
from aiolimiter import AsyncLimiter

from ..util.async_limits import with_limiter, with_semaphore

logger = logging.getLogger(__name__)

OAI_ENDPOINT = "https://oaipmh.arxiv.org/oai"
OAI_NS = "{http://www.openarchives.org/OAI/2.0/}"
ARXIV_NS = "{http://arxiv.org/OAI/arXiv/}"
# Fetch PDFs from export.arxiv.org -- the host arXiv sets aside for
# programmatic access -- not the interactive arxiv.org front end.
PDF_BASE = "https://export.arxiv.org/pdf/"

QUERY_ENDPOINT = "http://export.arxiv.org/api/query"
ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_ATOM_NS = "{http://arxiv.org/schemas/atom}"
OPENSEARCH_NS = "{http://a9.com/-/spec/opensearch/1.1/}"

MANIFEST_NAME = "manifest.jsonl"
STATE_NAME = "harvest_state.json"

SCHEMA_VERSION = 1
_OAI_MAX_503 = 6
_MAX_RETRY_WAIT_S = 300.0
_DOWNLOAD_BATCH = 500  # checkpoint manifest after each batch of PDFs.

# Two distinct rate regimes.
#  - Metadata APIs (OAI-PMH, Query API): arXiv ToU caps at 1 request / 3 s
#    on a single connection. The harvest/scout loops honor this.
#  - PDF downloads (export.arxiv.org): arXiv guidance tolerates ~4 req/s,
#    so phase 2 runs that fast/concurrent by default and backs off on
#    429/503. This is the documented PDF-friendly ceiling, not the API one.
_OAI_DELAY_S = 3.0
_PDF_CONCURRENCY = 4
_PDF_RATE = 4.0  # requests per second


def _user_agent() -> str:
    """Descriptive UA; appends a contact from WIKIFY_CONTACT_EMAIL if set."""
    contact = os.environ.get("WIKIFY_CONTACT_EMAIL", "").strip()
    return f"wikify-arxiv-harvester/0.1 (+{contact})" if contact else (
        "wikify-arxiv-harvester/0.1"
    )


@dataclass
class ArxivRecord:
    """One arXiv paper's metadata, as harvested from OAI-PMH."""

    arxiv_id: str
    title: str
    authors: list[str]
    summary: str
    categories: list[str]
    primary_category: str
    published: str
    updated: str
    doi: str
    journal_ref: str
    pdf_url: str
    pdf_filename: str
    status: str = "pending"  # pending | done | failed
    schema_version: int = SCHEMA_VERSION


@dataclass
class HarvestReport:
    harvested: int
    complete_list_size: int | None
    resumed: bool
    already_done: bool = False


@dataclass
class DownloadReport:
    downloaded: int
    skipped: int
    failed: list[dict] = field(default_factory=list)


@dataclass
class ScoutReport:
    query: str
    total_results: int
    sampled: int
    primary_histogram: list[dict]  # [{"category", "count", "setspec"}], desc by count


class UnknownArxivCategoryError(ValueError):
    """A dotted category whose archive isn't a known arXiv archive."""


class HarvestStateMismatchError(RuntimeError):
    """``harvest_state.json`` was created for a different request.

    Raised when an existing staging dir's stored sets / metadata prefix do
    not match the current request, so a resume would silently harvest the
    wrong categories or mislabel the manifest.
    """

    def __init__(self, stored_sets, requested_sets, stored_prefix, requested_prefix):
        self.stored_sets = list(stored_sets)
        self.requested_sets = list(requested_sets)
        self.stored_prefix = stored_prefix
        self.requested_prefix = requested_prefix
        super().__init__(
            "harvest_state.json was created for a different request "
            f"(stored sets {self.stored_sets}, requested {self.requested_sets})"
        )


# Archives whose OAI group name equals the archive name (group:archive:sub
# == <archive>:<archive>:<sub>).
_SELF_GROUP_ARCHIVES = frozenset({
    "cs", "econ", "eess", "math", "q-bio", "q-fin", "stat",
})
# Archives that live under the ``physics`` OAI group
# (physics:<archive>:<sub>).
_PHYSICS_ARCHIVES = frozenset({
    "astro-ph", "cond-mat", "gr-qc", "hep-ex", "hep-lat", "hep-ph", "hep-th",
    "math-ph", "nlin", "nucl-ex", "nucl-th", "physics", "quant-ph",
})


def setspec_for_category(category: str) -> str:
    """Map an arXiv category id to its OAI-PMH setSpec.

    arXiv's setSpec is ``<group>:<archive>:<subcategory>``. For self-grouped
    archives (cs, econ, eess, math, q-bio, q-fin, stat) the group equals the
    archive (``cs.LG`` -> ``cs:cs:LG``). Physics-group archives map to
    ``physics:<archive>:<sub>`` (``cond-mat.mtrl-sci`` ->
    ``physics:cond-mat:mtrl-sci``). A value containing ``:`` is treated as a
    raw setSpec and returned unchanged; a value with no ``.`` is returned
    unchanged. A dotted category with an unrecognized archive raises
    :class:`UnknownArxivCategoryError` (pass the exact setSpec via ``--set``).
    """
    cat = category.strip()
    if ":" in cat or "." not in cat:
        return cat
    archive, sub = cat.split(".", 1)
    if archive in _SELF_GROUP_ARCHIVES:
        group = archive
    elif archive in _PHYSICS_ARCHIVES:
        group = "physics"
    else:
        raise UnknownArxivCategoryError(
            f"unknown arXiv archive {archive!r} in category {cat!r}; "
            "pass the raw OAI setSpec via --set"
        )
    return f"{group}:{archive}:{sub}"


def _safe_setspec(category: str) -> str:
    """setspec_for_category, but empty string for unmappable categories."""
    try:
        return setspec_for_category(category)
    except UnknownArxivCategoryError:
        return ""


# --- record parsing -------------------------------------------------------

def _text(parent: ET.Element, tag: str) -> str:
    child = parent.find(tag)
    return child.text.strip() if child is not None and child.text else ""


def parse_record(record_el: ET.Element) -> ArxivRecord | None:
    """Parse one OAI ``<record>``; return None for deleted/empty records."""
    header = record_el.find(f"{OAI_NS}header")
    if header is not None and header.get("status") == "deleted":
        return None
    meta = record_el.find(f"{OAI_NS}metadata")
    arx = meta.find(f"{ARXIV_NS}arXiv") if meta is not None else None
    if arx is None:
        return None
    arxiv_id = _text(arx, f"{ARXIV_NS}id")
    if not arxiv_id:
        return None

    categories = _text(arx, f"{ARXIV_NS}categories").split()
    authors: list[str] = []
    authors_el = arx.find(f"{ARXIV_NS}authors")
    if authors_el is not None:
        for a in authors_el.findall(f"{ARXIV_NS}author"):
            name = " ".join(p for p in (
                _text(a, f"{ARXIV_NS}forenames"), _text(a, f"{ARXIV_NS}keyname"),
            ) if p)
            if name:
                authors.append(name)

    return ArxivRecord(
        arxiv_id=arxiv_id,
        title=" ".join(_text(arx, f"{ARXIV_NS}title").split()),
        authors=authors,
        summary=" ".join(_text(arx, f"{ARXIV_NS}abstract").split()),
        categories=categories,
        primary_category=categories[0] if categories else "",
        published=_text(arx, f"{ARXIV_NS}created"),
        updated=_text(arx, f"{ARXIV_NS}updated"),
        doi=_text(arx, f"{ARXIV_NS}doi"),
        journal_ref=_text(arx, f"{ARXIV_NS}journal-ref"),
        pdf_url=f"{PDF_BASE}{arxiv_id}",
        pdf_filename=f"{arxiv_id.replace('/', '_')}.pdf",
    )


# --- manifest + state IO --------------------------------------------------

def manifest_path(out_dir: Path) -> Path:
    return out_dir / MANIFEST_NAME


def state_path(out_dir: Path) -> Path:
    return out_dir / STATE_NAME


def _record_lines(records: list[ArxivRecord]) -> str:
    return "".join(json.dumps(asdict(r), ensure_ascii=False) + "\n" for r in records)


def read_records(out_dir: Path) -> list[ArxivRecord]:
    path = manifest_path(out_dir)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [ArxivRecord(**json.loads(line)) for line in fh if line.strip()]


def append_records(out_dir: Path, records: list[ArxivRecord]) -> None:
    if not records:
        return
    with manifest_path(out_dir).open("a", encoding="utf-8") as fh:
        fh.write(_record_lines(records))


def write_records(out_dir: Path, records: list[ArxivRecord]) -> None:
    """Atomically rewrite the whole manifest (used to checkpoint status)."""
    path = manifest_path(out_dir)
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text(_record_lines(records), encoding="utf-8")
    tmp.replace(path)


def read_state(out_dir: Path) -> dict | None:
    path = state_path(out_dir)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def write_state(out_dir: Path, state: dict) -> None:
    path = state_path(out_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _seen_ids(out_dir: Path) -> set[str]:
    return {rec.arxiv_id for rec in read_records(out_dir)}


# --- phase 1: identify (OAI-PMH harvest) ----------------------------------

def _retry_after(resp: httpx.Response, default: float) -> float:
    raw = resp.headers.get("Retry-After")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass  # HTTP-date form -> fall back to default.
    return default


def _get_with_retry(
    client: httpx.Client, url: str, params: dict, delay_s: float
) -> httpx.Response:
    """GET, retrying on 429/503 with Retry-After backoff."""
    for _ in range(_OAI_MAX_503):
        resp = client.get(url, params=params)
        if resp.status_code in (429, 503):
            wait = max(0.0, min(_retry_after(resp, delay_s * 4), _MAX_RETRY_WAIT_S))
            logger.info("arxiv %d; sleeping %.0fs before retry", resp.status_code, wait)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError("arxiv: too many throttled (429/503) responses")


async def _aget_with_retry(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """Async GET, retrying on 429/503 with Retry-After backoff."""
    for _ in range(_OAI_MAX_503):
        resp = await client.get(url)
        if resp.status_code in (429, 503):
            wait = max(0.0, min(_retry_after(resp, _OAI_DELAY_S * 4), _MAX_RETRY_WAIT_S))
            logger.info("arxiv %d on %s; sleeping %.0fs", resp.status_code, url, wait)
            await asyncio.sleep(wait)
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError("arxiv: too many throttled (429/503) responses")


def harvest(
    sets: list[str],
    out_dir: Path,
    *,
    metadata_prefix: str = "arXiv",
    delay_s: float = _OAI_DELAY_S,
    transport: httpx.BaseTransport | None = None,
) -> HarvestReport:
    """Harvest every record in ``sets`` to ``manifest.jsonl`` (resumable).

    Resumes from ``harvest_state.json``: continues the current set's
    ``resumptionToken`` then drains remaining sets. Records already in the
    manifest are de-duplicated, so replaying a page after a crash is safe.
    An expired token (``badResumptionToken``) restarts the current set.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    state = read_state(out_dir)
    if state is not None:
        stored = state.get("sets") or []
        if set(stored) != set(sets) or state.get("metadata_prefix") != metadata_prefix:
            raise HarvestStateMismatchError(stored, list(sets),
                                       state.get("metadata_prefix"), metadata_prefix)
    if state and state.get("done"):
        return HarvestReport(
            harvested=len(read_records(out_dir)),
            complete_list_size=state.get("complete_list_size"),
            resumed=True, already_done=True,
        )

    resumed = state is not None
    queue: list[str] = list(state.get("pending_sets", [])) if state else list(sets)
    current: str | None = state.get("current_set") if state else None
    token: str = (state.get("resumption_token") or "") if state else ""
    harvested: int = int(state.get("harvested", 0)) if state else 0
    set_sizes: dict[str, int] = dict(state.get("set_sizes") or {}) if state else {}

    seen = _seen_ids(out_dir)

    def _save(done: bool = False) -> None:
        write_state(out_dir, {
            "sets": list(sets), "metadata_prefix": metadata_prefix,
            "pending_sets": list(queue), "current_set": current,
            "resumption_token": token or "", "harvested": harvested,
            "set_sizes": set_sizes,
            "complete_list_size": sum(set_sizes.values()) or None,
            "done": done,
        })

    client = httpx.Client(
        timeout=60.0, follow_redirects=True, transport=transport,
        headers={"User-Agent": _user_agent()},
    )
    first_request = True
    try:
        while current is not None or queue:
            if current is None:
                current = queue.pop(0)
                token = ""

            if token:
                params = {"verb": "ListRecords", "resumptionToken": token}
            else:
                params = {"verb": "ListRecords", "set": current,
                          "metadataPrefix": metadata_prefix}

            if not first_request:
                time.sleep(delay_s)
            first_request = False

            root = ET.fromstring(_get_with_retry(client, OAI_ENDPOINT, params, delay_s).text)

            err = root.find(f"{OAI_NS}error")
            if err is not None:
                code = err.get("code", "")
                if code == "noRecordsMatch":
                    current, token = None, ""
                elif code == "badResumptionToken":
                    token = ""  # expired -> restart current set (seen de-dupes)
                else:
                    raise RuntimeError(f"OAI error {code}: {(err.text or '').strip()}")
                _save()
                continue

            list_records = root.find(f"{OAI_NS}ListRecords")
            if list_records is None:
                current, token = None, ""
                _save()
                continue

            page: list[ArxivRecord] = []
            for rec_el in list_records.findall(f"{OAI_NS}record"):
                rec = parse_record(rec_el)
                if rec is None or rec.arxiv_id in seen:
                    continue
                seen.add(rec.arxiv_id)
                page.append(rec)
            append_records(out_dir, page)
            harvested += len(page)

            token_el = list_records.find(f"{OAI_NS}resumptionToken")
            token = (token_el.text or "").strip() if token_el is not None else ""
            if token_el is not None and token_el.get("completeListSize"):
                set_sizes[current] = int(token_el.get("completeListSize"))
            logger.info("arxiv harvest set=%s +%d total=%d", current, len(page), harvested)

            if not token:
                current = None  # set exhausted
            _save()

        _save(done=True)
        return HarvestReport(
            harvested=harvested,
            complete_list_size=sum(set_sizes.values()) or None,
            resumed=resumed, already_done=False,
        )
    finally:
        client.close()


# --- phase 2: download (bounded-async PDF fetch) --------------------------

def download_all(
    out_dir: Path,
    *,
    concurrency: int = _PDF_CONCURRENCY,
    rate: float = _PDF_RATE,
    timeout: float = 120.0,
    transport: httpx.BaseTransport | None = None,
) -> DownloadReport:
    """Download every pending PDF in the manifest (resumable).

    A record is skipped when its PDF already exists -- that file existence,
    not the stored ``status``, is the resume source of truth. Downloads run
    under ``Semaphore(concurrency)`` + ``AsyncLimiter(rate)``, defaulting to
    arXiv's PDF-friendly ~4 req/s; transient 429/503 responses back off and
    retry rather than failing the record. Status checkpoints merge by id
    against the on-disk manifest, so records appended by a concurrent
    ``identify`` are preserved rather than clobbered.
    """
    out_dir = Path(out_dir)
    targets: list[ArxivRecord] = []
    existing_done: dict[str, str] = {}
    for rec in read_records(out_dir):
        if (out_dir / rec.pdf_filename).exists():
            existing_done[rec.arxiv_id] = "done"
        else:
            targets.append(rec)
    skipped = len(existing_done)

    if not targets:
        _merge_statuses(out_dir, existing_done)
        return DownloadReport(downloaded=0, skipped=skipped, failed=[])

    report = asyncio.run(
        _download_async(out_dir, targets, existing_done, concurrency, rate, timeout, transport)
    )
    report.skipped = skipped
    return report


def _merge_statuses(out_dir: Path, status_by_id: dict[str, str]) -> None:
    """Apply status updates to the current on-disk manifest, atomically.

    Re-reads the manifest so records appended since the download started
    (e.g. by a concurrent ``identify``) survive the checkpoint rewrite.
    """
    if not status_by_id:
        return
    records = read_records(out_dir)
    changed = False
    for rec in records:
        new = status_by_id.get(rec.arxiv_id)
        if new and rec.status != new:
            rec.status = new
            changed = True
    if changed:
        write_records(out_dir, records)


async def _download_async(
    out_dir: Path,
    targets: list[ArxivRecord],
    existing_done: dict[str, str],
    concurrency: int,
    rate: float,
    timeout: float,
    transport: httpx.BaseTransport | None,
) -> DownloadReport:
    limiter = AsyncLimiter(max(rate, 0.1), 1)
    semaphore = asyncio.Semaphore(max(concurrency, 1))
    failed: list[dict] = []
    status_by_id: dict[str, str] = dict(existing_done)
    downloaded = 0

    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True,
        headers={"User-Agent": _user_agent()}, transport=transport,
    ) as client:

        @with_limiter(limiter)
        @with_semaphore(semaphore)
        async def _fetch(rec: ArxivRecord) -> None:
            resp = await _aget_with_retry(client, rec.pdf_url)
            dest = out_dir / rec.pdf_filename
            tmp = dest.with_suffix(".pdf.part")
            tmp.write_bytes(resp.content)
            tmp.replace(dest)

        async def _run(rec: ArxivRecord) -> None:
            nonlocal downloaded
            try:
                await _fetch(rec)
                status_by_id[rec.arxiv_id] = "done"
                downloaded += 1
            except Exception as exc:  # noqa: BLE001 -- per-paper failure is non-fatal
                status_by_id[rec.arxiv_id] = "failed"
                failed.append({"arxiv_id": rec.arxiv_id, "error": str(exc)})

        for i in range(0, len(targets), _DOWNLOAD_BATCH):
            await asyncio.gather(*(_run(rec) for rec in targets[i:i + _DOWNLOAD_BATCH]))
            _merge_statuses(out_dir, status_by_id)  # checkpoint, append-safe

    return DownloadReport(downloaded=downloaded, skipped=0, failed=failed)


def status_summary(out_dir: Path) -> dict:
    """Tally manifest records by on-disk presence for monitoring/resume."""
    out_dir = Path(out_dir)
    records = read_records(out_dir)
    state = read_state(out_dir) or {}
    done = pending = failed = 0
    for rec in records:
        if (out_dir / rec.pdf_filename).exists():
            done += 1
        elif rec.status == "failed":
            failed += 1
        else:
            pending += 1
    return {
        "total": len(records), "done": done, "pending": pending, "failed": failed,
        "harvest_done": bool(state.get("done")),
        "complete_list_size": state.get("complete_list_size"),
    }


# --- scout (Query API discovery) ------------------------------------------

def scout(
    query: str,
    *,
    max_results: int = 200,
    transport: httpx.BaseTransport | None = None,
) -> ScoutReport:
    """Sample a free-text Query-API search and tally primary categories.

    The Query API can't drive an exhaustive harvest (it caps at 30k), but a
    small top-relevance sample reveals which categories a topic occupies --
    feed the resulting setspecs back into ``identify``. One request.
    """
    client = httpx.Client(
        timeout=60.0, follow_redirects=True, transport=transport,
        headers={"User-Agent": _user_agent()},
    )
    try:
        resp = _get_with_retry(client, QUERY_ENDPOINT, {
            "search_query": query, "start": 0, "max_results": max_results,
        }, _OAI_DELAY_S)
        root = ET.fromstring(resp.text)
    finally:
        client.close()

    total_el = root.find(f"{OPENSEARCH_NS}totalResults")
    total = int(total_el.text) if total_el is not None and total_el.text else 0

    counts: dict[str, int] = {}
    sampled = 0
    for entry in root.findall(f"{ATOM_NS}entry"):
        prim = entry.find(f"{ARXIV_ATOM_NS}primary_category")
        cat = prim.get("term") if prim is not None else ""
        if not cat:
            first = entry.find(f"{ATOM_NS}category")
            cat = first.get("term") if first is not None else "(unknown)"
        counts[cat] = counts.get(cat, 0) + 1
        sampled += 1

    histogram = [
        {"category": cat, "count": n, "setspec": _safe_setspec(cat)}
        for cat, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return ScoutReport(query=query, total_results=total, sampled=sampled,
                       primary_histogram=histogram)
