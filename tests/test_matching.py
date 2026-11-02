from copy import deepcopy

import pytest

from cite_audit.assessment import assess, current_match
from cite_audit.knowledge import Knowledge, identity_compatible
from cite_audit.matching import author_match, name_match, relevance, venue_normalize
from cite_audit.sources import MOCK_BERT_RECORDS
from cite_audit.storage import Store


@pytest.mark.parametrize('left,right', [
    ('Yanyi Liu', 'Liu, Yanyi'), ('Liu Yanyi', 'Yanyi Liu'),
    ('Liu, Yan-yi', 'Yan Yi Liu'), ('Yan Yi Liu', 'Liu Yanyi'),
    ('Ou-Yang, Ming', 'Ming Ouyang'), ('Jacob Devlin', 'Devlin, Jacob'),
])
def test_full_name_variants(left, right):
    assert name_match(left, right) == name_match(right, left) == 'full'


@pytest.mark.parametrize('left,right', [('Y. Liu', 'Yanyi Liu'), ('Liu, Y.', 'Liu, Yanyi'),
                                      ('Liu, Y. Y.', 'Liu, Yan Yi'), ('Y. Liu', 'Y Liu')])
def test_initials_are_not_full_identity(left, right):
    assert name_match(left, right) == 'initials'


def test_names_do_not_conflate_shared_surnames_or_reorder_authors():
    assert name_match('Liu, Yanyi', 'Liu, Yingyou') == 'different'
    assert name_match('Wang, Chen', 'Chen, Wang') == 'different'
    assert author_match('Yanyi Liu and Chen Wang', 'Wang, Chen and Liu, Yanyi') == 'different'
    assert author_match('Yanyi Liu and others', 'Liu, Yanyi') == 'partial'
    assert author_match('Yanyi Liu', '') == 'missing'


def test_entire_registry_roster_with_reversed_family_given_is_corroborated_not_exported(tmp_path):
    left = 'Wang, Chen and Liu, Yanyi and Guo, Tiezheng'
    right = 'Chen, Wang and Yan-yi, Liu and Tie-zheng, Guo'
    assert author_match(left, right) == 'order_variant'
    assert author_match('Wang, Chen', 'Chen, Wang') == 'different'
    assert author_match(left, 'Chen, Wang and Yan-yi, Liu and Tiezheng, Li') == 'different'
    kb = Knowledge(Store(tmp_path / 'roster.sqlite3'))
    record = deepcopy(MOCK_BERT_RECORDS[1])
    record['fields']['author'] = right
    sources = kb.ingest([record], 'mock')
    fields = {**record['fields'], 'author': left}
    result = assess(kb, {'fields': fields, 'entry_type': 'inproceedings', 'citation_key': 'x'}, sources, 'demo', [])
    assert result['identity_state'] == 'confirmed'
    assert next(a for a in result['field_assessments'] if a['field'] == 'author')['judgment'] == 'unverifiable'
    assert all('author' not in p['fields'] for p in result['proposals'])


def test_venue_ampersand_and_html_equivalence():
    assert venue_normalize(r'IET Control Theory \&; Applications') == venue_normalize('IET Control Theory and Applications')
    assert venue_normalize('Complex &amp; Intelligent Systems') == venue_normalize('Complex & Intelligent Systems')


def test_name_variants_require_publication_corroboration():
    fields = {'title': 'A precise title', 'author': 'Liu Yan Yi and Chen Wang', 'year': '2025', 'journal': 'A Journal'}
    version = {'fields': {**fields, 'author': 'Liu, Yanyi and Wang, Chen'}, 'identifiers': {'doi': '10.1234/example'}}
    assert current_match(fields, version)[0]
    assert not current_match({**fields, 'author': 'Y. Liu and C. Wang'}, version)[0]
    assert not current_match({**fields, 'year': '2024'}, version)[0]
    assert not current_match({**fields, 'journal': 'Other Journal'}, version)[0]
    assert not identity_compatible(fields, {**version['fields'], 'author': 'Liu, Yingyou'})


def test_noise_is_excluded_without_creating_ambiguity(tmp_path):
    kb = Knowledge(Store(tmp_path / 'test.sqlite3'))
    sources = kb.ingest(deepcopy(MOCK_BERT_RECORDS), 'mock')
    entry = {'fields': {'title': 'Image segmentation for cellular microscopy', 'author': 'Liu, Yanyi'},
             'entry_type': 'article', 'citation_key': 'cell'}
    result = assess(kb, entry, sources, 'demo', [])
    assert result['identity_state'] == 'not_found'
    assert result['candidates'] == [] and result['excluded_candidates']
    assert all(c['snapshot_id'] and c['status'] == 'rejected' for c in result['excluded_candidates'])
    assert not result['proposals']
    failed = assess(kb, entry, sources, 'demo', [{'status': 'failed'}])
    assert failed['identity_state'] == 'source_failed'
    limited = assess(kb, entry, sources, 'demo', [{'status': 'partial', 'error_code': 'coverage_unavailable'}])
    assert limited['identity_state'] == 'not_found'


def test_related_but_insufficient_is_not_ambiguous(tmp_path):
    kb = Knowledge(Store(tmp_path / 'test.sqlite3'))
    source = deepcopy(MOCK_BERT_RECORDS[1])
    sources = kb.ingest([source], 'mock')
    entry = {'fields': {'title': source['fields']['title']}, 'entry_type': 'article', 'citation_key': 'x'}
    result = assess(kb, entry, sources, 'demo', [])
    assert result['identity_state'] == 'insufficient'
    assert '完整有序作者' in result['candidates'][0]['reason']


def test_two_real_competitors_remain_ambiguous(tmp_path):
    kb = Knowledge(Store(tmp_path / 'test.sqlite3'))
    a, b = deepcopy(MOCK_BERT_RECORDS[1]), deepcopy(MOCK_BERT_RECORDS[1])
    b.update(record_id='other', identifiers={'doi': '10.1234/other'})
    b['fields']['doi'] = '10.1234/other'
    sources = kb.ingest([a, b], 'mock')
    fields = {k: v for k, v in a['fields'].items() if k not in {'doi', 'url'}}
    result = assess(kb, {'fields': fields, 'entry_type': 'inproceedings', 'citation_key': 'x'}, sources, 'demo', [])
    assert result['identity_state'] == 'ambiguous'
    assert len(result['candidates']) == 2 and not result['proposals']


def test_similar_title_is_only_a_candidate():
    result = relevance({'title': 'Image segmentation with neural networks', 'author': 'Liu, Yanyi'},
                       {'fields': {'title': 'Image segmentation with deep neural networks', 'author': 'Liu, Yanyi'}})
    assert result['status'] == 'unresolved'
