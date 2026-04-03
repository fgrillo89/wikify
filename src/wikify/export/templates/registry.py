"""Template registry: track, import, and discover DOCX/LaTeX templates via SQLite.

Templates come from three sources:
1. Publisher downloads — user downloads from URL, imports via CLI
2. User-supplied — any .docx the user provides (e.g., their own paper)
3. Built-in fallbacks — programmatic templates in templates/docx/

All imported templates are tracked in the JournalTemplate SQLite table.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from wikify.store.models import JournalTemplate

console = Console()
logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent
_DOCX_DIR = _TEMPLATES_DIR / "docx"
_LATEX_DIR = _TEMPLATES_DIR / "latex"

# Known publisher template download URLs.
# Some are behind Cloudflare — user must download manually via browser.
KNOWN_SOURCES: dict[str, dict[str, str]] = {
    "wiley_afm": {
        "name": "Advanced Functional Materials",
        "publisher": "Wiley",
        "url": (
            "https://advanced.onlinelibrary.wiley.com/pb-assets/assets/"
            "vch/msp/Article-template-1749710971070.docx"
        ),
        "notes": "Wiley VCH article template for Advanced Portfolio journals",
    },
    "nature": {
        "name": "Nature Manuscript",
        "publisher": "Springer Nature",
        "url": "",
        "notes": "Download from nature.com submission guidelines",
    },
    "elsevier": {
        "name": "Elsevier Article",
        "publisher": "Elsevier",
        "url": "",
        "notes": "LaTeX preferred. DOCX from Elsevier Author Hub",
    },
    "acs": {
        "name": "ACS Manuscript",
        "publisher": "American Chemical Society",
        "url": "",
        "notes": "Download from pubs.acs.org author templates",
    },
    "ieee": {
        "name": "IEEE Manuscript",
        "publisher": "IEEE",
        "url": "",
        "notes": "Download from template-selector.ieee.org",
    },
    "arxiv": {
        "name": "arXiv Preprint",
        "publisher": "arXiv",
        "url": "",
        "notes": "Standard LaTeX article class. DOCX fallback uses Times 11pt",
    },
}


def _sanitize_id(name: str) -> str:
    """Convert a name to a safe ID."""
    return re.sub(r"[^a-z0-9_]", "_", name.lower().strip()).strip("_")


def import_template(
    source_path: Path,
    name: str = "",
    publisher: str = "",
    source_url: str = "",
    notes: str = "",
) -> JournalTemplate:
    """Import a .docx or .cls file into the template registry.

    Copies the file into the templates directory and creates a SQLite record.
    """
    from wikify.store.db import get_session
    from wikify.store.models import JournalTemplate

    source_path = Path(source_path).resolve()
    if not source_path.exists():
        msg = f"File not found: {source_path}"
        raise FileNotFoundError(msg)

    display_name = name or source_path.stem
    template_id = _sanitize_id(display_name)

    # Determine type and destination
    suffix = source_path.suffix.lower()
    if suffix == ".docx":
        dest_dir = _DOCX_DIR
        file_type = "docx"
    elif suffix in (".cls", ".sty", ".tex"):
        dest_dir = _LATEX_DIR
        file_type = "latex"
    else:
        msg = f"Unsupported template type: {suffix}. Use .docx, .cls, .sty, or .tex"
        raise ValueError(msg)

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{template_id}{suffix}"
    shutil.copy2(source_path, dest)

    # Upsert into SQLite
    template = JournalTemplate(
        id=template_id,
        name=display_name,
        publisher=publisher,
        file_path=str(dest.resolve()),
        file_type=file_type,
        source_url=source_url,
        notes=notes,
    )
    with get_session() as session:
        existing = session.get(JournalTemplate, template_id)
        if existing:
            existing.file_path = template.file_path
            existing.publisher = publisher or existing.publisher
            existing.source_url = source_url or existing.source_url
            existing.notes = notes or existing.notes
            session.add(existing)
        else:
            session.add(template)
        session.commit()

    console.print(f"[green]Template imported:[/green] {display_name} ({dest.name})")
    return template


def get_template_path(name: str) -> Path | None:
    """Find a template by name or ID. Checks SQLite first, then filesystem fallbacks."""
    if not name:
        return None

    # Direct path (user-supplied absolute or relative)
    direct = Path(name)
    if direct.suffix in (".docx", ".cls", ".sty", ".tex"):
        if direct.is_absolute() and direct.exists():
            return direct
        cwd_path = Path.cwd() / name
        if cwd_path.exists():
            return cwd_path

    # Check SQLite registry
    try:
        from sqlmodel import select

        from wikify.store.db import get_session
        from wikify.store.models import JournalTemplate

        with get_session() as session:
            # By ID
            template = session.get(JournalTemplate, _sanitize_id(name))
            if template and Path(template.file_path).exists():
                return Path(template.file_path)

            # By name (fuzzy)
            all_templates = session.exec(select(JournalTemplate)).all()
            name_lower = name.lower()
            for t in all_templates:
                if name_lower in t.name.lower() or name_lower in t.id:
                    p = Path(t.file_path)
                    if p.exists():
                        return p
    except Exception:
        logger.debug("SQLite template lookup failed", exc_info=True)

    # Filesystem fallback (built-in templates dir)
    for candidate_name in [name, _sanitize_id(name)]:
        for suffix in [".docx", ""]:
            candidate = _DOCX_DIR / f"{candidate_name}{suffix}"
            if candidate.exists():
                return candidate

    return None


def list_templates() -> list[dict[str, str]]:
    """List all tracked templates from SQLite + filesystem."""
    templates: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    # SQLite tracked templates
    try:
        from sqlmodel import select

        from wikify.store.db import get_session
        from wikify.store.models import JournalTemplate

        with get_session() as session:
            for t in session.exec(select(JournalTemplate)).all():
                exists = Path(t.file_path).exists()
                templates.append(
                    {
                        "id": t.id,
                        "name": t.name,
                        "publisher": t.publisher,
                        "type": t.file_type,
                        "path": t.file_path,
                        "source": "imported",
                        "status": "ok" if exists else "MISSING",
                    }
                )
                seen_ids.add(t.id)
    except Exception:
        pass

    # Filesystem fallbacks not yet in SQLite
    if _DOCX_DIR.exists():
        for f in sorted(_DOCX_DIR.glob("*.docx")):
            fid = f.stem
            if fid not in seen_ids:
                templates.append(
                    {
                        "id": fid,
                        "name": fid.replace("_", " ").title(),
                        "publisher": "",
                        "type": "docx",
                        "path": str(f),
                        "source": "built-in",
                        "status": "ok",
                    }
                )

    return templates


def download_template(template_id: str) -> Path | None:
    """Download a known publisher template using a stealth browser.

    Uses patchright (patched Playwright) to bypass Cloudflare protection.
    The browser runs headless — no visible window.
    """
    import time

    info = KNOWN_SOURCES.get(template_id)
    if not info or not info["url"]:
        console.print(f"[red]No download URL for '{template_id}'[/red]")
        console.print("Available templates with URLs:")
        for tid, src in KNOWN_SOURCES.items():
            if src["url"]:
                console.print(f"  {tid}: {src['name']}")
        return None

    url = info["url"]
    _DOCX_DIR.mkdir(parents=True, exist_ok=True)
    dest = _DOCX_DIR / f"{template_id}.docx"

    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        console.print("[red]patchright not installed. Run: uv add scrapling[all][/red]")
        return None

    console.print(f"[dim]Downloading {info['name']} template...[/dim]")

    with sync_playwright() as p:
        # Try headless first, fall back to non-headless if CF blocks
        for headless in (True, False):
            try:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context(accept_downloads=True)
                page = context.new_page()

                # Visit main site first to get cookies
                base_url = "/".join(url.split("/")[:3])
                page.goto(base_url, wait_until="domcontentloaded", timeout=20000)
                time.sleep(5)

                # Set up download handler
                downloaded = [False]

                def on_download(dl):
                    dl.save_as(str(dest))
                    downloaded[0] = True

                page.on("download", on_download)

                try:
                    page.goto(url, wait_until="load", timeout=15000)
                except Exception:
                    pass  # "Download is starting" error is expected

                # Wait for download to complete
                for _ in range(10):
                    if downloaded[0]:
                        break
                    time.sleep(1)

                browser.close()

                if dest.exists() and dest.stat().st_size > 1000:
                    with open(dest, "rb") as f:
                        if f.read(2) == b"PK":
                            console.print(
                                f"[green]Downloaded:[/green] {dest.name}"
                                f" ({dest.stat().st_size:,} bytes)"
                            )
                            # Auto-import into SQLite
                            import_template(
                                dest,
                                name=template_id,
                                publisher=info["publisher"],
                                source_url=url,
                                notes=info["notes"],
                            )
                            return dest
                # If headless failed, try non-headless
                if headless:
                    continue
            except Exception as e:
                logger.warning("Download attempt failed: %s", e)
                if not headless:
                    break

    console.print("[red]Download failed. Please download manually:[/red]")
    console.print(f"  URL: {url}")
    console.print(f'  Then: wikify templates import <file> --name "{template_id}"')
    return None


def show_download_instructions() -> None:
    """Print instructions for downloading publisher templates."""
    console.print("\n[bold]Publisher templates:[/bold]\n")
    for tid, info in KNOWN_SOURCES.items():
        console.print(f"  [cyan]{info['name']}[/cyan] ({info['publisher']})")
        if info["url"]:
            console.print(f"    Auto-download: wikify templates download {tid}")
        else:
            console.print(f"    {info['notes']}")
        console.print(f'    Manual import: wikify templates import <file> --name "{tid}"\n')


def extract_styles(docx_path: Path) -> dict[str, str]:
    """Extract all defined style names from a .docx file."""
    from docx import Document

    doc = Document(str(docx_path))
    styles: dict[str, str] = {}
    for style in doc.styles:
        if style.name and style.type is not None:
            style_type = str(style.type).split(".")[-1] if style.type else "unknown"
            styles[style.name] = style_type
    return styles


def suggest_style_map(docx_path: Path) -> dict[str, str]:
    """Suggest a style map for a template based on its defined styles."""
    styles = extract_styles(docx_path)
    style_names = set(styles.keys())
    mapping: dict[str, str] = {}

    for role, candidates in [
        ("body", ["Normal", "Body Text", "Body", "Text"]),
        ("title", ["Title", "Paper Title", "Article Title"]),
        ("abstract", ["Abstract", "Abstract Text"]),
        ("references", ["Bibliography", "Reference", "References", "Endnote Text"]),
    ]:
        for c in candidates:
            if c in style_names:
                mapping[role] = c
                break
        if role not in mapping:
            mapping[role] = "Normal" if role != "title" else "Title"

    for level in range(1, 4):
        key = f"heading{level}"
        standard = f"Heading {level}"
        if standard in style_names:
            mapping[key] = standard
        else:
            mapping[key] = standard

    return mapping
