"""Actionable conversion diagnostics without modifying files or retry policy."""
from pathlib import Path


def conversion_diagnostic(job: dict) -> dict | None:
    if job.get('status') != 'failed':
        return None
    message = str(job.get('error_message') or '').lower()
    extension = Path(str(job.get('filename') or '')).suffix.lower()
    unsupported = any(term in message for term in (
        'unsupported file', 'unsupported format', 'unsupported input',
        'not a supported format', 'does not match any allowed format',
        'format is not supported', 'file type is not supported',
    ))
    dependency = any(term in message for term in ('libreoffice', 'soffice')) and any(
        term in message for term in ('not found', 'missing', 'not installed', 'required', 'no such file'))
    legacy_unknown = extension in {'.doc', '.xls', '.ppt'} and (
        'task failure without error details' in message)
    if not (unsupported or dependency or legacy_unknown):
        return None
    replacements = {'.doc': 'PDF or DOCX', '.xls': 'XLSX or CSV', '.ppt': 'PDF or PPTX'}
    return {
        'code': 'UNSUPPORTED_FILE' if unsupported else 'FILE_CONVERSION_REQUIRED',
        'title': 'Unsupported file' if unsupported else 'File format needs conversion',
        'confirmed': bool(unsupported or dependency),
        'explanation': (
            'The conversion service rejected this file format.' if unsupported else
            'The conversion service reports a missing document converter.' if dependency else
            'This legacy Office file failed without details. It may require a converter unavailable on the server.'
        ),
        'action': f"Convert the original file to {replacements.get(extension, 'PDF or another supported format')} on your computer, then upload the converted file through Add book. Renaming the extension does not convert the file.",
        'upload_url': '/add-book',
    }
