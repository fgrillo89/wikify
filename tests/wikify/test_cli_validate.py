"""Tests for wikify validate write."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wikify.cli import app

runner = CliRunner()


def _valid_body(marker: str = "[^e1]", quote: str = "ALD grows films one layer at a time") -> str:
    filler = (
        "Atomic layer deposition (ALD) is a self-limiting thin-film growth technique. "
        "It produces conformal films one atomic layer per cycle. "
    ) * 12
    return (
        f"**Atomic Layer Deposition** (ALD) is a self-limiting vapor-phase technique.{marker}\n\n"
        f"{filler}\n\n"
        "## Mechanism\n\n"
        f"{filler}\n\n"
        "## Applications\n\n"
        f"{filler}\n\n"
        "## References\n\n"
        f'{marker}: chunk_x__c0000__deadbeef (doc_x) > "{quote}"\n'
    )


def _write_pair(
    tmp_path: Path,
    *,
    page_id: str = "Atomic Layer Deposition",
    body: str | None = None,
    quote: str = "ALD grows films one layer at a time",
    response_page_id: str | None = None,
) -> tuple[Path, Path]:
    draft = {
        "page_id": page_id,
        "page_kind": "article",
        "title": page_id,
        "aliases": ["ALD"],
        "skeleton": "",
        "evidence": [],
        "evidence_v2": [
            {
                "chunk_id": "chunk_x__c0000__deadbeef",
                "doc_id": "doc_x",
                "quote": quote,
            }
        ],
        "prompt_template": "wikify/write",
        "model_id": "claude-sonnet",
        "tier": "M",
    }
    response = {
        "page_id": response_page_id or page_id,
        "page_kind": "article",
        "body_markdown": body or _valid_body(quote=quote),
        "used_markers": ["e1"],
        "tokens_in": 1000,
        "tokens_out": 500,
    }
    draft_path = tmp_path / f"draft-{page_id}.json"
    response_path = tmp_path / f"response-{page_id}.json"
    draft_path.write_text(json.dumps(draft), encoding="utf-8")
    response_path.write_text(json.dumps(response), encoding="utf-8")
    return draft_path, response_path


def test_validate_write_passes_on_valid_pair(tmp_path: Path) -> None:
    draft, response = _write_pair(tmp_path)
    result = runner.invoke(
        app, ["validate", "write", "--draft", str(draft), "--response", str(response)]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    verdict_path = Path(payload["validation_path"])
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert verdict["ok"] is True
    assert verdict["errors"] == []
    assert verdict["schema_version"] == 1
    assert verdict["structural_checks"]["pydantic"] == "pass"
    assert verdict["structural_checks"]["quote_in_chunk"] == "pass"


def test_validate_write_fails_on_quote_not_in_body(tmp_path: Path) -> None:
    draft, response = _write_pair(
        tmp_path,
        body=_valid_body(quote="not the same quote at all"),
        quote="a totally different quote that is not in the body",
    )
    result = runner.invoke(
        app, ["validate", "write", "--draft", str(draft), "--response", str(response)]
    )
    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["errors"] >= 1


def test_validate_write_fails_on_page_id_mismatch(tmp_path: Path) -> None:
    draft, response = _write_pair(tmp_path, response_page_id="A Different Page")
    result = runner.invoke(
        app, ["validate", "write", "--draft", str(draft), "--response", str(response)]
    )
    assert result.exit_code != 0
    verdict = json.loads(
        Path(json.loads(result.output)["validation_path"]).read_text(encoding="utf-8")
    )
    assert any(e["code"] == "page_id_mismatch" for e in verdict["errors"])


def test_validate_write_fails_on_invalid_response_schema(tmp_path: Path) -> None:
    draft, response = _write_pair(tmp_path)
    bad = json.loads(response.read_text(encoding="utf-8"))
    bad["body_markdown"] = "too short, no references, no markers"
    response.write_text(json.dumps(bad), encoding="utf-8")
    result = runner.invoke(
        app, ["validate", "write", "--draft", str(draft), "--response", str(response)]
    )
    assert result.exit_code != 0
    verdict = json.loads(
        Path(json.loads(result.output)["validation_path"]).read_text(encoding="utf-8")
    )
    assert verdict["structural_checks"]["pydantic"] == "fail"
    assert any("response" in e["path"] for e in verdict["errors"])


def test_validate_write_writes_verdict_at_custom_out_path(tmp_path: Path) -> None:
    draft, response = _write_pair(tmp_path)
    custom = tmp_path / "custom-verdict.json"
    result = runner.invoke(
        app,
        [
            "validate",
            "write",
            "--draft",
            str(draft),
            "--response",
            str(response),
            "--out",
            str(custom),
        ],
    )
    assert result.exit_code == 0, result.output
    assert custom.exists()
