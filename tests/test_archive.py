import io
import json
import zipfile

import pytest

from app.archive import select_docling_document


def test_select_docling_document_path_traversal():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('../../../etc/passwd.json', json.dumps({"texts": []}).encode('utf-8'))
        zf.writestr('/etc/shadow.json', json.dumps({"texts": []}).encode('utf-8'))
        zf.writestr('C:/Windows/System32/cmd.exe.json', json.dumps({"texts": []}).encode('utf-8'))

    buf.seek(0)

    with zipfile.ZipFile(buf, 'r') as zf:
        with pytest.raises(ValueError, match="ZIP contains no Docling JSON document"):
            select_docling_document(zf)


def test_select_docling_document_valid():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('document.json', json.dumps({"texts": []}).encode('utf-8'))

    buf.seek(0)

    with zipfile.ZipFile(buf, 'r') as zf:
        doc, name = select_docling_document(zf)
        assert name == 'document.json'
        assert isinstance(doc, dict)
