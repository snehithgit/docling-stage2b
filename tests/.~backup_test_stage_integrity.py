import asyncio
import json
import zipfile
from types import SimpleNamespace

import pytest

from app.postprocess import inspect_docling_zip
from app.stage2b import Stage2BWorker, _load_docling_document_fast
from app.stage2c import correction_fidelity


@pytest.mark.parametrize('original,proposed', [
    ('Set pressure to 24 bar before starting the pump.', 'Set pressure before starting the pump.'),
    ('K1 uses 24 V and K2 uses 12 V.', 'K1 uses 12 V and K2 uses 24 V.'),
    ('Check K1 then check K1 again.', 'Check K1 then check again.'),
])
def test_fidelity_preserves_technical_sequence(original, proposed):
    result = correction_fidelity(original, proposed)
    assert not result['accepted']
    assert 'TECHNICAL_TOKENS_NOT_PRESERVED' in result['reasons']


def test_fidelity_allows_spacing_cleanup_with_values():
    assert correction_fidelity('Pump mo tor uses 24 V.', 'Pump motor uses 24V.')['accepted']


def test_archive_ignores_larger_unrelated_json(tmp_path):
    path = tmp_path / 'book.zip'
    document = {'texts': [{'text': 'manual'}]}
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('metadata.json', json.dumps({'padding': 'x' * 10000}))
        archive.writestr('broken.json', '{')
        archive.writestr('book.json', json.dumps(document))
    assert inspect_docling_zip(path)[:2] == (document, 'book.json')
    assert _load_docling_document_fast(path) == document


def test_archive_rejects_multiple_documents_explicitly(tmp_path):
    path = tmp_path / 'books.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        for name in ('a.json', 'b.json'):
            archive.writestr(name, json.dumps({'texts': []}))
    for reader in (inspect_docling_zip, _load_docling_document_fast):
        with pytest.raises(ValueError, match='multiple Docling documents'):
            reader(path)


def test_old_verifier_cannot_write_into_new_analysis(tmp_path):
    async def run():
        class PostStore:
            async def get_job(self, job_id):
                return {'status': 'completed', 'result_dir': 'book__run1'}

        current = tmp_path / 'book__run1'
        current.mkdir()
        ledger = current / 'correction_ledger.json'
        ledger.write_text('{"entries": []}')
        worker = Stage2BWorker(
            lambda: SimpleNamespace(stage2c_enabled=True, processed_dir=str(tmp_path)),
            None, PostStore(), SimpleNamespace(notify=lambda *_: None),
        )
        await worker._record_stage2c_entry(
            'pi5', {'postprocess_job_id': 1, 'result_dir': 'book__run0'},
            {}, {}, 'LIKELY_CORRUPT', None,
        )
        assert ledger.read_text() == '{"entries": []}'
        assert not (tmp_path / 'book__run0').exists()
    asyncio.run(run())


def test_route_discovery_includes_books_beyond_first_thousand(tmp_path):
    async def run():
        class PostStore:
            async def list_jobs(self, limit=100):
                rows = [dict(id=i, conversion_job_id=i, status='completed',
                             result_dir='book', output_filename='book.zip') for i in range(1001)]
                return rows if limit < 0 else rows[:limit]

        class VerificationStore:
            async def sync_routes(self, *args):
                return 1

        result = tmp_path / 'book'
        result.mkdir()
        (result / 'routes.json').write_text('{"routes": []}')
        worker = Stage2BWorker(
            lambda: SimpleNamespace(processed_dir=str(tmp_path)),
            VerificationStore(), PostStore(), SimpleNamespace(notify=lambda *_: None),
        )
        assert await worker.sync_routes_once() == 1001
        assert await worker.sync_routes_once() == 0
    asyncio.run(run())
