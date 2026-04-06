"""Tests for the unified media extraction pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from wikify.extract.media import (
    _SCAN_THRESHOLD,
    _CaptionMatch,
    _consume_md_caption,
    _extract_captions_from_markdown,
    _extract_images_from_page,
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
            # Include a table caption block when table_data is provided
            if table_data:
                page.get_text.return_value = [
                    (0, 0, 500, 20, "Table 1. Process parameters", 0, 0)
                ]
            else:
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

            results = extract_media("/fake.pdf", "paper123", "Table 1. Process parameters")
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


# ── Scanned page detection ───────────────────────────────────────────────────


class TestScannedPageDetection:
    """Issue 1: pages with >15 images are treated as scanned."""

    def test_many_images_triggers_scan_path(self, tmp_path):
        """A page with >SCAN_THRESHOLD images should skip fragment extraction."""
        with patch("wikify.extract.media.settings") as mock_settings:
            mock_settings.figures_dir = tmp_path / "figures"

            doc = MagicMock()
            page = MagicMock()

            # Simulate 20 tiny image fragments (scanned page)
            page.get_images.return_value = [(i,) + (0,) * 7 for i in range(20)]
            assert len(page.get_images()) > _SCAN_THRESHOLD

            # No captions -> should return empty list, not extract fragments
            page.get_text.return_value = []

            page_captions: list[_CaptionMatch] = []
            md_captions: list[_CaptionMatch] = []

            results = _extract_images_from_page(
                doc, page, 0, "paper1", set(), page_captions, md_captions
            )
            # No captions means scanned page produces no figures
            assert results == []
            # extract_image should NOT have been called (fragments skipped)
            doc.extract_image.assert_not_called()

    def test_scanned_page_with_caption_renders_full_page(self, tmp_path):
        """Scanned page with captions renders page as pixmap."""
        with patch("wikify.extract.media.settings") as mock_settings:
            mock_settings.figures_dir = tmp_path / "figures"

            doc = MagicMock()
            page = MagicMock()

            page.get_images.return_value = [(i,) + (0,) * 7 for i in range(20)]

            # Mock pixmap rendering
            mock_pixmap = MagicMock()
            mock_pixmap.tobytes.return_value = b"\x89PNG" + b"\x00" * 5000
            mock_pixmap.width = 1200
            mock_pixmap.height = 1600
            page.get_pixmap.return_value = mock_pixmap

            page_captions = [
                _CaptionMatch("Fig. 3", "SEM cross-section", "figure", y_position=400.0),
            ]
            md_captions: list[_CaptionMatch] = []

            results = _extract_images_from_page(
                doc, page, 0, "paper1", set(), page_captions, md_captions
            )
            assert len(results) == 1
            assert results[0].label == "Fig. 3"
            assert results[0].width_px == 1200
            assert results[0].height_px == 1600
            # Caption should have been consumed
            assert len(page_captions) == 0

    def test_normal_page_not_affected(self, tmp_path):
        """Pages with <=SCAN_THRESHOLD images use normal extraction."""
        with patch("wikify.extract.media.settings") as mock_settings:
            mock_settings.figures_dir = tmp_path / "figures"

            doc = MagicMock()
            page = MagicMock()

            # 3 images -- normal page
            page.get_images.return_value = [(i,) + (0,) * 7 for i in range(3)]
            page.get_image_info.return_value = []

            doc.extract_image.return_value = {
                "image": b"\x89PNG" + b"\x00" * 3000,
                "width": 400,
                "height": 300,
                "ext": "png",
            }

            _extract_images_from_page(doc, page, 0, "paper1", set(), [], [])
            # Should have called extract_image for each fragment
            assert doc.extract_image.call_count == 3


# ── Caption consumption ──────────────────────────────────────────────────────


class TestCaptionConsumption:
    """Issue 2 & 3: captions should not be reused across images or pages."""

    def test_same_caption_not_assigned_twice(self, tmp_path):
        """Multiple images matching same caption: only largest gets it."""
        with patch("wikify.extract.media.settings") as mock_settings:
            mock_settings.figures_dir = tmp_path / "figures"

            doc = MagicMock()
            page = MagicMock()

            # Two images on the page
            page.get_images.return_value = [(10,) + (0,) * 7, (11,) + (0,) * 7]
            page.get_image_info.return_value = []

            # First image: small (200x150), second image: large (800x600)
            def extract_image_side_effect(xref):
                if xref == 10:
                    return {
                        "image": b"\x89PNG_small" + b"\x00" * 3000,
                        "width": 200,
                        "height": 150,
                        "ext": "png",
                    }
                return {
                    "image": b"\x89PNG_large" + b"\x00" * 3000,
                    "width": 800,
                    "height": 600,
                    "ext": "png",
                }

            doc.extract_image.side_effect = extract_image_side_effect

            page_captions = [
                _CaptionMatch("Fig. 7", "Composite panel", "figure", y_position=500.0),
            ]
            md_captions: list[_CaptionMatch] = []

            results = _extract_images_from_page(
                doc, page, 0, "paper1", set(), page_captions, md_captions
            )
            assert len(results) == 2
            labeled = [r for r in results if r.label == "Fig. 7"]
            unlabeled = [r for r in results if r.label is None]
            # Only one should get the caption (the larger one)
            assert len(labeled) == 1
            assert labeled[0].width_px == 800
            assert len(unlabeled) == 1

    def test_md_caption_consumed_across_pages(self, tmp_path):
        """md_captions should be depleted after first use (Issue 3)."""
        with (
            patch("wikify.extract.media.fitz") as mock_fitz,
            patch("wikify.extract.media.settings") as mock_settings,
        ):
            mock_settings.figures_dir = tmp_path / "figures"

            # Build doc with 2 pages, each with 1 image (different bytes)
            doc = MagicMock()
            doc.__len__ = MagicMock(return_value=2)

            pages = []
            for p in range(2):
                pg = MagicMock()
                pg.get_images.return_value = [(100 + p,) + (0,) * 7]
                pg.get_image_info.return_value = []
                pg.get_text.return_value = []  # No page-level captions
                pg.find_tables.return_value = []
                pages.append(pg)

            doc.__getitem__ = MagicMock(side_effect=lambda i: pages[i])

            # Different image bytes per page so hashes differ
            def extract_image_side_effect(xref):
                return {
                    "image": b"\x89PNG" + bytes([xref]) * 3000,
                    "width": 400,
                    "height": 300,
                    "ext": "png",
                }

            doc.extract_image.side_effect = extract_image_side_effect
            mock_fitz.open.return_value = doc

            # Only one Fig. 1 caption in markdown
            md_text = "**Fig. 1.** The cross-section image."
            results = extract_media("/fake.pdf", "paper1", md_text)

            # Only one figure should get the "Fig. 1" label
            labeled = [r for r in results if r.label and "Fig. 1" in r.label]
            assert len(labeled) == 1

    def test_consume_md_caption_removes_entry(self):
        """_consume_md_caption removes first matching label."""
        captions = [
            _CaptionMatch("Fig. 1", "First", "figure"),
            _CaptionMatch("Fig. 2", "Second", "figure"),
            _CaptionMatch("Fig. 1", "Duplicate", "figure"),
        ]
        _consume_md_caption(captions, "Fig. 1")
        assert len(captions) == 2
        assert captions[0].label == "Fig. 2"
        assert captions[1].label == "Fig. 1"  # Second instance survives
