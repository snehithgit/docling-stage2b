from app.conversion_diagnostics import conversion_diagnostic


def test_confirmed_unsupported_format_has_upload_guidance():
    d = conversion_diagnostic({'status': 'failed', 'filename': 'a.xyz',
                               'error_message': 'Input does not match any allowed format'})
    assert d['code'] == 'UNSUPPORTED_FILE' and d['confirmed']
    assert 'Add book' in d['action']


def test_legacy_generic_failure_is_suggestion_not_confirmed_rejection():
    d = conversion_diagnostic({'status': 'failed', 'filename': 'a.DOC',
                               'error_message': 'Docling Serve reported a task failure without error details.'})
    assert not d['confirmed']
    assert 'PDF or DOCX' in d['action']


def test_corrupt_or_network_failures_are_not_relabelled_unsupported():
    for filename in ('a.pdf', 'a.doc'):
        assert conversion_diagnostic({'status': 'failed', 'filename': filename,
                                      'error_message': 'Unable to connect: timeout'}) is None
    assert conversion_diagnostic({'status': 'completed', 'filename': 'a.doc'}) is None


def test_missing_converter_excel_guidance():
    d = conversion_diagnostic({'status': 'failed', 'filename': 'a.xls',
                               'error_message': 'LibreOffice executable not found'})
    assert d['confirmed'] and 'XLSX or CSV' in d['action']
