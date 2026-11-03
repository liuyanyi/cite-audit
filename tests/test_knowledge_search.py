from contextlib import contextmanager
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from cite_audit import presenters
from cite_audit.api import create_app
from cite_audit.sources import MOCK_BERT_RECORDS
from cite_audit.storage import dump


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, start_worker=False)) as client:
        knowledge = client.app.state.service.knowledge
        knowledge.ingest(deepcopy(MOCK_BERT_RECORDS), 'mock')
        for index, (title, author) in enumerate([
            ('中文检索研究', '王小明'), ('Straße 100%_ citations', 'Example, Ada'),
            ("Literal ' OR 1=1 -- title", 'Other, Bob'),
        ]):
            record = {
                'provider': 'synthetic', 'record_id': str(index), 'status': 'verified',
                'source_type': 'aggregator', 'source_url': f'https://example.org/{index}',
                'identifiers': {'doi': f'10.1234/search-{index}'}, 'identity_basis': 'doi',
                'fields': {'title': title, 'author': author, 'year': '2026', 'journal': 'Synthetic Journal'},
            }
            knowledge.ingest([record], 'online')
        yield client


def test_summaries_match_detail(client):
    knowledge = client.app.state.service.knowledge
    expected = {row['id']: presenters.article(knowledge.article(row['id']), detail=False)
                for row in knowledge.store.all('SELECT id FROM articles')}

    page = client.get('/api/articles').json()
    assert page['total'] == 4
    assert {item['id']: item for item in page['items']} == expected
    demo = next(item for item in page['items'] if item['mode'] == 'demo')
    assert demo['version_count'] == 2 and demo['source_count'] == len(MOCK_BERT_RECORDS)
    assert 'versions' not in demo and 'version_relations' not in demo


@pytest.mark.parametrize('query,mode,total', [
    ('BERT', '', 1), ('devlin', 'demo', 1), ('1810.04805', 'demo', 1),
    ('10.18653/V1/N19-1423', '', 1), ('bert', 'online', 0),
    ('王小明', 'online', 1), ('中文检索', '', 1), ('STRASSE', '', 1),
    ('100%_', '', 1), ('100%X', '', 0), ("' OR 1=1 --", '', 1),
    ('no such publication', '', 0), ('', 'unknown', 0), ('', 'online', 3),
])
def test_literal_unicode_search_and_mode_isolation(client, query, mode, total):
    response = client.get('/api/articles', params={'q': query, 'mode': mode})
    assert response.status_code == 200
    assert response.json()['total'] == total


def test_search_counts_distinct_articles_before_stable_paging(client):
    first = client.get('/api/articles', params={'q': 'bert', 'page_size': 1}).json()
    second = client.get('/api/articles', params={'q': 'bert', 'page_size': 1, 'page': 2}).json()
    assert first['total'] == second['total'] == 1
    assert len(first['items']) == 1 and second['items'] == []
    # Equal timestamps must not cause duplicates or missing rows across pages.
    client.app.state.service.store.execute("UPDATE articles SET created_at='2026-01-01'")
    pages = [client.get('/api/articles', params={'page_size': 1, 'page': i}).json() for i in range(1, 5)]
    assert all(page['total'] == 4 for page in pages)
    assert len({page['items'][0]['id'] for page in pages}) == 4
    clamped = client.get('/api/articles', params={'page': -1, 'page_size': 1000}).json()
    assert clamped['page'] == 1 and clamped['page_size'] == 100


def test_search_reads_are_bounded_at_larger_library_size(client, monkeypatch):
    store = client.app.state.service.store
    with store.connect() as db:
        db.executemany('INSERT INTO articles VALUES(?,?,?,?,?,?)', [
            (f'bulk-{i}', 'online', f'Other article {i}', None,
             dump({'reason': 'No recommendation', 'revision': 0}), '2025-01-01') for i in range(200)
        ])
    statements = []
    connect = store.connect

    @contextmanager
    def traced():
        with connect() as db:
            db.set_trace_callback(statements.append)
            yield db

    monkeypatch.setattr(store, 'connect', traced)
    for query, total in [('', 204), ('bert', 1), ('no matching article', 0)]:
        statements.clear()
        result = client.get('/api/articles', params={'q': query, 'page_size': 10}).json()
        assert result['total'] == total and len(result['items']) <= 10
        reads = [sql for sql in statements if sql.lstrip().upper().startswith('SELECT')]
        assert len(reads) <= 2
        assert all('snapshots' not in sql and 'settings' not in sql and 's.data' not in sql for sql in reads)
