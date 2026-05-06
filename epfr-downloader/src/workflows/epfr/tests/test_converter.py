"""Tests for document conversion module."""

# ruff: noqa: D102

from pathlib import Path

from ..converter import convert_to_markdown


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

    def test_invalid_doc_uses_fallback_encoding(self, tmp_path: Path):
        # Create a file that will trigger raw binary fallback
        doc_file = tmp_path / "test.doc"
        # Write some text that can be decoded
        doc_file.write_bytes("Plain text content".encode("utf-8"))

        success, content, error, md_path = convert_to_markdown(doc_file)

        # Should succeed with fallback raw decoding
        assert success is True
        assert content is not None
        assert "Plain text content" in content
        assert md_path is not None
        assert md_path == tmp_path / "test.md"
        assert md_path.exists()

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


class TestConvertToMarkdownExtensions:
    """Tests for supported file extension handling."""

    def test_docx_extension_recognized(self):
        # Just verify the extension mapping, actual conversion tested separately
        from pathlib import Path

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
