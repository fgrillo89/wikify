"""Tests for the vision module (LLM calls are mocked)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wikify.core.llm.vision import (
    _load_image_as_base64,
    _parse_json_response,
    describe_figure,
    extract_table_from_image,
    view_figure,
)

# ── _load_image_as_base64 ────────────────────────────────────────────────────


class TestLoadImageAsBase64:
    def test_png_encoding(self, tmp_path: Path) -> None:
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)  # minimal PNG header

        b64, media_type = _load_image_as_base64(img)

        assert media_type == "image/png"
        assert len(b64) > 0
        # Verify round-trip
        import base64

        decoded = base64.b64decode(b64)
        assert decoded == img.read_bytes()

    def test_jpeg_encoding(self, tmp_path: Path) -> None:
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)

        b64, media_type = _load_image_as_base64(img)

        assert media_type == "image/jpeg"
        assert len(b64) > 0

    def test_jpeg_extension(self, tmp_path: Path) -> None:
        img = tmp_path / "photo.jpeg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)

        _, media_type = _load_image_as_base64(img)
        assert media_type == "image/jpeg"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.png"
        with pytest.raises(FileNotFoundError, match="Image not found"):
            _load_image_as_base64(missing)

    def test_unknown_extension_defaults_to_png(self, tmp_path: Path) -> None:
        img = tmp_path / "file.xyz"
        img.write_bytes(b"\x00" * 8)

        _, media_type = _load_image_as_base64(img)
        assert media_type == "image/png"


# ── describe_figure ──────────────────────────────────────────────────────────


class TestDescribeFigure:
    @patch("wikify.core.llm.vision.complete")
    def test_builds_correct_message_format(self, mock_complete: MagicMock, tmp_path: Path) -> None:
        """Verify that describe_figure builds multimodal messages correctly."""
        img = tmp_path / "fig.png"
        img.write_bytes(b"\x89PNG" + b"\x00" * 20)

        mock_complete.return_value = json.dumps(
            {
                "description": "XRD pattern showing crystalline HfO2",
                "data_points": ["2theta peak at 28.5 degrees"],
                "concepts": ["HfO2", "XRD"],
                "values": ["28.5 degrees"],
            }
        )

        result = describe_figure(
            image_path=img,
            caption="XRD of HfO2 thin film",
            paper_title="ALD of HfO2",
            section="Results",
        )

        # Check the message structure
        call_args = mock_complete.call_args
        messages = (
            call_args.kwargs.get("messages") or call_args[1].get("messages") or call_args[0][0]
        )
        assert len(messages) == 1
        content = messages[0]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "image_url"
        assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
        assert content[1]["type"] == "text"
        assert "XRD of HfO2 thin film" in content[1]["text"]

        # Check parsed result
        assert result["description"] == "XRD pattern showing crystalline HfO2"
        assert "HfO2" in result["concepts"]

    @patch("wikify.core.llm.vision.complete")
    def test_parses_json_response(self, mock_complete: MagicMock, tmp_path: Path) -> None:
        img = tmp_path / "fig.png"
        img.write_bytes(b"\x89PNG" + b"\x00" * 20)

        mock_complete.return_value = (
            '```json\n{"description": "A graph", "data_points": [],'
            ' "concepts": [], "values": []}\n```'
        )

        result = describe_figure(image_path=img)
        assert result["description"] == "A graph"

    @patch("wikify.core.llm.vision.complete")
    def test_handles_malformed_response(self, mock_complete: MagicMock, tmp_path: Path) -> None:
        img = tmp_path / "fig.png"
        img.write_bytes(b"\x89PNG" + b"\x00" * 20)

        mock_complete.return_value = "This is not JSON at all."

        result = describe_figure(image_path=img)
        # Should return defaults instead of crashing
        assert "description" in result
        assert "data_points" in result


# ── extract_table_from_image ─────────────────────────────────────────────────


class TestExtractTableFromImage:
    @patch("wikify.core.llm.vision.complete")
    def test_builds_correct_prompt(self, mock_complete: MagicMock, tmp_path: Path) -> None:
        img = tmp_path / "table.png"
        img.write_bytes(b"\x89PNG" + b"\x00" * 20)

        mock_complete.return_value = json.dumps(
            {
                "markdown_table": "| Material | Thickness |\n| --- | --- |\n| HfO2 | 10nm |",
                "headers": ["Material", "Thickness"],
                "data_points": ["HfO2 deposited at 10nm"],
            }
        )

        result = extract_table_from_image(image_path=img, caption="Table 1: ALD parameters")

        call_args = mock_complete.call_args
        messages = (
            call_args.kwargs.get("messages") or call_args[1].get("messages") or call_args[0][0]
        )
        content = messages[0]["content"]
        assert content[0]["type"] == "image_url"
        text_block = content[1]["text"]
        assert "Table 1: ALD parameters" in text_block
        assert "markdown table" in text_block.lower()

        assert result["headers"] == ["Material", "Thickness"]

    @patch("wikify.core.llm.vision.complete")
    def test_parses_response(self, mock_complete: MagicMock, tmp_path: Path) -> None:
        img = tmp_path / "table.png"
        img.write_bytes(b"\x89PNG" + b"\x00" * 20)

        mock_complete.return_value = json.dumps(
            {
                "markdown_table": "| A | B |\n| - | - |\n| 1 | 2 |",
                "headers": ["A", "B"],
                "data_points": [],
            }
        )

        result = extract_table_from_image(image_path=img)
        assert "| A | B |" in result["markdown_table"]


# ── view_figure ──────────────────────────────────────────────────────────────


class TestViewFigure:
    @patch("wikify.core.store.db.get_session")
    def test_returns_correct_structure(self, mock_get_session: MagicMock, tmp_path: Path) -> None:
        """view_figure loads from DB and returns the expected dict keys."""
        from wikify.core.store.models import Figure, Paper

        # Create a test image
        img = tmp_path / "ab" / "cd" / "abcdef123456.png"
        img.parent.mkdir(parents=True, exist_ok=True)
        img.write_bytes(b"\x89PNG" + b"\x00" * 20)

        fig = Figure(
            id="abcdef123456",
            paper_id="paper1",
            caption="Test figure",
            image_path=str(img),
            width_px=400,
            height_px=300,
            format="png",
            llm_description='{"description": "A test"}',
        )
        paper = Paper(id="paper1", title="Test Paper")

        # Mock session to return our test objects
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        def exec_side_effect(stmt):
            result = MagicMock()
            # Detect which query by checking the statement's whereclause
            stmt_str = str(stmt)
            if "figure" in stmt_str.lower():
                result.first.return_value = fig
            else:
                result.first.return_value = paper
            return result

        mock_session.exec.side_effect = exec_side_effect
        mock_get_session.return_value = mock_session

        result = view_figure("abcdef123456")

        assert result["caption"] == "Test figure"
        assert result["llm_description"] == '{"description": "A test"}'
        assert result["paper_title"] == "Test Paper"
        assert result["image_base64"] != ""
        assert "image/" in result["media_type"]

    @patch("wikify.core.store.db.get_session")
    def test_missing_figure_returns_error(self, mock_get_session: MagicMock) -> None:
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_session.exec.return_value = mock_result

        mock_get_session.return_value = mock_session

        result = view_figure("nonexistent")
        assert "error" in result


# ── enrich_paper_figures ─────────────────────────────────────────────────────


class TestEnrichPaperFigures:
    @patch("wikify.wiki.figure_enrichment.describe_figure")
    @patch("wikify.wiki.figure_enrichment._resolve_figure_path")
    @patch("wikify.wiki.figure_enrichment.get_session")
    def test_skips_already_described(
        self,
        mock_get_session: MagicMock,
        mock_resolve: MagicMock,
        mock_describe: MagicMock,
    ) -> None:
        from wikify.core.store.models import Figure
        from wikify.wiki.figure_enrichment import enrich_paper_figures

        fig = Figure(
            id="abc123",
            paper_id="p1",
            llm_description="already done",
            width_px=500,
            height_px=500,
        )

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        call_count = [0]

        def exec_side_effect(stmt):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                # Paper query
                result.first.return_value = None
            else:
                # Figures query
                result.all.return_value = [fig]
            return result

        mock_session.exec.side_effect = exec_side_effect
        mock_get_session.return_value = mock_session

        count = enrich_paper_figures("p1")

        assert count == 0
        mock_describe.assert_not_called()

    @patch("wikify.wiki.figure_enrichment.describe_figure")
    @patch("wikify.wiki.figure_enrichment._resolve_figure_path")
    @patch("wikify.wiki.figure_enrichment.get_session")
    def test_skips_small_images(
        self,
        mock_get_session: MagicMock,
        mock_resolve: MagicMock,
        mock_describe: MagicMock,
    ) -> None:
        from wikify.core.store.models import Figure
        from wikify.wiki.figure_enrichment import enrich_paper_figures

        fig = Figure(
            id="small123",
            paper_id="p1",
            width_px=100,
            height_px=100,
        )

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        call_count = [0]

        def exec_side_effect(stmt):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.first.return_value = None
            else:
                result.all.return_value = [fig]
            return result

        mock_session.exec.side_effect = exec_side_effect
        mock_get_session.return_value = mock_session

        count = enrich_paper_figures("p1")

        assert count == 0
        mock_describe.assert_not_called()

    @patch("wikify.wiki.figure_enrichment.describe_figure")
    @patch("wikify.wiki.figure_enrichment._resolve_figure_path")
    @patch("wikify.wiki.figure_enrichment.get_session")
    def test_skips_caption_sufficient(
        self,
        mock_get_session: MagicMock,
        mock_resolve: MagicMock,
        mock_describe: MagicMock,
    ) -> None:
        from wikify.core.store.models import Figure
        from wikify.wiki.figure_enrichment import enrich_paper_figures

        long_caption = " ".join(["word"] * 55)  # 55 words > threshold of 50
        fig = Figure(
            id="cap123",
            paper_id="p1",
            caption=long_caption,
            width_px=500,
            height_px=500,
        )

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        call_count = [0]

        def exec_side_effect(stmt):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.first.return_value = None
            else:
                result.all.return_value = [fig]
            return result

        mock_session.exec.side_effect = exec_side_effect
        mock_get_session.return_value = mock_session

        count = enrich_paper_figures("p1")

        assert count == 0
        mock_describe.assert_not_called()

    @patch("wikify.wiki.figure_enrichment.describe_figure")
    @patch("wikify.wiki.figure_enrichment._resolve_figure_path")
    @patch("wikify.wiki.figure_enrichment.get_session")
    def test_enriches_eligible_figure(
        self,
        mock_get_session: MagicMock,
        mock_resolve: MagicMock,
        mock_describe: MagicMock,
        tmp_path: Path,
    ) -> None:
        from wikify.core.store.models import Figure, Paper
        from wikify.wiki.figure_enrichment import enrich_paper_figures

        img = tmp_path / "fig.png"
        img.write_bytes(b"\x89PNG" + b"\x00" * 20)

        fig = Figure(
            id="eligible1",
            paper_id="p1",
            caption="Short caption",
            width_px=500,
            height_px=500,
            format="png",
        )
        paper = Paper(id="p1", title="Test Paper")

        mock_resolve.return_value = img
        mock_describe.return_value = {
            "description": "A plot showing data",
            "data_points": [],
            "concepts": [],
            "values": [],
        }

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        call_count = [0]

        def exec_side_effect(stmt):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.first.return_value = paper
            else:
                result.all.return_value = [fig]
            return result

        mock_session.exec.side_effect = exec_side_effect
        mock_get_session.return_value = mock_session

        count = enrich_paper_figures("p1")

        assert count == 1
        mock_describe.assert_called_once()
        # Verify the figure got its description set
        assert fig.llm_description is not None


# ── _parse_json_response ─────────────────────────────────────────────────────


class TestParseJsonResponse:
    def test_clean_json(self) -> None:
        raw = '{"description": "test", "data_points": []}'
        result = _parse_json_response(raw, ["description", "data_points"])
        assert result["description"] == "test"

    def test_markdown_fenced_json(self) -> None:
        raw = '```json\n{"description": "fenced"}\n```'
        result = _parse_json_response(raw, ["description"])
        assert result["description"] == "fenced"

    def test_text_around_json(self) -> None:
        raw = 'Here is the result: {"description": "embedded"} hope this helps!'
        result = _parse_json_response(raw, ["description"])
        assert result["description"] == "embedded"

    def test_invalid_returns_defaults(self) -> None:
        raw = "No JSON here at all."
        result = _parse_json_response(raw, ["description", "data_points", "concepts"])
        assert result["description"] == ""
        assert result["data_points"] == []
        assert result["concepts"] == []
