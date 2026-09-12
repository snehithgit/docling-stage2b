"""Shared document selection for quality analysis and verifier reads."""
import json
import zipfile


def select_docling_document(archive: zipfile.ZipFile) -> tuple[dict, str]:
    candidates = []
    for name in archive.namelist():
        if not name.lower().endswith('.json') or name.endswith('/'):
            continue
        try:
            document = json.loads(archive.read(name))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(document, dict) and isinstance(document.get('texts'), list):
            candidates.append((document, name))
    if not candidates:
        raise ValueError('ZIP contains no Docling JSON document')
    if len(candidates) > 1:
        raise ValueError('ZIP contains multiple Docling documents; export one document per ZIP')
    return candidates[0]
