"""Caption reassignment planner + Docling parser hook."""

from wikify.corpus.images_index import is_decoration_dims, plan_caption_reassignment
from wikify.ingest.parsers.docling import _reassign_misbound_captions
from wikify.ingest.parsers.registry import RawImage


def test_is_decoration_dims_short_side():
    assert is_decoration_dims(236, 99)
    assert is_decoration_dims(99, 236)


def test_is_decoration_dims_small_area():
    assert is_decoration_dims(150, 150)  # area 22500 < 40000


def test_is_decoration_dims_extreme_aspect():
    # 800x150: short=150 passes; area=120000 passes; aspect=5.33 > 4
    assert is_decoration_dims(800, 150)


def test_is_decoration_dims_real_figure_passes():
    assert not is_decoration_dims(1526, 798)
    assert not is_decoration_dims(691, 304)


def test_plan_caption_reassignment_banner_then_real_same_page():
    items = [
        ("banner", 5, 237, 98, "Figure 9. Real caption text."),
        ("real", 5, 1474, 779, ""),
    ]
    plan = plan_caption_reassignment(items)
    assert plan == [(0, 1)]


def test_plan_caption_reassignment_skips_when_target_already_captioned():
    items = [
        ("banner", 5, 237, 98, "Figure 9. ..."),
        ("real", 5, 1474, 779, "already has caption"),
    ]
    assert plan_caption_reassignment(items) == []


def test_plan_caption_reassignment_no_target_available():
    items = [
        ("banner_only", 5, 237, 98, "Figure 9. ..."),
    ]
    assert plan_caption_reassignment(items) == []


def test_plan_caption_reassignment_pairs_within_page_only():
    # banner on page 5, real on page 6 — different pages, no pairing.
    items = [
        ("banner_p5", 5, 237, 98, "Figure 9. ..."),
        ("real_p6", 6, 1474, 779, ""),
    ]
    assert plan_caption_reassignment(items) == []


def test_plan_caption_reassignment_multiple_pairs_same_page():
    items = [
        ("banner1", 3, 237, 98, "Figure 1. ..."),
        ("real1", 3, 1474, 779, ""),
        ("banner2", 3, 237, 98, "Figure 2. ..."),
        ("real2", 3, 1474, 779, ""),
    ]
    plan = plan_caption_reassignment(items)
    assert plan == [(0, 1), (2, 3)]


def test_plan_caption_reassignment_skips_missing_dims():
    items = [
        ("banner", 3, None, None, "Figure 1. ..."),
        ("real", 3, 1474, 779, ""),
    ]
    assert plan_caption_reassignment(items) == []


def test_reassign_misbound_captions_drops_banner_and_carries_metadata():
    images = [
        RawImage(data=b"x", page=5, width=237, height=98, caption="Figure 9. ...",
                 label="Figure 9", media_type="figure"),
        RawImage(data=b"y", page=5, width=1474, height=779, caption=""),
    ]
    out = _reassign_misbound_captions(images)
    assert len(out) == 1
    assert out[0].caption == "Figure 9. ..."
    assert out[0].label == "Figure 9"
    assert out[0].media_type == "figure"
    assert out[0].width == 1474


def test_reassign_misbound_captions_noop_when_nothing_to_move():
    images = [
        RawImage(data=b"x", page=5, width=1474, height=779, caption="Figure 1. ..."),
        RawImage(data=b"y", page=5, width=1480, height=780, caption="Figure 2. ..."),
    ]
    out = _reassign_misbound_captions(images)
    assert out == images  # same list returned unchanged


def test_reassign_misbound_captions_preserves_order():
    images = [
        RawImage(data=b"a", page=1, width=1474, height=779, caption="Figure 1. ..."),
        RawImage(data=b"b", page=5, width=237, height=98, caption="Figure 5. ..."),
        RawImage(data=b"c", page=5, width=1474, height=779, caption=""),
        RawImage(data=b"d", page=6, width=1474, height=779, caption="Figure 6. ..."),
    ]
    out = _reassign_misbound_captions(images)
    # banner at index 1 should drop; remaining order: a, c (now captioned), d
    assert [im.data for im in out] == [b"a", b"c", b"d"]
    assert out[1].caption == "Figure 5. ..."
