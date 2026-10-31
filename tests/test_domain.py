import sqlite3
from copy import deepcopy

import pytest

from cite_audit.assessment import assess, brief_bib
from cite_audit.knowledge import Knowledge, field_normalize, normalize
from cite_audit.sources import BERT_TITLE, MOCK_BERT_RECORDS
from cite_audit.storage import Store


@pytest.fixture
def kb(tmp_path):
    return Knowledge(Store(tmp_path / 'knowledge.sqlite3'))


def formal(provider='alpha', **fields):
    r = deepcopy(MOCK_BERT_RECORDS[1])
    r.update(provider=provider, record_id='publication', raw_payload=fields or {'initial': provider})
    r['fields'].update(fields)
    return r


def test_same_version_complement_conflict_and_evidence(kb):
    a = formal(); a['fields'].pop('pages')
    b = formal('beta', title=BERT_TITLE.upper())
    sources = kb.ingest([a, b], 'mock')
    assert sources[0]['version_id'] == sources[1]['version_id']
    v = kb.version(sources[0]['version_id'])
    assert v['fields']['pages'] == '4171--4186'
    assert v['field_selections']['pages']['evidence'][0]['source_content_id'] == sources[1]['id']
    c = formal('gamma', pages='1--23')
    kb.ingest([c], 'mock')
    v = kb.version(v['id'])
    assert 'pages' not in v['fields']
    assert v['field_selections']['pages']['status'] == 'conflicting'
    assert len(kb.store.all('SELECT id FROM versions')) == 1
    assert kb.article(v['article_id'])['recommended_version_id'] is None


def test_updated_source_snapshot_reuses_version_without_self_conflict(kb):
    a = formal(pages='1--9'); old = kb.ingest([a], 'mock')[0]
    a['fields']['pages'] = '1--10'; a['raw_payload'] = {'revision': 2}
    new = kb.ingest([a], 'mock')[0]
    assert old['snapshot_id'] != new['snapshot_id']
    assert old['version_id'] == new['version_id']
    assert kb.version(new['version_id'])['fields']['pages'] == '1--10'
    assert kb.source(old['id'])['fields']['pages'] == '1--9'
    with pytest.raises(sqlite3.IntegrityError):
        kb.store.execute('UPDATE snapshots SET content=? WHERE id=?', (b'changed', old['snapshot_id']))


def test_preprint_formal_relation_and_current_version_semantics(kb):
    sources = kb.ingest(deepcopy(MOCK_BERT_RECORDS), 'mock')
    assert len(kb.store.all('SELECT id FROM articles')) == 1
    assert len(kb.store.all('SELECT id FROM versions')) == 2
    entry = {'fields': deepcopy(MOCK_BERT_RECORDS[0]['fields']), 'entry_type': 'article', 'citation_key': 'bert'}
    result = assess(kb, entry, sources, 'demo', [])
    assert result['identity_state'] == 'confirmed'
    assert result['recommendation_status'] == 'upgrade_available'
    assert not result['has_conflict']
    assert result['current_version']['fields']['year'] == '2018'
    assert result['recommended_version']['fields']['year'] == '2019'
    assert 'eprint' not in result['recommended_version']['fields']
    assert any(p['proposal_type'] == 'whole_version_switch' for p in result['proposals'])


def test_mock_never_enters_online(kb):
    assert kb.ingest(deepcopy(MOCK_BERT_RECORDS), 'online') == []
    assert kb.store.all('SELECT id FROM articles') == []


def test_wrong_doi_does_not_replace_input(kb):
    sources = kb.ingest([formal()], 'mock')
    result = assess(kb, {'fields': {'title': 'A completely different paper', 'doi': '10.18653/v1/n19-1423'},
                         'entry_type': 'article', 'citation_key': 'wrong'}, sources, 'demo', [])
    assert result['identity_state'] != 'confirmed'
    assert result['proposals'] == []
    assert next(a for a in result['field_assessments'] if a['field'] == 'doi')['judgment'] == 'conflict'


def test_similar_title_no_relationship_never_links(kb):
    a, b = formal(), formal('beta', doi='10.1111/extension', journal='Extended Research')
    b['identifiers'] = {'doi': '10.1111/extension'}
    kb.ingest([a,b], 'mock')
    assert len(kb.store.all('SELECT id FROM articles')) == 2


@pytest.mark.parametrize('field,left,right', [('doi','10.1/a-b','10.1/ab'),('pages','1--23','12--3'),('url','https://a/x-y','https://a/xy')])
def test_meaningful_punctuation_is_not_erased(field,left,right):
    assert field_normalize(field,left) != field_normalize(field,right)


def test_title_language_symbols_are_not_erased():
    assert normalize('C++ study') != normalize('C study')


def test_unsafe_copyable_proposal_is_empty():
    assert brief_bib('k', {'entrytype':'article','title':r'bad}\n\input{secret}'}) == ''


def test_arxiv_base_input_matches_specific_revision_but_other_revision_does_not(kb):
    record = deepcopy(MOCK_BERT_RECORDS[0])
    record['identifiers']['arxiv']='1810.04805v2'
    record['fields']['eprint']='1810.04805v2'
    sources = kb.ingest([record], 'mock')
    entry={'fields':{'eprint':'1810.04805','title':BERT_TITLE},'entry_type':'article','citation_key':'bert'}
    assert assess(kb,entry,sources,'demo',[])['identity_state']=='confirmed'
    entry['fields']['eprint']='1810.04805v1'
    assert assess(kb,entry,sources,'demo',[])['identity_state']!='confirmed'


def test_disjoint_official_ids_require_complete_publication_tuple(kb):
    a,b=formal(),formal('beta')
    a['identifiers']={'acl':'N19-1423'};a['fields'].pop('doi')
    b['identifiers']={'pmlr':'v1/example'};b['fields'].pop('doi')
    sources=kb.ingest([a,b],'mock')
    assert sources[0]['version_id']==sources[1]['version_id']


def test_conflicting_official_id_with_shared_doi_is_unresolved(kb):
    a,b=formal(),formal('beta')
    b['identifiers']['acl']='different-workshop-paper'
    sources=kb.ingest([a,b],'mock')
    assert sources[1]['version_id'] is None
    assert len(kb.store.all('SELECT id FROM versions'))==1
