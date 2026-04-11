"""Vision helpers for multimodal LLM calls.

Sends images to vision-capable models for
description, data extraction, or table parsing.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

from wikify.core.config import settings
from wikify.core.llm.client import complete

logger = logging.getLogger(__name__)

# Default to a configurable vision model, falling back to the fast text tier.
_DEFAULT_VISION_MODEL = settings.vision_model or settings.llm_fast_model


def describe_figure(
    image_path: str | Path,
    caption: str = "",
    paper_title: str = "",
    section: str = "",
    model: str | None = None,
) -> dict:
    """Send a figure to a vision model and get structured extraction.

    Args:
        image_path: Path to the image file.
        caption: Figure caption text (may be empty).
        paper_title: Title of the source paper.
        section: Section where the figure appears.
        model: LLM model to use (defaults to the fast tier).

    Returns:
        Dict with keys: description, data_points, concepts, values
    """
    path = Path(image_path)
    b64_data, media_type = _load_image_as_base64(path)
    resolved_model = model or _DEFAULT_VISION_MODEL

    context_parts = []
    if caption:
        context_parts.append(f"Figure caption: {caption}")
    if paper_title:
        context_parts.append(f"Paper: {paper_title}")
    if section:
        context_parts.append(f"Section: {section}")
    context_block = "\n".join(context_parts)

    prompt = f"""You are extracting knowledge from a scientific figure.

{context_block}

Extract as JSON:
{{
  "description": "1-2 sentence description of what the figure shows",
  "data_points": ["key data point 1", "key data point 2"],
  "concepts": ["concept1", "concept2"],
  "values": ["numerical value 1 with unit", "numerical value 2 with unit"]
}}"""

    messages: list[dict] = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{b64_data}"},
                },
                {"type": "text", "text": prompt},
            ],
        }
    ]

    raw = complete(messages=messages, model=resolved_model, max_tokens=1024)  # type: ignore[arg-type]
    return _parse_json_response(
        raw, default_keys=["description", "data_points", "concepts", "values"]
    )


def extract_table_from_image(
    image_path: str | Path,
    caption: str = "",
    model: str | None = None,
) -> dict:
    """Send a table image to a vision model for structured extraction.

    Returns:
        Dict with keys: markdown_table, headers, data_points
    """
    path = Path(image_path)
    b64_data, media_type = _load_image_as_base64(path)
    resolved_model = model or _DEFAULT_VISION_MODEL

    caption_line = f"\nTable caption: {caption}" if caption else ""

    prompt = f"""Extract the table content as a markdown table. Include all headers and rows.
Preserve chemical formulas, numerical values with units, and any special notation.{caption_line}

Return JSON:
{{
  "markdown_table": "| col1 | col2 |\\n| --- | --- |\\n| val | val |",
  "headers": ["col1", "col2"],
  "data_points": ["key finding from data"]
}}"""

    messages: list[dict] = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{b64_data}"},
                },
                {"type": "text", "text": prompt},
            ],
        }
    ]

    raw = complete(messages=messages, model=resolved_model, max_tokens=2048)  # type: ignore[arg-type]
    return _parse_json_response(raw, default_keys=["markdown_table", "headers", "data_points"])


def view_figure(figure_id: str) -> dict:
    """Load a figure for LLM viewing during article writing.

    Returns:
        Dict with: image_base64, caption, llm_description, paper_title, media_type
    """
    from sqlmodel import select

    from wikify.core.store.db import get_session
    from wikify.core.store.models import Figure, Paper

    with get_session() as session:
        figure = session.exec(select(Figure).where(Figure.id == figure_id)).first()
        if not figure:
            return {"error": f"Figure {figure_id} not found"}

        paper_title = ""
        if figure.paper_id:
            paper = session.exec(select(Paper).where(Paper.id == figure.paper_id)).first()
            if paper:
                paper_title = paper.title

    result: dict = {
        "caption": figure.caption or "",
        "llm_description": figure.llm_description or "",
        "paper_title": paper_title,
        "media_type": f"image/{figure.format}",
    }

    # Try to load image for base64 encoding
    image_path = _resolve_figure_path(figure)
    if image_path and image_path.exists():
        b64_data, media_type = _load_image_as_base64(image_path)
        result["image_base64"] = b64_data
        result["media_type"] = media_type
    else:
        result["image_base64"] = ""

    return result


def _load_image_as_base64(path: Path) -> tuple[str, str]:
    """Read an image file and return (base64_string, media_type).

    media_type is e.g. "image/png", "image/jpeg".

    Raises:
        FileNotFoundError: If the image file does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {path}")

    suffix = path.suffix.lower()
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
    }
    media_type = media_types.get(suffix, "image/png")

    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return b64, media_type


def _resolve_figure_path(figure) -> Path | None:
    """Resolve the filesystem path for a Figure record."""
    if figure.image_path:
        # image_path may be relative to data dir or absolute
        candidate = Path(figure.image_path)
        if candidate.is_absolute() and candidate.exists():
            return candidate
        # Try relative to figures_dir
        relative = settings.figures_dir / figure.image_path
        if relative.exists():
            return relative

    # Fall back to content-addressed layout: {hash[:2]}/{hash[2:4]}/{hash}.{ext}
    fig_hash = figure.id
    ext = figure.format or "png"
    content_addressed = settings.figures_dir / fig_hash[:2] / fig_hash[2:4] / f"{fig_hash}.{ext}"
    if content_addressed.exists():
        return content_addressed

    return None


def _parse_json_response(raw: str, default_keys: list[str]) -> dict:
    """Parse a JSON response from the LLM, with fallback for malformed output."""
    text = raw.strip()

    # Strip markdown fences
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3].rstrip()

    # Try direct parse
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Find JSON boundaries
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            result = json.loads(text[start : end + 1])
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    # Return empty defaults if parsing failed
    logger.warning("Could not parse JSON from vision response: %s", text[:200])
    return {k: [] if k != "description" and k != "markdown_table" else "" for k in default_keys}
