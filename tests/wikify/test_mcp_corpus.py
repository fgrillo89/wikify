"""MCP layer tests for the corpus surface.

Three concerns that are unique to the MCP adapter:

- tool registration: ``build_server`` exposes the corpus tools.
- envelope shape: every tool returns ``{ok, kind, items, notes, next}``
  (or ``{ok=False, code, message}`` on error).
- parity: tool ``items`` align with the underlying ``queries.*``
  primitive's output for matching args.

Data correctness lives in ``test_corpus_queries.py``; format/exit-code
correctness lives in ``test_cli_corpus.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Reuse the on-disk corpus builder from test_corpus_queries.
from tests.wikify.test_corpus_queries import _make_corpus  # noqa: E402
from wikify.corpus import queries
from wikify.mcp import context, server

ENVELOPE_KEYS = {"ok", "kind", "items", "notes", "next"}


@pytest.fixture(autouse=True)
def _reset_mcp_context() -> None:
    """Drop any stray binding so test order is irrelevant."""
    context.reset()
    yield
    context.reset()


def _tool(srv, name):
    """Pull the underlying async function for a registered tool by name."""
    info = srv._tool_manager.get_tool(name)
    assert info is not None, f"tool {name!r} not registered"
    return info.fn


# ----------------------------------------------------------- registration


def test_build_server_registers_corpus_tools() -> None:
    srv = server.build_server()
    names = {t.name for t in srv._tool_manager.list_tools()}
    assert names == {
        "context_show",
        "context_set",
        "corpus_find",
        "corpus_traverse",
        "corpus_show",
        "corpus_sample",
        "corpus_schema",
        "corpus_image",
    }


def test_build_server_registers_corpus_resources() -> None:
    srv = server.build_server()
    templates = {t.uri_template for t in srv._resource_manager.list_templates()}
    static = {str(r.uri) for r in srv._resource_manager.list_resources()}
    assert "wikify://corpus/docs/{ident}" in templates
    assert "wikify://corpus/chunks/{ident}" in templates
    assert "wikify://corpus/figures/{doc_short}/{stem}" in templates
    assert "wikify://corpus/equations/{ident}" in templates
    assert "wikify://corpus/authors/{ident}" in templates
    assert "wikify://schemas/corpus" in static


# ----------------------------------------------------------- envelope shape


async def test_envelope_shape_ok(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    context.bind(corpus_path=corpus.root)
    srv = server.build_server()
    schema = await _tool(srv, "corpus_schema")()
    assert schema.keys() >= ENVELOPE_KEYS
    assert schema["ok"] is True
    assert schema["kind"] == "corpus_schema"
    assert isinstance(schema["items"], list)
    assert isinstance(schema["notes"], list)


async def test_envelope_shape_err_no_corpus_bound() -> None:
    srv = server.build_server()
    res = await _tool(srv, "corpus_find")(query="anything")
    assert res["ok"] is False
    assert res["code"] == "no_corpus_bound"
    assert "message" in res


async def test_envelope_shape_err_bad_args(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    context.bind(corpus_path=corpus.root)
    srv = server.build_server()
    res = await _tool(srv, "corpus_find")(
        query="x", by="chunk", rank="citation_count",
    )
    assert res["ok"] is False
    assert res["code"] == "bad_rank_by_combo"


# ------------------------------------------------------------------ parity


async def test_corpus_find_text_parity_with_queries(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    context.bind(corpus_path=corpus.root)
    srv = server.build_server()
    res = await _tool(srv, "corpus_find")(
        query="atomic layer", text=True, top_k=10,
    )
    direct = queries.find(
        corpus, query="atomic layer", by="chunk", rank="semantic",
        top_k=10, text=True,
    )
    assert res["ok"] is True
    assert len(res["items"]) == len(direct["rows"])
    # Item handles align with primitive ids.
    direct_ids = [r["id"] for r in direct["rows"]]
    item_ids = [it["handle"].split(":", 1)[1] for it in res["items"]]
    # short_id may shorten ids — compare using endswith.
    for full, short in zip(direct_ids, item_ids, strict=True):
        assert full.endswith(short)


async def test_corpus_show_doc_parity(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    context.bind(corpus_path=corpus.root)
    srv = server.build_server()
    res = await _tool(srv, "corpus_show")(handle="doc:paper_0")
    assert res["ok"] is True
    assert len(res["items"]) == 1
    item = res["items"][0]
    assert item["type"] == "doc"
    direct = queries.get_doc(corpus, "paper_0")
    assert item["title"] == direct.title
    assert item["resource_uri"] == "wikify://corpus/docs/paper_0"


async def test_corpus_show_chunk_full_text(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    context.bind(corpus_path=corpus.root)
    srv = server.build_server()
    res = await _tool(srv, "corpus_show")(
        handle="chunk:paper_0__c0000", full=True,
    )
    item = res["items"][0]
    assert item["type"] == "chunk"
    assert "atomic layer deposition" in item.get("text", "")


async def test_corpus_show_handle_not_found(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    context.bind(corpus_path=corpus.root)
    srv = server.build_server()
    res = await _tool(srv, "corpus_show")(handle="doc:nope")
    assert res["ok"] is False
    assert res["code"] == "doc_not_found"


async def test_corpus_schema_parity() -> None:
    srv = server.build_server()
    res = await _tool(srv, "corpus_schema")()
    assert res["items"] == [queries.SCHEMA]


# ----------------------------------------------------------- context tools


async def test_context_show_default_unbound() -> None:
    srv = server.build_server()
    res = await _tool(srv, "context_show")()
    snap = res["items"][0]
    assert snap["corpus_bound"] is False
    assert snap["bundle_bound"] is False


async def test_context_set_then_show(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    srv = server.build_server()
    set_res = await _tool(srv, "context_set")(corpus_path=str(corpus.root))
    assert set_res["ok"] is True
    show_res = await _tool(srv, "context_show")()
    snap = show_res["items"][0]
    assert snap["corpus_bound"] is True
    assert Path(snap["corpus_path"]) == corpus.root


async def test_context_set_bad_path_returns_error() -> None:
    srv = server.build_server()
    res = await _tool(srv, "context_set")(corpus_path="/no/such/dir")
    assert res["ok"] is False
    assert res["code"] == "bad_context"


# ---------------------------------------------------------------- resources


async def test_doc_resource_returns_full_record(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    context.bind(corpus_path=corpus.root)
    srv = server.build_server()
    contents = await srv.read_resource("wikify://corpus/docs/paper_0")
    payload = list(contents)[0].content
    # FunctionResource serialises non-str payloads to JSON text.
    import json as _json
    data = _json.loads(payload)
    assert data["title"] == "Title 0"
    assert data["handle"] == "doc:paper_0"


async def test_chunk_resource_returns_full_text(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    context.bind(corpus_path=corpus.root)
    srv = server.build_server()
    contents = await srv.read_resource(
        "wikify://corpus/chunks/paper_0__c0000"
    )
    payload = list(contents)[0].content
    import json as _json
    data = _json.loads(payload)
    assert "atomic layer deposition" in data["text"]


async def test_corpus_schema_resource(tmp_path: Path) -> None:
    srv = server.build_server()
    contents = await srv.read_resource("wikify://schemas/corpus")
    payload = list(contents)[0].content
    import json as _json
    data = _json.loads(payload)
    assert data == queries.SCHEMA


# ----------------------------------------------------- doc text + section index


async def test_corpus_show_doc_carries_section_index(tmp_path: Path) -> None:
    """corpus_show on a doc returns the section structure for cheap navigation."""
    corpus = _make_corpus(tmp_path / "c")
    context.bind(corpus_path=corpus.root)
    srv = server.build_server()
    res = await _tool(srv, "corpus_show")(handle="doc:paper_0")
    item = res["items"][0]
    assert item["type"] == "doc"
    sections = item["meta"]["sections"]
    assert any(s["section_path"] == ["intro"] for s in sections)
    assert any(s["section_path"] == ["body"] for s in sections)


async def test_corpus_show_doc_include_text_returns_grouped_body(
    tmp_path: Path,
) -> None:
    corpus = _make_corpus(tmp_path / "c")
    context.bind(corpus_path=corpus.root)
    srv = server.build_server()
    res = await _tool(srv, "corpus_show")(
        handle="doc:paper_0", include_text=True,
    )
    item = res["items"][0]
    text_blocks = item["meta"]["text"]
    assert [b["section_path"] for b in text_blocks] == [["intro"], ["body"]]
    assert all("atomic layer deposition" in b["text"] for b in text_blocks)


async def test_corpus_show_doc_section_filter(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    context.bind(corpus_path=corpus.root)
    srv = server.build_server()
    res = await _tool(srv, "corpus_show")(
        handle="doc:paper_0", include_text=True, sections=["intro"],
    )
    text_blocks = res["items"][0]["meta"]["text"]
    assert len(text_blocks) == 1
    assert text_blocks[0]["section_path"] == ["intro"]
    assert any("matched sections" in n for n in res["notes"])


async def test_corpus_show_doc_full_mode_returns_one_string(
    tmp_path: Path,
) -> None:
    """mode='full' flattens the body to one ordered string with section headers."""
    corpus = _make_corpus(tmp_path / "c")
    context.bind(corpus_path=corpus.root)
    srv = server.build_server()
    res = await _tool(srv, "corpus_show")(
        handle="doc:paper_0", include_text=True, mode="full",
    )
    item = res["items"][0]
    body = item["meta"]["body"]
    assert isinstance(body, str)
    assert "## intro" in body
    assert "## body" in body
    assert body.index("## intro") < body.index("## body")
    # mode='full' replaces the segmented view; no meta.text in this mode.
    assert "text" not in item["meta"]


async def test_corpus_show_doc_full_mode_respects_section_filter(
    tmp_path: Path,
) -> None:
    corpus = _make_corpus(tmp_path / "c")
    context.bind(corpus_path=corpus.root)
    srv = server.build_server()
    res = await _tool(srv, "corpus_show")(
        handle="doc:paper_0", include_text=True, mode="full",
        sections=["intro"],
    )
    body = res["items"][0]["meta"]["body"]
    assert "## intro" in body
    assert "## body" not in body


async def test_corpus_show_rejects_unknown_mode(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    context.bind(corpus_path=corpus.root)
    srv = server.build_server()
    res = await _tool(srv, "corpus_show")(handle="doc:paper_0", mode="bogus")
    assert res["ok"] is False
    assert res["code"] == "bad_mode"


async def test_corpus_show_doc_section_filter_no_match_echoes_available(
    tmp_path: Path,
) -> None:
    """Empty filter result must surface the available sections in notes."""
    corpus = _make_corpus(tmp_path / "c")
    context.bind(corpus_path=corpus.root)
    srv = server.build_server()
    res = await _tool(srv, "corpus_show")(
        handle="doc:paper_0", include_text=True, sections=["nonexistent"],
    )
    assert res["items"][0]["meta"]["text"] == []
    msg = " ".join(res["notes"])
    assert "matched no sections" in msg
    # Available sections from the fixture are 'intro' and 'body'.
    assert "intro" in msg and "body" in msg


# ------------------------------------------------------------- title search


async def test_corpus_find_field_title(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    context.bind(corpus_path=corpus.root)
    srv = server.build_server()
    res = await _tool(srv, "corpus_find")(
        query="Title 0", by="paper", field="title", top_k=5,
    )
    assert res["ok"] is True
    assert len(res["items"]) == 1
    assert res["items"][0]["title"] == "Title 0"


async def test_corpus_find_field_title_rejects_chunk_by(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    context.bind(corpus_path=corpus.root)
    srv = server.build_server()
    res = await _tool(srv, "corpus_find")(
        query="x", by="chunk", field="title",
    )
    assert res["ok"] is False
    assert res["code"] == "bad_field_by_combo"


# ------------------------------------------------------- traversal enrichment


async def test_corpus_traverse_chunks_carries_section_and_ord(
    tmp_path: Path,
) -> None:
    corpus = _make_corpus(tmp_path / "c")
    context.bind(corpus_path=corpus.root)
    srv = server.build_server()
    res = await _tool(srv, "corpus_traverse")(
        handle="doc:paper_0", to="chunks",
    )
    assert res["ok"] is True
    items = res["items"]
    assert all(it["type"] == "chunk" for it in items)
    assert all("section_path" in it["meta"] for it in items)
    assert [it["meta"]["ord"] for it in items] == sorted(
        it["meta"]["ord"] for it in items
    )


# -------------------------------------------------- context_show health summary


async def test_context_show_includes_health_when_corpus_bound(
    tmp_path: Path,
) -> None:
    corpus = _make_corpus(tmp_path / "c")
    context.bind(corpus_path=corpus.root)
    srv = server.build_server()
    res = await _tool(srv, "context_show")()
    snap = res["items"][0]
    assert snap["corpus_bound"] is True
    assert "health" in snap
    assert snap["health"]["n_docs"] == 2
    assert snap["health"]["n_chunks"] == 4


async def test_context_show_omits_health_when_unbound() -> None:
    srv = server.build_server()
    res = await _tool(srv, "context_show")()
    snap = res["items"][0]
    assert snap["corpus_bound"] is False
    assert "health" not in snap


# ----------------------------------------------------- corpus_image tool


def _seed_png(path: Path) -> bytes:
    """Write a minimal 1x1 PNG to *path* and return its bytes."""
    import struct
    import zlib

    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data)
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    idat = _chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
    iend = _chunk(b"IEND", b"")
    payload = sig + ihdr + idat + iend
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return payload


def _stub_get_figure(monkeypatch, fig_path_rel: str) -> None:
    """Bypass the KG load path: ``queries.get_figure`` returns a fixed dict."""
    def _fake(corpus, fig_id):
        return {
            "id": fig_id,
            "source_id": "paper_0",
            "caption": "stub caption",
            "page": 1,
            "path": fig_path_rel,
            "near_chunk_ids": [],
        }
    monkeypatch.setattr(queries, "get_figure", _fake)


async def test_corpus_image_returns_image_content(
    tmp_path: Path, monkeypatch
) -> None:
    """corpus_image returns an MCP ImageContent block, not bytes-as-text."""
    corpus = _make_corpus(tmp_path / "c")
    expected = _seed_png(corpus.root / "images" / "paper_0" / "fig_001.png")
    _stub_get_figure(monkeypatch, "images/paper_0/fig_001.png")
    context.bind(corpus_path=corpus.root)
    srv = server.build_server()

    result = await _tool(srv, "corpus_image")(handle="figure:paper_0/fig_001")
    # FastMCP returns the Image helper directly; the SDK converts it to
    # an ImageContent block at call_tool time. Test the helper output
    # since the unit test bypasses call_tool's content-block wrapping.
    from mcp.server.fastmcp import Image
    assert isinstance(result, Image)
    block = result.to_image_content()
    assert block.type == "image"
    assert block.mimeType == "image/png"
    import base64
    assert base64.b64decode(block.data) == expected


async def test_corpus_image_rejects_non_figure_handle(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    context.bind(corpus_path=corpus.root)
    srv = server.build_server()
    res = await _tool(srv, "corpus_image")(handle="doc:paper_0")
    assert isinstance(res, dict)
    assert res["ok"] is False
    assert res["code"] == "bad_handle_kind"


async def test_corpus_image_missing_file(
    tmp_path: Path, monkeypatch
) -> None:
    corpus = _make_corpus(tmp_path / "c")
    _stub_get_figure(monkeypatch, "images/paper_0/missing.png")
    context.bind(corpus_path=corpus.root)
    srv = server.build_server()
    res = await _tool(srv, "corpus_image")(handle="figure:paper_0/missing")
    assert res["ok"] is False
    assert res["code"] == "image_missing_on_disk"


def test_corpus_image_registered_in_tool_list() -> None:
    srv = server.build_server()
    names = {t.name for t in srv._tool_manager.list_tools()}
    assert "corpus_image" in names


async def test_figure_item_advertises_image_tool(
    tmp_path: Path, monkeypatch
) -> None:
    """corpus_show on a figure handle hints at the corpus_image tool."""
    corpus = _make_corpus(tmp_path / "c")
    _seed_png(corpus.root / "images" / "paper_0" / "fig_001.png")
    _stub_get_figure(monkeypatch, "images/paper_0/fig_001.png")
    context.bind(corpus_path=corpus.root)
    srv = server.build_server()
    res = await _tool(srv, "corpus_show")(handle="figure:paper_0/fig_001")
    item = res["items"][0]
    assert item["resource_uri"] == "wikify://corpus/figures/paper_0/fig_001"
    hint = item["meta"]["image_tool"]
    assert hint["name"] == "corpus_image"
    assert hint["args"]["handle"] == "figure:paper_0/fig_001"
