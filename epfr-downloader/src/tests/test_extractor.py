"""Tests for archive extraction module."""

# ruff: noqa: D102

import asyncio
import importlib
import io
from pathlib import Path
import tarfile
from typing import Literal
import zipfile

from workflows.epfr.extractor import (
    ARCHIVE_EXTENSIONS,
    _detect_ooxml_type,
    extract_all_archives,
    extract_archive,
    extract_unp_archives,
    is_archive,
)


pytest = importlib.import_module("pytest")


@pytest.fixture()
def anyio_backend():
    return "asyncio"


def _write_tar_archive(archive_path: Path, files: dict[str, bytes], mode: Literal["w", "w:gz"] = "w") -> None:
    with tarfile.open(archive_path, mode) as tf:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))


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


@pytest.mark.anyio
async def test_extract_unp_archives_tracks_stats_lineage_and_skips_non_archives(tmp_path: Path):
    unp_dir = tmp_path / "123456789"
    unp_dir.mkdir()

    with zipfile.ZipFile(unp_dir / "bundle.zip", "w") as zf:
        zf.writestr("zip-folder/notice.txt", "zip payload")

    _write_tar_archive(unp_dir / "bundle.tar", {"tar-report.txt": b"tar payload"})
    _write_tar_archive(unp_dir / "bundle.gz", {"gz-report.txt": b"gz payload"}, mode="w:gz")
    _write_tar_archive(unp_dir / "bundle.tgz", {"tgz-report.txt": b"tgz payload"}, mode="w:gz")

    skipped_file = unp_dir / "skip.pdf"
    skipped_file.write_bytes(b"%PDF-1.4")

    bad_archive = unp_dir / "broken.zip"
    bad_archive.write_text("not really a zip", encoding="utf-8")

    result = await extract_unp_archives("123456789", unp_dir, asyncio.Semaphore(4))

    assert result[0] == "123456789"
    assert result[1] == 4
    assert result[2] == 1
    assert result[3] == [str(bad_archive)]
    assert result[4] == 4
    assert set(result[5]) == {"notice.txt", "tar-report.txt", "gz-report.txt", "tgz-report.txt"}
    assert result[6] == {
        "bundle.zip": ["notice.txt"],
        "bundle.tar": ["tar-report.txt"],
        "bundle.gz": ["gz-report.txt"],
        "bundle.tgz": ["tgz-report.txt"],
    }

    assert (unp_dir / "notice.txt").read_text(encoding="utf-8") == "zip payload"
    assert (unp_dir / "tar-report.txt").read_text(encoding="utf-8") == "tar payload"
    assert (unp_dir / "gz-report.txt").read_text(encoding="utf-8") == "gz payload"
    assert (unp_dir / "tgz-report.txt").read_text(encoding="utf-8") == "tgz payload"

    assert not (unp_dir / "bundle.zip").exists()
    assert not (unp_dir / "bundle.tar").exists()
    assert not (unp_dir / "bundle.gz").exists()
    assert not (unp_dir / "bundle.tgz").exists()
    assert bad_archive.exists()
    assert skipped_file.exists()


@pytest.mark.anyio
async def test_extract_all_archives_aggregates_lineage_ooxml_and_empty_unps(tmp_path: Path):
    ooxml_unp = tmp_path / "111"
    ooxml_unp.mkdir()
    with zipfile.ZipFile(ooxml_unp / "statement.zip", "w") as zf:
        zf.writestr("[Content_Types].xml", "content types")
        zf.writestr("word/document.xml", "document content")

    empty_unp = tmp_path / "222"
    empty_unp.mkdir()
    (empty_unp / "notes.txt").write_text("not an archive", encoding="utf-8")

    stats = await extract_all_archives(["111", "222", "333"], tmp_path)

    assert stats == {
        "total_unps": 3,
        "total_archives": 1,
        "successful": 1,
        "failed": 0,
        "failed_archives": [],
        "files_extracted": 1,
        "by_unp": {
            "111": {
                "archives_extracted": 1,
                "archives_failed": 0,
                "failed_archives": [],
                "extracted_files": ["statement.docx"],
                "archive_to_files": {"statement.zip": ["statement.docx"]},
            },
            "222": {
                "archives_extracted": 0,
                "archives_failed": 0,
                "failed_archives": [],
                "extracted_files": [],
                "archive_to_files": {},
            },
            "333": {
                "archives_extracted": 0,
                "archives_failed": 0,
                "failed_archives": [],
                "extracted_files": [],
                "archive_to_files": {},
            },
        },
    }

    assert not (ooxml_unp / "statement.zip").exists()
    assert (ooxml_unp / "statement.docx").exists()
    assert (empty_unp / "notes.txt").exists()
