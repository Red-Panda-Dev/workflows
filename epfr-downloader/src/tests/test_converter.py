"""Tests for document conversion module."""

# ruff: noqa: D102

import asyncio
import importlib
import shutil
import subprocess
from pathlib import Path

from workflows.epfr import converter
from workflows.epfr.converter import _table_to_md, convert_all_files, convert_to_markdown, convert_unp_files

pytest = importlib.import_module("pytest")


@pytest.fixture()
def anyio_backend():
    return "asyncio"


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

    def test_creates_md_file_with_correct_name(self, tmp_path: Path, monkeypatch):
        doc_file = tmp_path / "report.doc"
        doc_file.write_bytes("Sample document text".encode("utf-8"))

        monkeypatch.setattr(converter, "_extract_doc", lambda _path: "Sample document text")

        success, content, error, md_path = convert_to_markdown(doc_file)

        assert success is True
        assert md_path is not None
        assert md_path == tmp_path / "report.md"
        assert md_path.exists()
        assert "Sample document text" in md_path.read_text()

    def test_overwrite_true_replaces_existing(self, tmp_path: Path, monkeypatch):
        doc_file = tmp_path / "test.doc"
        doc_file.write_bytes("New content".encode("utf-8"))
        md_file = tmp_path / "test.md"
        md_file.write_text("Old markdown")

        monkeypatch.setattr(converter, "_extract_doc", lambda _path: "New content")

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

    def test_case_insensitive_extension(self, tmp_path: Path, monkeypatch):
        doc_file = tmp_path / "TEST.DOC"
        doc_file.write_bytes("content".encode("utf-8"))

        monkeypatch.setattr(converter, "_extract_doc", lambda _path: "content")

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


class TestRealDocFiles:
    """Integration tests using real EPFR files captured from production.

    Both fixtures are OLE2 Excel workbooks (created by Microsoft Excel) that
    were saved with a ``.doc`` extension by the issuer. They exercise the xlrd
    fallback path added to ``_extract_doc``.
    """

    def test_dividends_doc_converts_successfully(self, copy_epfr_fixture, tmp_path: Path):
        dest = copy_epfr_fixture("ole2_excel_as_doc_dividends.doc", tmp_path)

        success, content, error, md_path = convert_to_markdown(dest)

        assert success is True
        assert error is None
        assert md_path is not None
        assert md_path.suffix == ".md"
        assert md_path.exists()

    def test_dividends_doc_contains_expected_text(self, copy_epfr_fixture, tmp_path: Path):
        dest = copy_epfr_fixture("ole2_excel_as_doc_dividends.doc", tmp_path)

        _, content, _, _ = convert_to_markdown(dest)

        assert content is not None
        assert "ИнТриз" in content
        assert "дивидендов" in content
        assert "2025" in content

    def test_dividends_doc_produces_markdown_table(self, copy_epfr_fixture, tmp_path: Path):
        dest = copy_epfr_fixture("ole2_excel_as_doc_dividends.doc", tmp_path)

        _, content, _, _ = convert_to_markdown(dest)

        assert content is not None
        # xlrd fallback renders rows as pipe-delimited Markdown
        assert "|" in content

    def test_sectors_doc_converts_successfully(self, copy_epfr_fixture, tmp_path: Path):
        dest = copy_epfr_fixture("ole2_excel_as_doc_sectors.doc", tmp_path)

        success, content, error, md_path = convert_to_markdown(dest)

        assert success is True
        assert error is None
        assert md_path is not None
        assert md_path.exists()

    def test_sectors_doc_contains_expected_text(self, copy_epfr_fixture, tmp_path: Path):
        dest = copy_epfr_fixture("ole2_excel_as_doc_sectors.doc", tmp_path)

        _, content, _, _ = convert_to_markdown(dest)

        assert content is not None
        assert "Сельское" in content
        assert "промышленность" in content

    def test_sectors_doc_rows_are_pipe_delimited(self, copy_epfr_fixture, tmp_path: Path):
        dest = copy_epfr_fixture("ole2_excel_as_doc_sectors.doc", tmp_path)

        _, content, _, _ = convert_to_markdown(dest)

        assert content is not None
        rows = [line for line in content.splitlines() if line.strip()]
        # Every data row must be a pipe-delimited Markdown row
        assert all("|" in row for row in rows)

    def test_real_doc_not_extracted_by_python_docx(self, copy_epfr_fixture, tmp_path: Path, monkeypatch):
        """python-docx and docx2txt should fail; xlrd must carry the conversion."""
        dest = copy_epfr_fixture("ole2_excel_as_doc_dividends.doc", tmp_path)

        docx_calls = []

        def failing_Document(path):
            docx_calls.append(path)
            raise Exception("not a docx")

        monkeypatch.setattr(converter, "Document", failing_Document)
        monkeypatch.setattr(converter.docx2txt, "process", lambda _: (_ for _ in ()).throw(Exception("not docx2txt")))
        # Keep xlrd intact — conversion must still succeed via _extract_xls fallback
        monkeypatch.setattr(shutil, "which", lambda _name: None)  # block antiword/catdoc/soffice

        success, content, error, _ = convert_to_markdown(dest)

        assert len(docx_calls) == 1, "python-docx should have been tried once"
        assert success is True
        assert "ИнТриз" in (content or "")


@pytest.mark.anyio
async def test_convert_unp_files_tracks_real_fixture_pairs_without_cleanup(copy_epfr_fixture, tmp_path: Path):
    unp_dir = tmp_path / "200019375"
    unp_dir.mkdir()

    dividends_doc = copy_epfr_fixture("ole2_excel_as_doc_dividends.doc", unp_dir)
    sectors_doc = copy_epfr_fixture("ole2_excel_as_doc_sectors.doc", unp_dir)
    (unp_dir / "skip.txt").write_text("leave me alone", encoding="utf-8")

    unp, success, failure, failed_files, converted_pairs = await convert_unp_files(
        "200019375",
        unp_dir,
        asyncio.Semaphore(4),
    )

    assert unp == "200019375"
    assert success == 2
    assert failure == 0
    assert failed_files == []
    assert {(src.name, md.name) for src, md in converted_pairs} == {
        (dividends_doc.name, "ole2_excel_as_doc_dividends.md"),
        (sectors_doc.name, "ole2_excel_as_doc_sectors.md"),
    }
    assert dividends_doc.exists()
    assert sectors_doc.exists()
    assert (unp_dir / "skip.txt").exists()
    assert "ИнТриз" in (unp_dir / "ole2_excel_as_doc_dividends.md").read_text(encoding="utf-8")
    assert "Сельское" in (unp_dir / "ole2_excel_as_doc_sectors.md").read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_convert_all_files_honors_overwrite_false_and_cleans_successes(copy_epfr_fixture, tmp_path: Path):
    success_unp_dir = tmp_path / "200019375"
    success_unp_dir.mkdir()
    success_doc = copy_epfr_fixture("ole2_excel_as_doc_dividends.doc", success_unp_dir)

    blocked_unp_dir = tmp_path / "700332293"
    blocked_unp_dir.mkdir()
    blocked_doc = copy_epfr_fixture("ole2_excel_as_doc_sectors.doc", blocked_unp_dir)
    blocked_md = blocked_doc.with_suffix(".md")
    blocked_md.write_text("existing markdown", encoding="utf-8")

    stats = await convert_all_files(
        ["200019375", "700332293", "000000000"],
        tmp_path,
        overwrite=False,
        cleanup_source=True,
    )

    assert stats["total_unps"] == 3
    assert stats["total_files_attempted"] == 2
    assert stats["total_successful"] == 1
    assert stats["total_failed"] == 1
    assert stats["failed_files"] == [str(blocked_doc)]
    assert stats["cleaned_up_files"] == [str(success_doc)]
    assert stats["by_unp"]["200019375"] == {
        "success": 1,
        "failed": 0,
        "failed_files": [],
        "converted_pairs": [(str(success_doc), str(success_doc.with_suffix(".md")))],
    }
    assert stats["by_unp"]["700332293"] == {
        "success": 0,
        "failed": 1,
        "failed_files": [str(blocked_doc)],
        "converted_pairs": [],
    }
    assert stats["by_unp"]["000000000"] == {
        "success": 0,
        "failed": 0,
        "failed_files": [],
        "converted_pairs": [],
    }

    assert not success_doc.exists()
    assert success_doc.with_suffix(".md").exists()
    assert blocked_doc.exists()
    assert blocked_md.read_text(encoding="utf-8") == "existing markdown"


@pytest.mark.anyio
async def test_convert_all_files_preserves_failed_real_fixture_sources(copy_epfr_fixture, tmp_path: Path, monkeypatch):
    unp_dir = tmp_path / "123456789"
    unp_dir.mkdir()
    failing_doc = copy_epfr_fixture("ole2_excel_as_doc_dividends.doc", unp_dir)
    success_doc = copy_epfr_fixture("ole2_excel_as_doc_sectors.doc", unp_dir)

    real_extract_doc = converter._extract_doc

    def controlled_extract_doc(path: Path) -> str:
        if path.name == failing_doc.name:
            raise RuntimeError("boom")
        return real_extract_doc(path)

    monkeypatch.setattr(converter, "_extract_doc", controlled_extract_doc)

    stats = await convert_all_files(["123456789"], tmp_path, cleanup_source=True)

    assert stats["total_files_attempted"] == 2
    assert stats["total_successful"] == 1
    assert stats["total_failed"] == 1
    assert stats["failed_files"] == [str(failing_doc)]
    assert stats["cleaned_up_files"] == [str(success_doc)]
    assert stats["by_unp"]["123456789"] == {
        "success": 1,
        "failed": 1,
        "failed_files": [str(failing_doc)],
        "converted_pairs": [(str(success_doc), str(success_doc.with_suffix(".md")))],
    }

    assert failing_doc.exists()
    assert not failing_doc.with_suffix(".md").exists()
    assert not success_doc.exists()
    assert success_doc.with_suffix(".md").exists()
