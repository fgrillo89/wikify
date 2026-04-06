"""Enrich wiki figures with LLM-generated descriptions.

Called during epoch Pass 1 (after text extraction) to add visual
understanding to extracted figures. Stores descriptions in
Figure.llm_description for searchability and article enrichment.
"""

from __future__ import annotations

import json
import logging

from sqlmodel import select

from wikify.llm.vision import _resolve_figure_path, describe_figure, extract_table_from_image
from wikify.store.db import get_session
from wikify.store.models import Figure, Paper

logger = logging.getLogger(__name__)

# Figures with captions longer than this are self-explanatory
_CAPTION_WORD_THRESHOLD = 50

# Minimum image dimensions to bother describing
_MIN_DIMENSION_PX = 200


def enrich_paper_figures(
    paper_id: str,
    model: str | None = None,
    force: bool = False,
) -> int:
    """Enrich all figures for a paper with LLM descriptions.

    Args:
        paper_id: Paper to process.
        model: Vision model to use (defaults to fast tier).
        force: Re-describe even if llm_description is already set.

    Returns:
        Number of figures enriched.

    Skips:
    - Figures with existing llm_description (unless force=True)
    - Images smaller than 200x200px
    - Figures where caption alone is >50 words (caption is sufficient)
    """
    enriched_count = 0

    with get_session() as session:
        paper = session.exec(select(Paper).where(Paper.id == paper_id)).first()
        paper_title = paper.title if paper else ""

        figures = session.exec(select(Figure).where(Figure.paper_id == paper_id)).all()

        for figure in figures:
            # Skip if already described (unless forced)
            if figure.llm_description and not force:
                logger.debug("Skipping %s: already has description", figure.id)
                continue

            # Skip small images (likely icons or artifacts)
            if figure.width_px < _MIN_DIMENSION_PX or figure.height_px < _MIN_DIMENSION_PX:
                logger.debug(
                    "Skipping %s: too small (%dx%d)",
                    figure.id,
                    figure.width_px,
                    figure.height_px,
                )
                continue

            # Skip if caption is sufficiently descriptive
            caption = figure.caption or ""
            if len(caption.split()) > _CAPTION_WORD_THRESHOLD:
                logger.debug("Skipping %s: caption is %d words", figure.id, len(caption.split()))
                continue

            # Resolve image path -- skip if file not found
            image_path = _resolve_figure_path(figure)
            if not image_path or not image_path.exists():
                logger.warning("Skipping %s: image file not found", figure.id)
                continue

            result = describe_figure(
                image_path=image_path,
                caption=caption,
                paper_title=paper_title,
                section=figure.section_path or "",
                model=model,
            )

            figure.llm_description = json.dumps(result, ensure_ascii=False)
            session.add(figure)
            enriched_count += 1
            logger.info("Enriched figure %s: %s", figure.id, result.get("description", "")[:80])

        session.commit()

    return enriched_count


def enrich_table_images(
    paper_id: str,
    model: str | None = None,
) -> int:
    """For table-type figures that have no extracted data, use vision to extract.

    This handles scanned PDFs where find_tables() couldn't extract structured data
    but the image was captured.

    Returns:
        Number of table images enriched.
    """
    enriched_count = 0

    with get_session() as session:
        figures = session.exec(select(Figure).where(Figure.paper_id == paper_id)).all()

        for figure in figures:
            # Only process figures tagged as tables with no extracted data
            tags = []
            try:
                tags = json.loads(figure.tags) if figure.tags else []
            except (json.JSONDecodeError, TypeError):
                pass

            if "table" not in tags:
                continue

            # Skip if already has extracted data
            if figure.extracted_data:
                continue

            image_path = _resolve_figure_path(figure)
            if not image_path or not image_path.exists():
                logger.warning("Skipping table %s: image file not found", figure.id)
                continue

            result = extract_table_from_image(
                image_path=image_path,
                caption=figure.caption or "",
                model=model,
            )

            figure.extracted_data = json.dumps(result, ensure_ascii=False)
            session.add(figure)
            enriched_count += 1
            logger.info(
                "Extracted table from %s: %d headers",
                figure.id,
                len(result.get("headers", [])),
            )

        session.commit()

    return enriched_count
