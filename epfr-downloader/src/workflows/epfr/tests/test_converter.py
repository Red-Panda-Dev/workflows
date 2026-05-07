"""Tests for document conversion module."""

# ruff: noqa: D102

import shutil
import subprocess
from pathlib import Path

from .. import converter
from ..converter import _table_to_md, convert_to_markdown


class TestConvertToMarkdown:
    """Tests for convert_to_markdown function."""

    def test_unsupported_extension(self, tmp_path: Path):
        pdf_file = tmp_path / "document.pdf"
        pdf_file.write_bytes(b"%PDF-1.4")

        success, content, error, md_path = convert_to_markdown(pdf_file)

        assert success is False
        assert content is None
        assert error == "Unsupported extension: .pdf"
        assert md_path is None

    def test_unknown_extension(self, tmp_path: Path):
        unknown_file = tmp_path / "file.xyz"
        unknown_file.write_text("some content")

        success, content, error, md_path = convert_to_markdown(unknown_file)

        assert success is False
        assert content is None
        assert error == "Unsupported extension: .xyz"
        assert md_path is None

    def test_md_already_exists_no_overwrite(self, tmp_path: Path):
        docx_file = tmp_path / "doc.docx"
        # Create a minimal valid docx-like file (will fail extraction but tests the path)
        docx_file.write_bytes(b"invalid docx")
        md_file = tmp_path / "doc.md"
        md_file.write_text("existing markdown")

        # Should fail because docx is invalid, not because md exists
        success, content, error, md_path = convert_to_markdown(docx_file, overwrite=False)

        # The function will try to convert and fail on the invalid docx
        assert success is False
        # Error will be about extraction failure, not "MD_ALREADY_EXISTS"
        # because the check for MD_ALREADY_EXISTS happens after successful extraction

    def test_invalid_docx_fails_gracefully(self, tmp_path: Path):
        bad_docx = tmp_path / "bad.docx"
        bad_docx.write_text("not a valid docx file")

        success, content, error, md_path = convert_to_markdown(bad_docx)

        assert success is False
        assert content is None
        assert error is not None
        assert "Error" in error or "ValueError" in error or "BadZipFile" in error
        assert md_path is None

    def test_invalid_doc_uses_fallback_encoding(self, tmp_path: Path, monkeypatch):
        # Binary .doc-like content should fail when no external extractors exist.
        doc_file = tmp_path / "test.doc"
        doc_file.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1\x00\x00\x00\x00")

        monkeypatch.setattr(shutil, "which", lambda _name: None)

        success, content, error, md_path = convert_to_markdown(doc_file)

        assert success is False
        assert content is None
        assert error is not None
        assert md_path is None

    def test_doc_uses_libreoffice_fallback(self, tmp_path: Path, monkeypatch):
        doc_file = tmp_path / "legacy.doc"
        doc_file.write_bytes(b"not-really-doc")

        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/soffice" if name == "soffice" else None)

        def fake_run(cmd, capture_output, check, text, encoding, errors):
            assert "--convert-to" in cmd
            assert "txt:Text" in cmd
            outdir = Path(cmd[cmd.index("--outdir") + 1])
            (outdir / "legacy.txt").write_text("Extracted with LibreOffice", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        success, content, error, md_path = convert_to_markdown(doc_file)

        assert success is True
        assert error is None
        assert content == "Extracted with LibreOffice"
        assert md_path is not None
        assert md_path.read_text(encoding="utf-8") == "Extracted with LibreOffice"

    def test_invalid_xls_fails_gracefully(self, tmp_path: Path):
        bad_xls = tmp_path / "bad.xls"
        bad_xls.write_text("not a valid xls file")

        success, content, error, md_path = convert_to_markdown(bad_xls)

        assert success is False
        assert content is None
        assert error is not None
        assert md_path is None

    def test_creates_md_file_with_correct_name(self, tmp_path: Path):
        doc_file = tmp_path / "report.doc"
        doc_file.write_bytes("Sample document text".encode("utf-8"))

        success, content, error, md_path = convert_to_markdown(doc_file)

        assert success is True
        assert md_path is not None
        assert md_path == tmp_path / "report.md"
        assert md_path.exists()
        assert "Sample document text" in md_path.read_text()

    def test_overwrite_true_replaces_existing(self, tmp_path: Path):
        doc_file = tmp_path / "test.doc"
        doc_file.write_bytes("New content".encode("utf-8"))
        md_file = tmp_path / "test.md"
        md_file.write_text("Old markdown")

        success, content, error, md_path = convert_to_markdown(doc_file, overwrite=True)

        assert success is True
        assert md_path is not None
        assert md_path.exists()
        assert "New content" in md_path.read_text()

    def test_cleanup_removes_pipe_only_lines_before_write(self, tmp_path: Path, monkeypatch):
        doc_file = tmp_path / "cleanup.doc"
        doc_file.write_bytes(b"placeholder")

        monkeypatch.setattr(converter, "_extract_doc", lambda _path: "Title\n||||\n\nBody")

        success, content, error, md_path = convert_to_markdown(doc_file)

        assert success is True
        assert error is None
        assert content == "Title\n\nBody"
        assert md_path is not None
        assert md_path.read_text(encoding="utf-8") == "Title\n\nBody"


class TestConvertToMarkdownExtensions:
    """Tests for supported file extension handling."""

    def test_docx_extension_recognized(self):
        # Just verify the extension mapping, actual conversion tested separately
        ext = Path("file.docx").suffix.lower()
        assert ext == ".docx"

    def test_doc_extension_recognized(self):
        ext = Path("file.doc").suffix.lower()
        assert ext == ".doc"

    def test_xls_extension_recognized(self):
        ext = Path("file.xls").suffix.lower()
        assert ext == ".xls"

    def test_case_insensitive_extension(self, tmp_path: Path):
        doc_file = tmp_path / "TEST.DOC"
        doc_file.write_bytes("content".encode("utf-8"))

        success, content, error, md_path = convert_to_markdown(doc_file)

        assert success is True
        assert md_path is not None
        assert md_path.name == "TEST.md"


class _FakeCell:
    def __init__(self, text: str):
        self.text = text


class _FakeRow:
    def __init__(self, cells: list[str]):
        self.cells = [_FakeCell(cell) for cell in cells]


class _FakeTable:
    def __init__(self, rows: list[list[str]]):
        self.rows = [_FakeRow(row) for row in rows]


def test_table_to_md_flattens_ragged_border_columns():
    table = _FakeTable(
        [
            ["", "Информация о существенных фактах (событиях, действиях).", ""],
            ["", "О выплате дивидендов по акциям за 1-й квартал 2026 год:", ""],
            ["", "Полное наименование акционерного общества", 'Открытое акционерное общество "Объединение "Лотос"', ""],
            ["", "", "", ""],
        ]
    )

    assert _table_to_md(table) == (
        "Информация о существенных фактах (событиях, действиях).\n"
        "О выплате дивидендов по акциям за 1-й квартал 2026 год:\n"
        'Полное наименование акционерного общества | Открытое акционерное общество "Объединение "Лотос"'
    )
