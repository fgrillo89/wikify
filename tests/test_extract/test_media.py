"""Tests for the unified media extraction pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from wikify.extract.media import (
    _CaptionMatch,
    _extract_captions_from_markdown,
    _match_caption,
    _store_media,
    _table_data_to_markdown,
    extract_media,
)
from wikify.store.models import Figure

# ── Caption extraction from markdown ────────────────────────────────────────────


class TestExtractCaptionsFromMarkdown:
    def test_figure_caption(self):
        md = "**Fig. 1.** The measured I-V curve of the HfO2 device."
        captions = _extract_captions_from_markdown(md)
        assert len(captions) == 1
        assert captions[0].label == "Fig. 1"
        assert captions[0].media_type == "figure"
        assert "I-V curve" in captions[0].text

    def test_table_caption(self):
        md = "**Table 2.** Deposition parameters for ALD films."
        captions = _extract_captions_from_markdown(md)
        assert len(captions) == 1
        assert captions[0].label == "Table 2"
        assert captions[0].media_type == "table"

    def test_scheme_caption(self):
        md = "Scheme 1: Overview of the proposed synthesis route."
        captions = _extract_captions_from_markdown(md)
        assert len(captions) == 1
        assert captions[0].media_type == "scheme"

    def test_mixed_captions(self):
        md = (
            "Some intro text.\n"
            "**Fig. 1.** First figure caption.\n"
            "More text here.\n"
            "**Table 1.** A table of values.\n"
            "**Figure 2** Second figure.\n"
        )
        captions = _extract_captions_from_markdown(md)
        assert len(captions) == 3
        types = [c.media_type for c in captions]
        assert types.count("figure") == 2
        assert types.count("table") == 1

    def test_no_captions(self):
        md = "This is just regular text with no figure or table references."
        captions = _extract_captions_from_markdown(md)
        assert captions == []

    def test_caption_text_capped_at_500(self):
        long_caption = "Fig. 1. " + "x" * 600
        captions = _extract_captions_from_markdown(long_caption)
        assert len(captions) == 1
        assert len(captions[0].text) <= 500


# ── Caption matching ───────────────────────────────────────────────────────────


class TestMatchCaption:
    def test_match_by_proximity(self):
        """Caption closest to figure bottom is preferred."""
        page_caps = [
            _CaptionMatch("Fig. 1", "First", "figure", y_position=100.0),
            _CaptionMatch("Fig. 2", "Second", "figure", y_position=500.0),
        ]
        # Figure bbox bottom at y=480
        result = _match_caption(0, [0, 400, 300, 480], page_caps, [])
        assert result is not None
        assert result.label == "Fig. 2"

    def test_exclude_table_for_images(self):
        page_caps = [
            _CaptionMatch("Table 1", "A table", "table", y_position=100.0),
            _CaptionMatch("Fig. 1", "A figure", "figure", y_position=200.0),
        ]
        result = _match_caption(0, None, page_caps, [], exclude_type="table")
        assert result is not None
        assert result.media_type == "figure"

    def test_prefer_table_for_tables(self):
        page_caps = [
            _CaptionMatch("Fig. 1", "A figure", "figure", y_position=100.0),
            _CaptionMatch("Table 1", "A table", "table", y_position=200.0),
        ]
        result = _match_caption(0, None, page_caps, [], prefer_type="table")
        assert result is not None
        assert result.media_type == "table"

    def test_fallback_to_md_captions(self):
        md_caps = [_CaptionMatch("Fig. 3", "From markdown", "figure")]
        result = _match_caption(0, None, [], md_caps)
        assert result is not None
        assert result.label == "Fig. 3"

    def test_no_captions_returns_none(self):
        result = _match_caption(0, None, [], [])
        assert result is None


# ── Table data to markdown ─────────────────────────────────────────────────────


class TestTableDataToMarkdown:
    def test_basic_table(self):
        data = [["A", "B", "C"], ["1", "2", "3"], ["4", "5", "6"]]
        md = _table_data_to_markdown(data)
        lines = md.strip().split("\n")
        assert len(lines) == 4  # header + separator + 2 data rows
        assert "| A | B | C |" in lines[0]
        assert "| --- | --- | --- |" in lines[1]
        assert "| 1 | 2 | 3 |" in lines[2]

    def test_empty_data(self):
        assert _table_data_to_markdown([]) == ""
        assert _table_data_to_markdown([[]]) == ""

    def test_ragged_rows(self):
        data = [["A", "B"], ["1"]]
        md = _table_data_to_markdown(data)
        assert md  # Should not crash
        # Second row should be padded
        assert "| 1 |" in md

    def test_pipe_escaping(self):
        data = [["col"], ["val|ue"]]
        md = _table_data_to_markdown(data)
        assert "val\\|ue" in md

    def test_none_cells(self):
        data = [["A", "B"], [None, "x"]]
        md = _table_data_to_markdown(data)
        assert md  # Should not crash


# ── Content-addressed storage ──────────────────────────────────────────────────


class TestStoreMedia:
    def test_creates_nested_directory(self, tmp_path):
        with patch("wikify.extract.media.settings") as mock_settings:
            mock_settings.figures_dir = tmp_path / "figures"
            content = b"fake image content for testing"
            h = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
            result = _store_media(content, h, "png")

            expected = tmp_path / "figures" / "ab" / "cd" / f"{h}.png"
            assert result == expected
            assert result.exists()
            assert result.read_bytes() == content

    def test_idempotent_write(self, tmp_path):
        with patch("wikify.extract.media.settings") as mock_settings:
            mock_settings.figures_dir = tmp_path / "figures"
            content = b"image bytes"
            h = "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
            _store_media(content, h, "png")
            path = _store_media(content, h, "png")
            assert path.read_bytes() == content


# ── Full extract_media with mocked fitz ────────────────────────────────────────


class TestExtractMedia:
    def _make_mock_doc(
        self,
        *,
        num_pages: int = 1,
        images_per_page: list[list[tuple]] | None = None,
        image_bytes: bytes | None = None,
        table_data: list[list] | None = None,
    ) -> MagicMock:
        """Build a mock fitz.Document with configurable images and tables."""
        doc = MagicMock()
        doc.__len__ = MagicMock(return_value=num_pages)
        doc.__enter__ = MagicMock(return_value=doc)
        doc.__exit__ = MagicMock(return_value=False)

        if image_bytes is None:
            image_bytes = b"\x89PNG" + b"\x00" * 3000  # fake PNG > _MIN_BYTES

        pages = []
        for p in range(num_pages):
            page = MagicMock()

            # Images
            if images_per_page and p < len(images_per_page):
                page.get_images.return_value = images_per_page[p]
            else:
                page.get_images.return_value = []

            # Text blocks (for caption matching)
            page.get_text.return_value = []
            page.get_image_info.return_value = []

            # Tables
            if table_data:
                mock_table = MagicMock()
                mock_table.extract.return_value = table_data
                mock_table.bbox = (0, 0, 500, 300)
                page.find_tables.return_value = [mock_table]
            else:
                page.find_tables.return_value = []

            pages.append(page)

        doc.__getitem__ = MagicMock(side_effect=lambda i: pages[i])

        # extract_image returns consistent data
        doc.extract_image.return_value = {
            "image": image_bytes,
            "width": 400,
            "height": 300,
            "ext": "png",
        }

        return doc

    def test_extracts_images(self, tmp_path):
        with (
            patch("wikify.extract.media.fitz") as mock_fitz,
            patch("wikify.extract.media.settings") as mock_settings,
        ):
            mock_settings.figures_dir = tmp_path / "figures"
            doc = self._make_mock_doc(
                num_pages=1,
                images_per_page=[[(42, 0, 0, 0, 0, 0, 0, 0)]],
            )
            mock_fitz.open.return_value = doc

            results = extract_media("/fake.pdf", "paper123", "")
            assert len(results) >= 1
            fig = results[0]
            assert isinstance(fig, Figure)
            assert fig.media_type == "figure"
            assert fig.page_number == 0
            assert fig.paper_id == "paper123"
            assert fig.width_px == 400
            assert fig.height_px == 300

    def test_extracts_tables(self, tmp_path):
        with (
            patch("wikify.extract.media.fitz") as mock_fitz,
            patch("wikify.extract.media.settings") as mock_settings,
        ):
            mock_settings.figures_dir = tmp_path / "figures"
            table_data = [["Param", "Value"], ["Temp", "250C"], ["Pressure", "1 Torr"]]
            doc = self._make_mock_doc(num_pages=1, table_data=table_data)
            mock_fitz.open.return_value = doc

            results = extract_media("/fake.pdf", "paper123", "")
            tables = [r for r in results if r.media_type == "table"]
            assert len(tables) == 1
            tbl = tables[0]
            assert tbl.markdown_table is not None
            assert "Param" in tbl.markdown_table
            assert tbl.extracted_data is not None
            parsed = json.loads(tbl.extracted_data)
            assert len(parsed) == 3

    def test_caption_matching_from_md(self, tmp_path):
        with (
            patch("wikify.extract.media.fitz") as mock_fitz,
            patch("wikify.extract.media.settings") as mock_settings,
        ):
            mock_settings.figures_dir = tmp_path / "figures"
            doc = self._make_mock_doc(
                num_pages=1,
                images_per_page=[[(42, 0, 0, 0, 0, 0, 0, 0)]],
            )
            mock_fitz.open.return_value = doc

            md_text = "**Fig. 1.** Cross-section SEM image of the ALD HfO2 film."
            results = extract_media("/fake.pdf", "paper123", md_text)
            assert len(results) >= 1
            fig = results[0]
            assert fig.label is not None
            assert "Fig" in fig.label

    def test_skips_tiny_images(self, tmp_path):
        with (
            patch("wikify.extract.media.fitz") as mock_fitz,
            patch("wikify.extract.media.settings") as mock_settings,
        ):
            mock_settings.figures_dir = tmp_path / "figures"
            doc = self._make_mock_doc(
                num_pages=1,
                images_per_page=[[(42, 0, 0, 0, 0, 0, 0, 0)]],
                image_bytes=b"\x89PNG" + b"\x00" * 50,  # tiny
            )
            # Override extract_image to return tiny dimensions
            doc.extract_image.return_value = {
                "image": b"\x89PNG" + b"\x00" * 50,
                "width": 20,
                "height": 20,
                "ext": "png",
            }
            mock_fitz.open.return_value = doc

            results = extract_media("/fake.pdf", "paper123", "")
            images = [r for r in results if r.media_type == "figure"]
            assert len(images) == 0

    def test_deduplicates_images(self, tmp_path):
        with (
            patch("wikify.extract.media.fitz") as mock_fitz,
            patch("wikify.extract.media.settings") as mock_settings,
        ):
            mock_settings.figures_dir = tmp_path / "figures"
            # Two identical images on the same page
            doc = self._make_mock_doc(
                num_pages=1,
                images_per_page=[[(42, 0, 0, 0, 0, 0, 0, 0), (43, 0, 0, 0, 0, 0, 0, 0)]],
            )
            mock_fitz.open.return_value = doc

            results = extract_media("/fake.pdf", "paper123", "")
            images = [r for r in results if r.media_type == "figure"]
            # Same bytes -> same hash -> deduplicated
            assert len(images) == 1

    def test_meta_sidecar_written(self, tmp_path):
        with (
            patch("wikify.extract.media.fitz") as mock_fitz,
            patch("wikify.extract.media.settings") as mock_settings,
        ):
            mock_settings.figures_dir = tmp_path / "figures"
            doc = self._make_mock_doc(
                num_pages=1,
                images_per_page=[[(42, 0, 0, 0, 0, 0, 0, 0)]],
            )
            mock_fitz.open.return_value = doc

            results = extract_media("/fake.pdf", "paper123", "")
            assert len(results) >= 1
            img_path = Path(results[0].image_path)
            meta_path = img_path.with_suffix(".png.meta.json")
            assert meta_path.exists()
            meta = json.loads(meta_path.read_text())
            assert meta["paper_id"] == "paper123"
            assert meta["page_number"] == 0
