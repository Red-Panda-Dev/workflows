"""File type detection from magic bytes.

Examines the first bytes of binary data to determine the file extension,
since the EPFR API returns raw file content without filenames.
"""

UNKNOWN_EXTENSION = ".bin"

SIGNATURES: list[tuple[bytes, str]] = [
    (b"\x25\x50\x44\x46", ".pdf"),
    (b"\x50\x4b\x03\x04", ".zip"),
    (b"\x50\x4b\x05\x06", ".zip"),
    (b"\x1f\x8b", ".gz"),
    (b"\x89\x50\x4e\x47", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"\xd0\xcf\x11\xe0", ".doc"),
    (b"\x25\x21\x50\x53", ".ps"),
    (b"\x49\x44\x33", ".mp3"),
    (b"\x66\x74\x79\x70", ".mp4"),
]


def detect_file_extension(data: bytes) -> str:
    """Detect file extension from leading magic bytes.

    For ZIP-based formats (DOCX, XLSX, ODT, etc.) the generic .zip extension
    is returned since the content is valid regardless — the extractor handles
    the inner format.

    Args:
        data: Leading bytes of a file (first chunk is sufficient).

    Returns:
        File extension string starting with '.', e.g. '.pdf'.
        Returns '.bin' if no known signature matches.
    """
    if not data:
        return UNKNOWN_EXTENSION

    for signature, ext in SIGNATURES:
        if data.startswith(signature):
            return ext

    return UNKNOWN_EXTENSION


def build_filename(record_id: int, data: bytes) -> str:
    """Build a filename from record ID and detected extension.

    Args:
        record_id: EPFR record ID (used as the stem).
        data: Leading bytes of the file content.

    Returns:
        Filename string, e.g. '141278.pdf'.
    """
    ext = detect_file_extension(data)
    return f"{record_id}{ext}"
