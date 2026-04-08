"""Prompt registry for wikify_simple role templates."""

from .registry import (
    Prompt,
    available_artifact_templates,
    available_field_guides,
    compose_writer_prompt,
    load_artifact_template,
    load_field_guide,
    load_prompt,
    load_style_guide,
)

__all__ = [
    "Prompt",
    "available_artifact_templates",
    "available_field_guides",
    "compose_writer_prompt",
    "load_artifact_template",
    "load_field_guide",
    "load_prompt",
    "load_style_guide",
]
