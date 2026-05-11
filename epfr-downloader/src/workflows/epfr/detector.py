"""File type detection from magic bytes.

Examines the first bytes of binary data to determine the file extension,
since the EPFR API returns raw file content without filenames.
"""

from io import BytesIO

import olefile


UNKNOWN_EXTENSION = ".bin"
OLE2_SIGNATURE = b"\xd0\xcf\x11\xe0"

SIGNATURES: list[tuple[bytes, str]] = [
    (b"\x25\x50\x44\x46", ".pdf"),
    (b"\x50\x4b\x03\x04", ".zip"),
    (b"\x50\x4b\x05\x06", ".zip"),
    (b"\x1f\x8b", ".gz"),
    (b"\x89\x50\x4e\x47", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (OLE2_SIGNATURE, ".doc"),
    (b"\x25\x21\x50\x53", ".ps"),
    (b"\x49\x44\x33", ".mp3"),
    (b"\x66\x74\x79\x70", ".mp4"),
]


def _detect_ole2_extension(data: bytes) -> str:
    """Best-effort OLE2 subtype detection from in-memory bytes.

    Args:
        data: Leading bytes from the attachment payload.

    Returns:
        ``.xls`` for Excel OLE2 documents when identifiable, else ``.doc``.

    """
    try:
        with olefile.OleFileIO(BytesIO(data)) as ole:
            root_clsid = str(ole.root.clsid).lower()
    except Exception:
        return ".doc"

    if root_clsid.startswith("00020820"):
        return ".xls"

    return ".doc"


def detect_file_extension(data: bytes) -> str:
    """Detect the business file type from raw EPFR attachment bytes.

    EPFR downloads do not include reliable filenames, so the first response
    chunk determines the extension used for downstream extraction and mapping.

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
            if signature == OLE2_SIGNATURE:
                return _detect_ole2_extension(data)
            return ext

    return UNKNOWN_EXTENSION


def build_filename(record_id: int, data: bytes) -> str:
    """Build the deterministic local filename for an EPFR attachment.

    Uses the record ID as a stable stem and the detected extension so all later
    workflow stages can correlate files back to source records.

    Args:
        record_id: EPFR record ID (used as the stem).
        data: Leading bytes of the file content.

    Returns:
        Filename string, e.g. '141278.pdf'.

    """
    ext = detect_file_extension(data)
    return f"{record_id}{ext}"
