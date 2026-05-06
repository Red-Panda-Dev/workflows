"""Tests for archive extraction module."""

# ruff: noqa: D102

import zipfile
from pathlib import Path

from ..extractor import (
    ARCHIVE_EXTENSIONS,
    _detect_ooxml_type,
    extract_archive,
    is_archive,
)


class TestIsArchive:
    """Tests for is_archive function."""

    def test_zip_extension(self):
        assert is_archive("document.zip") is True

    def test_tar_extension(self):
        assert is_archive("archive.tar") is True

    def test_gz_extension(self):
        assert is_archive("data.gz") is True

    def test_tgz_extension(self):
        assert is_archive("backup.tgz") is True

    def test_tar_gz_extension(self):
        assert is_archive("archive.tar.gz") is True

    def test_uppercase_zip(self):
        assert is_archive("ARCHIVE.ZIP") is True

    def test_pdf_not_archive(self):
        assert is_archive("document.pdf") is False

    def test_docx_not_archive(self):
        assert is_archive("report.docx") is False

    def test_txt_not_archive(self):
        assert is_archive("readme.txt") is False

    def test_path_with_directory(self):
        assert is_archive("/path/to/file.zip") is True


class TestDetectOoxmlType:
    """Tests for _detect_ooxml_type function."""

    def test_detects_docx(self, tmp_path: Path):
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("[Content_Types].xml", "content types")
            zf.writestr("word/document.xml", "document content")

        assert _detect_ooxml_type(zip_path) == ".docx"

    def test_detects_xlsx(self, tmp_path: Path):
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("[Content_Types].xml", "content types")
            zf.writestr("xl/workbook.xml", "workbook content")

        assert _detect_ooxml_type(zip_path) == ".xlsx"

    def test_detects_pptx(self, tmp_path: Path):
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("[Content_Types].xml", "content types")
            zf.writestr("ppt/presentation.xml", "presentation content")

        assert _detect_ooxml_type(zip_path) == ".pptx"

    def test_regular_zip_not_ooxml(self, tmp_path: Path):
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("file1.txt", "content1")
            zf.writestr("file2.txt", "content2")

        assert _detect_ooxml_type(zip_path) is None

    def test_zip_with_content_types_but_no_doc(self, tmp_path: Path):
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("[Content_Types].xml", "content types")
            zf.writestr("random/file.xml", "random content")

        assert _detect_ooxml_type(zip_path) is None

    def test_invalid_zip_file(self, tmp_path: Path):
        not_zip = tmp_path / "fake.zip"
        not_zip.write_text("not a zip file")

        assert _detect_ooxml_type(not_zip) is None


class TestExtractArchive:
    """Tests for extract_archive function."""

    def test_extract_zip_preserves_filenames(self, tmp_path: Path):
        zip_path = tmp_path / "archive.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("Document.txt", "content1")
            zf.writestr("Report.PDF", "content2")

        success, error, count, files = extract_archive(zip_path)

        assert success is True
        assert error is None
        assert count == 2
        assert set(files) == {"Document.txt", "Report.PDF"}
        assert not zip_path.exists()  # Archive deleted
        assert (tmp_path / "Document.txt").exists()
        assert (tmp_path / "Report.PDF").exists()

    def test_extract_renames_ooxml_instead_of_extracting(self, tmp_path: Path):
        zip_path = tmp_path / "document.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("[Content_Types].xml", "content types")
            zf.writestr("word/document.xml", "document content")
            zf.writestr("word/styles.xml", "styles")

        success, error, count, files = extract_archive(zip_path)

        assert success is True
        assert error is None
        assert count == 1
        assert files == ["document.docx"]
        assert not zip_path.exists()  # Original zip deleted
        assert (tmp_path / "document.docx").exists()

    def test_extract_nested_files(self, tmp_path: Path):
        zip_path = tmp_path / "nested.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("folder/nested_file.txt", "nested content")
            zf.writestr("root_file.txt", "root content")

        success, error, count, files = extract_archive(zip_path)

        assert success is True
        assert count == 2
        assert "root_file.txt" in files or "folder" in [f.split("/")[0] for f in files]

    def test_extract_overwrites_existing(self, tmp_path: Path):
        existing = tmp_path / "file.txt"
        existing.write_text("old content")

        zip_path = tmp_path / "archive.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("file.txt", "new content")

        success, error, count, files = extract_archive(zip_path)

        assert success is True
        assert existing.read_text() == "new content"

    def test_extract_invalid_zip_fails(self, tmp_path: Path):
        bad_zip = tmp_path / "bad.zip"
        bad_zip.write_text("not a zip")

        success, error, count, files = extract_archive(bad_zip)

        assert success is False
        assert error is not None
        assert "BadZipFile" in error or "Error" in error
        assert count == 0
        assert files == []
        assert bad_zip.exists()  # Failed extraction keeps archive


class TestArchiveExtensions:
    """Tests for ARCHIVE_EXTENSIONS constant."""

    def test_contains_zip(self):
        assert ".zip" in ARCHIVE_EXTENSIONS

    def test_contains_tar(self):
        assert ".tar" in ARCHIVE_EXTENSIONS

    def test_contains_gz(self):
        assert ".gz" in ARCHIVE_EXTENSIONS

    def test_contains_tgz(self):
        assert ".tgz" in ARCHIVE_EXTENSIONS

    def test_contains_tar_gz(self):
        assert ".tar.gz" in ARCHIVE_EXTENSIONS
