"""Tests for file type detection from magic bytes."""

# ruff: noqa: D102

from uuid import UUID

from ..detector import UNKNOWN_EXTENSION, build_filename, detect_file_extension


class TestDetectFileExtension:
    """Tests for detect_file_extension function."""

    def test_pdf(self):
        assert detect_file_extension(b"\x25\x50\x44\x46") == ".pdf"

    def test_pdf_with_rest(self):
        data = b"\x25\x50\x44\x46\x2d\x31\x2e\x34"
        assert detect_file_extension(data) == ".pdf"

    def test_zip(self):
        assert detect_file_extension(b"\x50\x4b\x03\x04") == ".zip"

    def test_zip_empty_archive(self):
        assert detect_file_extension(b"\x50\x4b\x05\x06") == ".zip"

    def test_gzip(self):
        assert detect_file_extension(b"\x1f\x8b\x08\x00") == ".gz"

    def test_png(self):
        assert detect_file_extension(b"\x89\x50\x4e\x47\x0d\x0a") == ".png"

    def test_jpeg(self):
        assert detect_file_extension(b"\xff\xd8\xff\xe0") == ".jpg"

    def test_ole_doc(self):
        assert detect_file_extension(b"\xd0\xcf\x11\xe0\xa1\xb1") == ".doc"

    def test_ole_xls_when_excel_clsid_detected(self, monkeypatch):
        class Root:
            clsid = UUID("00020820-0000-0000-c000-000000000046")

        class FakeOleFile:
            root = Root()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        monkeypatch.setattr("workflows.epfr.detector.olefile.OleFileIO", lambda _stream: FakeOleFile())

        assert detect_file_extension(b"\xd0\xcf\x11\xe0\xa1\xb1") == ".xls"

    def test_unknown_bytes(self):
        assert detect_file_extension(b"\x00\x01\x02\x03") == UNKNOWN_EXTENSION

    def test_empty_data(self):
        assert detect_file_extension(b"") == UNKNOWN_EXTENSION

    def test_short_data(self):
        assert detect_file_extension(b"\x25") == UNKNOWN_EXTENSION

    def test_ps_signature(self):
        assert detect_file_extension(b"\x25\x21\x50\x53") == ".ps"

    def test_mp3_id3(self):
        assert detect_file_extension(b"\x49\x44\x33") == ".mp3"


class TestBuildFilename:
    """Tests for build_filename function."""

    def test_pdf_filename(self):
        assert build_filename(141278, b"\x25\x50\x44\x46") == "141278.pdf"

    def test_zip_filename(self):
        assert build_filename(999, b"\x50\x4b\x03\x04") == "999.zip"

    def test_unknown_filename(self):
        assert build_filename(42, b"\x00\x01") == f"42{UNKNOWN_EXTENSION}"

    def test_empty_data_filename(self):
        assert build_filename(1, b"") == f"1{UNKNOWN_EXTENSION}"
