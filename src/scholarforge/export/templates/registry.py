"""Template registry: discover, download, and manage DOCX/LaTeX templates.

Handles three template sources:
1. Built-in templates (shipped with ScholarForge in templates/docx/)
2. Downloaded publisher templates (fetched via URL and cached)
3. User-supplied templates (any .docx file the user provides)
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from rich.console import Console

console = Console()
logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent
_DOCX_DIR = _TEMPLATES_DIR / "docx"
_LATEX_DIR = _TEMPLATES_DIR / "latex"

# Known publisher template URLs (freely available author guidelines).
# These are stable URLs that resolve to .docx or .zip files.
# Updated manually when publishers change their URLs.
KNOWN_TEMPLATES: dict[str, dict[str, str]] = {
    "wiley": {
        "description": "Wiley generic author template",
        "url": "",  # Requires manual download from Wiley Author Services
        "filename": "wiley_generic.docx",
    },
    "elsevier": {
        "description": "Elsevier single-column article template",
        "url": "",  # Available via Elsevier Author Hub
        "filename": "elsevier_generic.docx",
    },
    "nature": {
        "description": "Nature/Springer manuscript template",
        "url": "",  # Available via nature.com/documents
        "filename": "nature_manuscript.docx",
    },
    "acs": {
        "description": "ACS manuscript template",
        "url": "",  # Available via ACS Paragon Plus
        "filename": "acs_manuscript.docx",
    },
    "ieee": {
        "description": "IEEE conference/journal template",
        "url": "",  # Available via IEEE Author Center
        "filename": "ieee_manuscript.docx",
    },
}


def get_template_path(name: str) -> Path | None:
    """Find a template by name. Searches built-in, then downloaded, then user dir.

    Args:
        name: Template filename (e.g., "wiley_adv_funct_mater.docx") or
              a full path to a user-supplied .docx file.

    Returns:
        Path to the template file, or None if not found.
    """
    if not name:
        return None

    # Check if it's a direct path to a user-supplied file
    user_path = Path(name)
    if user_path.is_absolute() and user_path.exists():
        return user_path

    # Check relative to CWD
    cwd_path = Path.cwd() / name
    if cwd_path.exists():
        return cwd_path

    # Check built-in DOCX templates
    builtin = _DOCX_DIR / name
    if builtin.exists():
        return builtin

    # Check with .docx extension added
    if not name.endswith(".docx"):
        builtin_ext = _DOCX_DIR / f"{name}.docx"
        if builtin_ext.exists():
            return builtin_ext

    return None


def get_latex_template_path(name: str) -> Path | None:
    """Find a LaTeX template/class file by name."""
    if not name:
        return None

    user_path = Path(name)
    if user_path.is_absolute() and user_path.exists():
        return user_path

    builtin = _LATEX_DIR / name
    if builtin.exists():
        return builtin

    if not name.endswith((".cls", ".sty", ".tex")):
        for ext in (".cls", ".sty", ".tex"):
            candidate = _LATEX_DIR / f"{name}{ext}"
            if candidate.exists():
                return candidate

    return None


def list_templates() -> list[dict[str, str]]:
    """List all available templates (built-in + downloaded)."""
    templates: list[dict[str, str]] = []

    # Built-in DOCX templates
    if _DOCX_DIR.exists():
        for f in sorted(_DOCX_DIR.glob("*.docx")):
            templates.append(
                {
                    "name": f.stem,
                    "type": "docx",
                    "path": str(f),
                    "source": "built-in",
                }
            )

    # LaTeX templates
    if _LATEX_DIR.exists():
        for f in sorted(_LATEX_DIR.glob("*")):
            if f.suffix in (".cls", ".sty", ".tex"):
                templates.append(
                    {
                        "name": f.stem,
                        "type": "latex",
                        "path": str(f),
                        "source": "built-in",
                    }
                )

    return templates


def import_template(source_path: Path, name: str = "") -> Path:
    """Import a user-supplied .docx file as a reusable template.

    Copies the file into the built-in templates directory so it can be
    referenced by name in journal profiles.

    Args:
        source_path: Path to the user's .docx file.
        name: Optional name for the template. Defaults to the filename stem.

    Returns:
        Path to the imported template.
    """
    source_path = Path(source_path)
    if not source_path.exists():
        msg = f"Template file not found: {source_path}"
        raise FileNotFoundError(msg)

    _DOCX_DIR.mkdir(parents=True, exist_ok=True)

    stem = name or source_path.stem
    # Sanitize the name
    safe_name = stem.replace(" ", "_").replace("-", "_").lower()
    dest = _DOCX_DIR / f"{safe_name}.docx"

    shutil.copy2(source_path, dest)
    console.print(f"[green]Template imported:[/green] {dest.name}")
    return dest


def extract_styles(docx_path: Path) -> dict[str, str]:
    """Extract all defined style names from a .docx file.

    Useful for discovering what styles a template provides so the
    exporter can map to them.
    """
    from docx import Document

    doc = Document(str(docx_path))
    styles: dict[str, str] = {}
    for style in doc.styles:
        if style.name and style.type is not None:
            style_type = str(style.type).split(".")[-1] if style.type else "unknown"
            styles[style.name] = style_type
    return styles


def suggest_style_map(docx_path: Path) -> dict[str, str]:
    """Suggest a style map for a template based on its defined styles.

    Looks for common style name patterns and maps them to ScholarForge roles.
    """
    styles = extract_styles(docx_path)
    style_names = set(styles.keys())

    mapping: dict[str, str] = {}

    # Body text
    for candidate in ["Normal", "Body Text", "Body", "Text"]:
        if candidate in style_names:
            mapping["body"] = candidate
            break
    if "body" not in mapping:
        mapping["body"] = "Normal"

    # Title
    for candidate in ["Title", "Paper Title", "Article Title"]:
        if candidate in style_names:
            mapping["title"] = candidate
            break
    if "title" not in mapping:
        mapping["title"] = "Title"

    # Headings
    for level in range(1, 4):
        key = f"heading{level}"
        standard = f"Heading {level}"
        if standard in style_names:
            mapping[key] = standard
        else:
            # Try custom patterns
            for candidate in [f"Heading{level}", f"H{level}", f"Section {level}"]:
                if candidate in style_names:
                    mapping[key] = candidate
                    break
            if key not in mapping:
                mapping[key] = standard  # default even if not in template

    # Abstract
    for candidate in ["Abstract", "Abstract Text"]:
        if candidate in style_names:
            mapping["abstract"] = candidate
            break
    if "abstract" not in mapping:
        mapping["abstract"] = mapping.get("body", "Normal")

    # References
    for candidate in ["Bibliography", "Reference", "References", "Endnote Text"]:
        if candidate in style_names:
            mapping["references"] = candidate
            break
    if "references" not in mapping:
        mapping["references"] = mapping.get("body", "Normal")

    return mapping
