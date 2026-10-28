"""Shared document selection for quality analysis and verifier reads."""
import json
import zipfile
import pathlib


def _is_safe_zip_member(name: str) -> bool:
    """Check if a zip member path is safe to use."""
    if not name:
        return False

    # Check for paths starting with / or \
    if name.startswith('/') or name.startswith('\\'):
        return False

    # Normalize path separators for checking
    normalized = name.replace('\\', '/')

    # Check for Windows-style drive letters in the original or normalized path
    # like C:, D: etc. We reject absolute paths on Windows
    if ':' in normalized:
        return False

    path = pathlib.PurePosixPath(normalized)

    if path.is_absolute() or path.drive:
        return False

    if '..' in path.parts:
        return False

    return True


def select_docling_document(archive: zipfile.ZipFile) -> tuple[dict, str]:
    candidates = []
    for name in archive.namelist():
        if not _is_safe_zip_member(name):
            continue
        if not name.lower().endswith('.json') or name.endswith('/'):
            continue
        try:
            document = json.loads(archive.read(name))
        except (ValueError, UnicodeDecodeError, KeyError):
            continue
        if isinstance(document, dict) and isinstance(document.get('texts'), list):
            candidates.append((document, name))
    if not candidates:
        raise ValueError('ZIP contains no Docling JSON document')
    if len(candidates) > 1:
        raise ValueError('ZIP contains multiple Docling documents; export one document per ZIP')
    return candidates[0]
