"""Tests for EPFR PDF OCR conversion and mapping updates."""

# ruff: noqa: D102

import json
from pathlib import Path

import pytest

from .. import pdf_ocr


class _FakePage:
    def __init__(self, markdown: str):
        self.markdown = markdown


class _FakeOcrResult:
    def __init__(self, pages: list[_FakePage]):
        self.pages = pages


@pytest.mark.anyio
async def test_ocr_pdf_to_markdown_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pdf_path = tmp_path / "a.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")

    async def _fake_ocr(_request):
        return _FakeOcrResult([_FakePage("hello"), _FakePage("world")])

    monkeypatch.setattr(pdf_ocr, "mistralai_ocr", _fake_ocr)

    success, md_path, err = await pdf_ocr.ocr_pdf_to_markdown(pdf_path)

    assert success is True
    assert err is None
    assert md_path is not None
    assert md_path.exists()
    assert md_path.read_text(encoding="utf-8") == "hello\n\nworld"


@pytest.mark.anyio
async def test_ocr_pdf_to_markdown_missing_file(tmp_path: Path):
    success, md_path, err = await pdf_ocr.ocr_pdf_to_markdown(tmp_path / "missing.pdf")
    assert success is False
    assert md_path is None
    assert err == "PDF_NOT_FOUND"


@pytest.mark.anyio
async def test_ocr_pdf_to_markdown_too_large(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pdf_path = tmp_path / "large.pdf"
    pdf_path.write_bytes(b"%PDF")

    monkeypatch.setattr(pdf_ocr, "MAX_PDF_SIZE_BYTES", 1)

    success, md_path, err = await pdf_ocr.ocr_pdf_to_markdown(pdf_path)
    assert success is False
    assert md_path is None
    assert err is not None
    assert err.startswith("PDF_TOO_LARGE")


@pytest.mark.anyio
async def test_mapping_updates_and_cleanup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    out = tmp_path / "output"
    unp = "123"
    folder = out / unp
    folder.mkdir(parents=True)
    pdf_path = folder / "1.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 data")

    mapping_path = out / "unp_file_mapping.json"
    mapping = {
        unp: {
            "title": "Company",
            "holder_id": 1,
            "files": [
                {
                    "id": 1,
                    "filename": "1.pdf",
                    "original_name": "doc",
                    "upload_date": "2026-01-01",
                    "extracted_from": "1.zip",
                    "converted_from": None,
                }
            ],
        }
    }
    mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")

    async def _fake_ocr(_request):
        return _FakeOcrResult([_FakePage("markdown")])

    monkeypatch.setattr(pdf_ocr, "mistralai_ocr", _fake_ocr)

    stats = await pdf_ocr.ocr_mapping_pdfs(
        output_root=out,
        mapping_filename="unp_file_mapping.json",
        overwrite=True,
        cleanup_source=True,
    )

    updated = json.loads(mapping_path.read_text(encoding="utf-8"))
    entry = updated[unp]["files"][0]

    assert stats["total_pdf_entries"] == 1
    assert stats["total_successful"] == 1
    assert entry["filename"] == "1.md"
    assert entry["converted_from"] == "1.pdf"
    assert entry["extracted_from"] == "1.zip"
    assert not pdf_path.exists()
    assert (folder / "1.md").exists()


@pytest.mark.anyio
async def test_mapping_unp_filter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    out = tmp_path / "output"
    (out / "111").mkdir(parents=True)
    (out / "222").mkdir(parents=True)
    (out / "111" / "a.pdf").write_bytes(b"%PDF-1.4")
    (out / "222" / "b.pdf").write_bytes(b"%PDF-1.4")

    mapping_path = out / "unp_file_mapping.json"
    mapping = {
        "111": {"title": "A", "holder_id": 1, "files": [{"id": 1, "filename": "a.pdf", "original_name": "a"}]},
        "222": {"title": "B", "holder_id": 2, "files": [{"id": 2, "filename": "b.pdf", "original_name": "b"}]},
    }
    mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")

    async def _fake_ocr(_request):
        return _FakeOcrResult([_FakePage("ok")])

    monkeypatch.setattr(pdf_ocr, "mistralai_ocr", _fake_ocr)

    stats = await pdf_ocr.ocr_mapping_pdfs(
        output_root=out,
        mapping_filename="unp_file_mapping.json",
        overwrite=True,
        cleanup_source=False,
        unps=["222"],
    )

    updated = json.loads(mapping_path.read_text(encoding="utf-8"))
    assert stats["total_pdf_entries"] == 1
    assert updated["111"]["files"][0]["filename"] == "a.pdf"
    assert updated["222"]["files"][0]["filename"] == "b.md"
