"""Tests for EPFR OCR conversion and mapping updates (PDF, PNG, JPG, JPEG)."""

# ruff: noqa: D102

from dataclasses import replace
import json
from pathlib import Path

import pytest

from workflows.epfr import pdf_ocr
from workflows.epfr.config import EPFR_DEFAULTS


class _FakePage:
    def __init__(self, markdown: str):
        self.markdown = markdown


class _FakeOcrResult:
    def __init__(self, pages: list[_FakePage]):
        self.pages = pages


@pytest.mark.anyio
async def test_ocr_file_to_markdown_pdf_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pdf_path = tmp_path / "a.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")

    async def _fake_ocr(_request):
        return _FakeOcrResult([_FakePage("hello"), _FakePage("world")])

    monkeypatch.setattr(pdf_ocr, "mistralai_ocr", _fake_ocr)

    success, md_path, err = await pdf_ocr.ocr_file_to_markdown(pdf_path)

    assert success is True
    assert err is None
    assert md_path is not None
    assert md_path.exists()
    assert md_path.read_text(encoding="utf-8") == "hello\n\nworld"


@pytest.mark.anyio
async def test_ocr_file_to_markdown_png_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    png_path = tmp_path / "image.png"
    # Write a minimal valid PNG file
    png_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    async def _fake_ocr(_request):
        return _FakeOcrResult([_FakePage("image content")])

    monkeypatch.setattr(pdf_ocr, "mistralai_ocr", _fake_ocr)

    success, md_path, err = await pdf_ocr.ocr_file_to_markdown(png_path)

    assert success is True
    assert err is None
    assert md_path is not None
    assert md_path.exists()
    assert md_path.read_text(encoding="utf-8") == "image content"


@pytest.mark.anyio
async def test_ocr_file_to_markdown_jpg_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    jpg_path = tmp_path / "photo.jpg"
    # Write a minimal valid JPG file
    jpg_path.write_bytes(b"\xff\xd8\xff")

    async def _fake_ocr(_request):
        return _FakeOcrResult([_FakePage("jpg content")])

    monkeypatch.setattr(pdf_ocr, "mistralai_ocr", _fake_ocr)

    success, md_path, err = await pdf_ocr.ocr_file_to_markdown(jpg_path)

    assert success is True
    assert err is None
    assert md_path is not None
    assert md_path.exists()
    assert md_path.read_text(encoding="utf-8") == "jpg content"


@pytest.mark.anyio
async def test_ocr_file_to_markdown_jpeg_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    jpeg_path = tmp_path / "photo.jpeg"
    # Write a minimal valid JPEG file
    jpeg_path.write_bytes(b"\xff\xd8\xff")

    async def _fake_ocr(_request):
        return _FakeOcrResult([_FakePage("jpeg content")])

    monkeypatch.setattr(pdf_ocr, "mistralai_ocr", _fake_ocr)

    success, md_path, err = await pdf_ocr.ocr_file_to_markdown(jpeg_path)

    assert success is True
    assert err is None
    assert md_path is not None
    assert md_path.exists()
    assert md_path.read_text(encoding="utf-8") == "jpeg content"


@pytest.mark.anyio
async def test_ocr_file_to_markdown_removes_image_placeholders(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pdf_path = tmp_path / "images.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")

    async def _fake_ocr(_request):
        return _FakeOcrResult([_FakePage("Title\n\n![img-0.jpeg](img-0.jpeg)\n\nBody")])

    monkeypatch.setattr(pdf_ocr, "mistralai_ocr", _fake_ocr)

    success, md_path, err = await pdf_ocr.ocr_file_to_markdown(pdf_path)

    assert success is True
    assert err is None
    assert md_path is not None
    assert md_path.read_text(encoding="utf-8") == "Title\n\nBody"


@pytest.mark.anyio
async def test_ocr_file_to_markdown_missing_file(tmp_path: Path):
    success, md_path, err = await pdf_ocr.ocr_file_to_markdown(tmp_path / "missing.pdf")
    assert success is False
    assert md_path is None
    assert err == "FILE_NOT_FOUND"


@pytest.mark.anyio
async def test_ocr_file_to_markdown_too_large(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pdf_path = tmp_path / "large.pdf"
    pdf_path.write_bytes(b"%PDF")

    monkeypatch.setattr(pdf_ocr, "load_epfr_config", lambda: replace(EPFR_DEFAULTS, max_pdf_size_bytes=1))

    success, md_path, err = await pdf_ocr.ocr_file_to_markdown(pdf_path)
    assert success is False
    assert md_path is None
    assert err is not None
    assert err.startswith("FILE_TOO_LARGE")


@pytest.mark.anyio
async def test_ocr_mapping_files_updates_and_cleanup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
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

    stats = await pdf_ocr.ocr_mapping_files(
        output_root=out,
        mapping_filename="unp_file_mapping.json",
        overwrite=True,
        cleanup_source=True,
    )

    updated = json.loads(mapping_path.read_text(encoding="utf-8"))
    entry = updated[unp]["files"][0]

    assert stats["total_ocr_entries"] == 1
    assert stats["total_successful"] == 1
    assert entry["filename"] == "1.md"
    assert entry["converted_from"] == "1.pdf"
    assert entry["extracted_from"] == "1.zip"
    assert not pdf_path.exists()
    assert (folder / "1.md").exists()


@pytest.mark.anyio
async def test_ocr_mapping_files_unp_filter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
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

    stats = await pdf_ocr.ocr_mapping_files(
        output_root=out,
        mapping_filename="unp_file_mapping.json",
        overwrite=True,
        cleanup_source=False,
        unps=["222"],
    )

    updated = json.loads(mapping_path.read_text(encoding="utf-8"))
    assert stats["total_ocr_entries"] == 1
    assert updated["111"]["files"][0]["filename"] == "a.pdf"
    assert updated["222"]["files"][0]["filename"] == "b.md"


@pytest.mark.anyio
async def test_ocr_mapping_files_skips_non_ocr_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    out = tmp_path / "output"
    unp = "123"
    folder = out / unp
    folder.mkdir(parents=True)

    # Create both OCR-able and non-OCR-able files
    (folder / "1.pdf").write_bytes(b"%PDF-1.4")
    (folder / "2.png").write_bytes(b"\x89PNG")
    (folder / "3.docx").write_bytes(b"docx content")

    mapping_path = out / "unp_file_mapping.json"
    mapping = {
        unp: {
            "title": "Company",
            "holder_id": 1,
            "files": [
                {"id": 1, "filename": "1.pdf", "original_name": "pdf"},
                {"id": 2, "filename": "2.png", "original_name": "png"},
                {"id": 3, "filename": "3.docx", "original_name": "docx"},
            ],
        }
    }
    mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")

    async def _fake_ocr(_request):
        return _FakeOcrResult([_FakePage("content")])

    monkeypatch.setattr(pdf_ocr, "mistralai_ocr", _fake_ocr)

    stats = await pdf_ocr.ocr_mapping_files(
        output_root=out,
        mapping_filename="unp_file_mapping.json",
        overwrite=True,
        cleanup_source=False,
    )

    updated = json.loads(mapping_path.read_text(encoding="utf-8"))
    # Only PDF and PNG should be processed (2 OCR-able files)
    assert stats["total_ocr_entries"] == 2
    assert updated[unp]["files"][0]["filename"] == "1.md"  # PDF converted
    assert updated[unp]["files"][1]["filename"] == "2.md"  # PNG converted
    assert updated[unp]["files"][2]["filename"] == "3.docx"  # DOCX unchanged


@pytest.mark.anyio
async def test_ocr_file_to_markdown_unsupported_extension(tmp_path: Path):
    """Test that unsupported file extensions are rejected."""
    docx_path = tmp_path / "document.docx"
    docx_path.write_bytes(b"docx content")

    success, md_path, err = await pdf_ocr.ocr_file_to_markdown(docx_path)

    assert success is False
    assert md_path is None
    assert err == "UNSUPPORTED_EXTENSION(.docx)"
